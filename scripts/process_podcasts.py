"""
process_podcasts.py
-------------------
Monitors multiple YouTube channels for new videos.
For each new video:
  1. Downloads audio as MP3 via yt-dlp
  2. Uploads MP3 to Cloudflare R2 (free egress, S3-compatible)
  3. Updates the channel's RSS feed XML (hosted on GitHub Pages)
  4. Records the video ID in processed.json to avoid re-processing

Designed to run daily via GitHub Actions.
"""

import os
import sys
import json

sys.stdout.reconfigure(encoding="utf-8")
import argparse
import re
import unicodedata
import time
import subprocess
import html
import requests
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from yt_dlp import YoutubeDL
from feedgen.feed import FeedGenerator
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

def slugify(title: str, max_len: int = 70) -> str:
    nfd = unicodedata.normalize("NFD", title)
    result = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    result = result.lower()
    result = re.sub(r"[^a-z0-9א-תװ-״]+", "-", result)
    result = result.strip("-")
    result = re.sub(r"-+", "-", result)
    return result[:max_len].rstrip("-")

def ep_page_url(ch_slug: str, entry: dict) -> str:
    slug = slugify(entry.get("title", "")) or entry.get("video_id", "episode")
    prefix = ch_slug + "-"
    if slug.startswith(prefix):
        slug = slug[len(prefix):] or entry.get("video_id", "episode")
    fname = f"{slug}-{entry['published'][:10]}.html"
    return f"https://thetorahpodcast.net/{ch_slug}/{fname}"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_KEY      = os.environ.get("YOUTUBE_API_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "")

if not API_KEY:
    print("ERROR: YOUTUBE_API_KEY environment variable is not set.")
    sys.exit(1)
if not GITHUB_TOKEN:
    print("ERROR: GITHUB_TOKEN environment variable is not set.")
    sys.exit(1)
if not GITHUB_REPO:
    print("ERROR: GITHUB_REPO environment variable is not set.")
    sys.exit(1)

_owner, _repo_name = GITHUB_REPO.split("/", 1)
BASE_URL = "https://thetorahpodcast.net/"

CHANNELS_FILE  = "channels.json"
PROCESSED_FILE = "processed.json"
FEEDS_DIR      = Path("feeds")
AUDIO_DIR      = Path("tmp_audio")

FEEDS_DIR.mkdir(exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Cloudflare R2 helpers
# ---------------------------------------------------------------------------

def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=Config(
            signature_version="s3v4",
            connect_timeout=30,
            read_timeout=300,
            retries={"max_attempts": 3},
        ),
        region_name="auto",
    )


def upload_audio_to_r2(mp3_path: Path, filename: str, retries: int = 3) -> str:
    """Upload mp3_path to R2 using multipart transfer. Returns the public URL."""
    from boto3.s3.transfer import TransferConfig
    bucket = os.environ["R2_BUCKET_NAME"]
    public_base = os.environ["R2_PUBLIC_URL"].rstrip("/")
    client = get_r2_client()
    transfer_cfg = TransferConfig(multipart_threshold=8 * 1024 * 1024, max_concurrency=4)

    for attempt in range(retries):
        try:
            client.upload_file(
                str(mp3_path), bucket, filename,
                ExtraArgs={"ContentType": "audio/mpeg"},
                Config=transfer_cfg,
            )
            return f"{public_base}/{filename}"
        except Exception as e:
            if attempt < retries - 1:
                wait = 15 * (attempt + 1)
                print(f"  R2 upload attempt {attempt + 1} failed: {e}. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise


def asset_exists_in_r2(filename: str, video_id: str = None) -> str | None:
    """Return the public URL if the file already exists in R2, else None."""
    bucket = os.environ["R2_BUCKET_NAME"]
    public_base = os.environ["R2_PUBLIC_URL"].rstrip("/")
    client = get_r2_client()

    try:
        client.head_object(Bucket=bucket, Key=filename)
        return f"{public_base}/{filename}"
    except ClientError:
        pass

    if video_id:
        try:
            resp = client.list_objects_v2(Bucket=bucket, Prefix=f"{video_id}_", MaxKeys=1)
            if resp.get("Contents"):
                key = resp["Contents"][0]["Key"]
                return f"{public_base}/{key}"
        except ClientError:
            pass

    return None

# ---------------------------------------------------------------------------
# YouTube API helpers
# ---------------------------------------------------------------------------

def _best_thumbnail_url(thumbnails: dict) -> str:
    return (
        thumbnails.get("maxres") or
        thumbnails.get("high")   or
        thumbnails.get("medium") or
        thumbnails.get("default") or
        {}
    ).get("url", "")


def _parse_iso8601_duration(value: str) -> int:
    if not value:
        return 0
    match = re.fullmatch(r"P(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)", value)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def get_upload_playlist_video_ids(channel_id: str, limit: int = 100) -> list[str]:
    """Fetch recent video IDs from the channel uploads playlist, newest first."""
    playlist_id = "UU" + channel_id[2:]
    page_token = None
    ids = []

    while len(ids) < limit:
        batch = min(50, limit - len(ids))
        url = (
            f"https://www.googleapis.com/youtube/v3/playlistItems"
            f"?key={API_KEY}&playlistId={playlist_id}"
            f"&part=snippet&maxResults={batch}"
        )
        if page_token:
            url += f"&pageToken={page_token}"

        r = requests.get(url, timeout=20)
        data = r.json()

        if "error" in data:
            raise Exception(f"YouTube API error: {data['error']['message']}")
        items = data.get("items", [])
        if not items:
            break

        for item in items:
            vid_id = ((item.get("snippet") or {}).get("resourceId") or {}).get("videoId")
            if vid_id:
                ids.append(vid_id)

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return ids


def discover_channel_tab_ids(channel_id: str, tabs: tuple[str, ...] = ("/videos", "/streams", "/shorts"), limit_per_tab: int | None = None) -> list[str]:
    """Discover IDs from channel tabs via yt-dlp, preserving newest-first order across tabs."""
    base_url = f"https://www.youtube.com/channel/{channel_id}"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "ignoreerrors": True,
    }
    if limit_per_tab:
        ydl_opts["playlistend"] = limit_per_tab

    cookies_file = os.environ.get("YOUTUBE_COOKIES_FILE")
    if cookies_file and Path(cookies_file).exists():
        ydl_opts["cookiefile"] = cookies_file

    seen = {}
    for tab in tabs:
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(base_url + tab, download=False)
            for entry in (info or {}).get("entries") or []:
                vid_id = (entry or {}).get("id")
                if vid_id:
                    seen.setdefault(vid_id, True)
        except Exception as e:
            print(f"  [yt-dlp] Could not scan {tab} for {channel_id}: {e}")
    return list(seen.keys())


def fetch_video_metadata(video_ids: list[str]) -> list[dict]:
    """Hydrate video IDs into full metadata via youtube/v3/videos, preserving input order."""
    if not video_ids:
        return []

    by_id = {}
    for chunk in _chunks(video_ids, 50):
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "key": API_KEY,
                "id": ",".join(chunk),
                "part": "snippet,contentDetails",
                "maxResults": 50,
            },
            timeout=20,
        )
        data = r.json()
        if "error" in data:
            raise Exception(f"YouTube API error: {data['error']['message']}")

        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            thumbs = snippet.get("thumbnails", {})
            vid_id = item.get("id")
            if not vid_id:
                continue
            by_id[vid_id] = {
                "id":          vid_id,
                "title":       html.unescape(snippet.get("title", "")),
                "description": html.unescape(snippet.get("description", "")),
                "published":   snippet.get("publishedAt", ""),
                "thumbnail":   _best_thumbnail_url(thumbs),
                "url":         f"https://www.youtube.com/watch?v={vid_id}",
                "duration":    _parse_iso8601_duration((item.get("contentDetails") or {}).get("duration", "")),
                "live_status": snippet.get("liveBroadcastContent", "none"),
            }

    ordered = []
    for vid_id in video_ids:
        video = by_id.get(vid_id)
        if not video:
            continue
        if video["live_status"] in ("live", "upcoming"):
            print(f"  Skipping live/upcoming video: {vid_id} ({video['title'][:50]})")
            continue
        ordered.append(video)
    return ordered

def get_channel_info(channel_id: str) -> dict:
    """
    Fetch the YouTube channel's name, description, and best available thumbnail.
    Returns a dict with keys: title, description, thumbnail.
    """
    url = (
        f"https://www.googleapis.com/youtube/v3/channels"
        f"?key={API_KEY}&id={channel_id}&part=snippet"
    )
    r = requests.get(url)
    data = r.json()

    if "error" in data:
        raise Exception(f"YouTube API error fetching channel info: {data['error']['message']}")

    items = data.get("items", [])
    if not items:
        raise Exception(f"No channel found for ID: {channel_id}")

    snippet    = items[0]["snippet"]
    thumbnails = snippet.get("thumbnails", {})
    thumb_url  = _best_thumbnail_url(thumbnails)

    return {
        "title":       html.unescape(snippet["title"]),
        "description": html.unescape(snippet.get("description", "")),
        "thumbnail":   thumb_url,
    }


def get_new_videos(channel_id: str, already_processed: set) -> list[dict]:
    """
    Fetch recent uploads plus explicit /shorts and /streams tab discoveries.
    This avoids missing Shorts that were omitted by older bootstrap/backfill logic
    or that don't surface reliably via the uploads playlist alone.
    """
    merged_ids = []
    seen = set()
    for vid_id in get_upload_playlist_video_ids(channel_id, limit=100):
        if vid_id not in seen:
            merged_ids.append(vid_id)
            seen.add(vid_id)
    for vid_id in discover_channel_tab_ids(channel_id, tabs=("/shorts", "/streams"), limit_per_tab=30):
        if vid_id not in seen:
            merged_ids.append(vid_id)
            seen.add(vid_id)

    candidate_ids = [vid_id for vid_id in merged_ids if vid_id not in already_processed]
    videos = fetch_video_metadata(candidate_ids)
    videos.sort(key=lambda v: v.get("published", ""), reverse=True)
    return videos


# ---------------------------------------------------------------------------
# Audio download — Piped-first, cobalt second, yt-dlp fallback
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Invidious downloader
# Invidious proxies YouTube streams through its own servers.
# With local=true, stream URLs go through the Invidious instance,
# so GitHub Actions downloads from Invidious (non-Azure IP), not YouTube CDN.
# ---------------------------------------------------------------------------

INVIDIOUS_FALLBACK_INSTANCES = [
    "https://yewtu.be",
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://invidious.privacyredirect.com",
    "https://iv.ggtyler.dev",
    "https://invidious.perennialte.ch",
    "https://invidious.flossboxin.org.in",
    "https://invidious.protokolla.fi",
]


def get_invidious_instances() -> list[str]:
    """Fetch healthy Invidious instances that have the API enabled."""
    try:
        resp = requests.get(
            "https://api.invidious.io/instances.json",
            params={"sort_by": "health"},
            headers={"User-Agent": "TheThoraPodcast/1.0"},
            timeout=10,
        )
        resp.raise_for_status()
        instances = resp.json()
        # Format: [[name, {uri, api, type, health, ...}], ...]
        uris = [
            data["uri"].rstrip("/")
            for _, data in instances
            if data.get("api") and data.get("type") == "https"
        ]
        return uris[:8] if uris else INVIDIOUS_FALLBACK_INSTANCES
    except Exception as e:
        print(f"  [invidious] Could not fetch instance list: {e} — using fallbacks")
        return INVIDIOUS_FALLBACK_INSTANCES


def _download_stream_and_convert(audio_url: str, video_id: str, out_dir: Path, tag: str) -> Path:
    """Download an audio stream URL and convert to MP3 via ffmpeg."""
    mime_hint = "webm"  # default; ffmpeg handles both webm/opus and m4a/aac
    ext = "webm"
    tmp_path = out_dir / f"{video_id}.{ext}"
    mp3_path = out_dir / f"{video_id}.mp3"

    with requests.get(
        audio_url, stream=True, timeout=300,
        headers={"User-Agent": "TheThoraPodcast/1.0"}
    ) as r:
        r.raise_for_status()
        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

    if tmp_path.stat().st_size < 10_000:
        tmp_path.unlink()
        raise RuntimeError("Downloaded file too small — blocked or rate-limited")

    result = subprocess.run(
        ["ffmpeg", "-i", str(tmp_path), "-vn", "-ar", "44100", "-ac", "2",
         "-b:a", "128k", str(mp3_path), "-y"],
        capture_output=True, text=True,
    )
    tmp_path.unlink()
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-300:]}")

    print(f"  [{tag}] Downloaded and converted to MP3")
    return mp3_path


def download_via_invidious(video_url: str, video_id: str, out_dir: Path) -> Path:
    """Try downloading audio via Invidious API (proxied streams). Returns MP3 path."""
    instances = get_invidious_instances()
    for base in instances:
        try:
            resp = requests.get(
                f"{base}/api/v1/videos/{video_id}",
                params={"local": "true"},
                headers={"User-Agent": "TheThoraPodcast/1.0"},
                timeout=15,
            )
            if resp.status_code != 200:
                print(f"  [invidious] {base} → HTTP {resp.status_code}")
                continue
            data = resp.json()
            if "error" in data:
                print(f"  [invidious] {base} → error: {data['error']}")
                continue

            adaptive = data.get("adaptiveFormats", [])
            audio_formats = [f for f in adaptive if f.get("type", "").startswith("audio/")]
            if not audio_formats:
                print(f"  [invidious] {base} → no audio formats")
                continue

            best = sorted(audio_formats, key=lambda x: x.get("bitrate", 0), reverse=True)[0]
            audio_url = best["url"]

            print(f"  [invidious] Trying stream from {base} ...")
            result = _download_stream_and_convert(audio_url, video_id, out_dir, f"invidious/{base}")
            return result

        except Exception as e:
            print(f"  [invidious] {base} failed: {e}")
            for p in [out_dir / f"{video_id}.webm", out_dir / f"{video_id}.m4a",
                      out_dir / f"{video_id}.mp3"]:
                if p.exists():
                    p.unlink()
            continue

    raise RuntimeError("All Invidious instances failed")


# ---------------------------------------------------------------------------
# Piped downloader (fallback)
# ---------------------------------------------------------------------------

PIPED_FALLBACK_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://pipedapi.tokhmi.xyz",
    "https://pipedapi.moomoo.me",
    "https://piped-api.codeberg.page",
]


def get_piped_instances() -> list[str]:
    """Fetch active Piped instances from the official instance list."""
    try:
        resp = requests.get(
            "https://instances.piped.video/api/v1/instances",
            headers={"User-Agent": "TheThoraPodcast/1.0"},
            timeout=10,
        )
        resp.raise_for_status()
        instances = resp.json()
        # Format: [{api_url, frontend_url, ...}]
        urls = [i["api_url"].rstrip("/") for i in instances if i.get("api_url")]
        return urls[:6] if urls else PIPED_FALLBACK_INSTANCES
    except Exception as e:
        print(f"  [piped] Could not fetch instance list: {e} — using fallbacks")
        return PIPED_FALLBACK_INSTANCES


def download_via_piped(video_url: str, video_id: str, out_dir: Path) -> Path:
    """Try downloading audio via Piped API (proxied streams). Returns MP3 path."""
    instances = get_piped_instances()
    for base in instances:
        try:
            resp = requests.get(
                f"{base}/streams/{video_id}",
                headers={"User-Agent": "TheThoraPodcast/1.0"},
                timeout=15,
            )
            if resp.status_code != 200:
                print(f"  [piped] {base} → HTTP {resp.status_code}")
                continue
            data = resp.json()
            if "error" in data:
                print(f"  [piped] {base} → error: {data['error']}")
                continue
            audio_streams = data.get("audioStreams", [])
            if not audio_streams:
                print(f"  [piped] {base} → no audio streams")
                continue

            sorted_streams = sorted(audio_streams, key=lambda x: x.get("bitrate", 0), reverse=True)
            # Prefer proxied URLs (not direct googlevideo.com CDN)
            chosen = next(
                (s for s in sorted_streams if "googlevideo.com" not in s.get("url", "")),
                sorted_streams[0],
            )
            audio_url = chosen["url"]
            print(f"  [piped] Trying stream from {base} ...")
            return _download_stream_and_convert(audio_url, video_id, out_dir, f"piped/{base}")

        except Exception as e:
            print(f"  [piped] {base} failed: {e}")
            for p in [out_dir / f"{video_id}.webm", out_dir / f"{video_id}.m4a",
                      out_dir / f"{video_id}.mp3"]:
                if p.exists():
                    p.unlink()
            continue

    raise RuntimeError("All Piped instances failed")


COBALT_INSTANCES_API = "https://instances.cobalt.best/api"
# Community instances that don't require auth — wider list for resilience
COBALT_FALLBACK_INSTANCES = [
    "https://api.dl.woof.monster",
    "https://cobaltapi.squair.xyz",
    "https://api.cobalt.blackcat.sweeux.org",
    "https://api.cobalt.liubquanti.click",
    "https://cobaltapi.cjs.nz",
    "https://api.qwkuns.me",
]


def get_cobalt_instances() -> list[str]:
    """Fetch top public cobalt instances with YouTube support, sorted by score."""
    try:
        resp = requests.get(
            COBALT_INSTANCES_API,
            headers={"Accept": "application/json", "User-Agent": "TheThoraPodcast/1.0"},
            timeout=10,
        )
        resp.raise_for_status()
        text = resp.text.strip()
        if not text:
            raise ValueError("Empty response from instances API")
        instances = resp.json()
        filtered = [
            i for i in instances
            if i.get("online")
            and i.get("services", {}).get("youtube")
            and not i.get("info", {}).get("auth")
            and i.get("api")
            and i.get("protocol")
        ]
        filtered.sort(key=lambda x: x.get("score", 0), reverse=True)
        urls = [f"{i['protocol']}://{i['api']}" for i in filtered[:5]]
        return urls if urls else COBALT_FALLBACK_INSTANCES
    except Exception as e:
        print(f"  [cobalt] Could not fetch instance list: {e} — using fallbacks")
        return COBALT_FALLBACK_INSTANCES


def download_via_cobalt(video_url: str, video_id: str, out_dir: Path) -> Path:
    """Try downloading audio via cobalt public instances. Returns MP3 path."""
    instances = get_cobalt_instances()
    if not instances:
        raise RuntimeError("No cobalt instances available")

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://cobalt.tools",
        "User-Agent": "Mozilla/5.0 (compatible; TheThoraPodcast/1.0)",
    }
    api_key = os.environ.get("COBALT_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Api-Key {api_key}"

    for base_url in instances:
        try:
            resp = requests.post(
                f"{base_url}/",
                json={"url": video_url, "downloadMode": "audio",
                      "audioFormat": "mp3", "audioBitrate": "128"},
                headers=headers,
                timeout=30,
            )
            if resp.status_code != 200:
                print(f"  [cobalt] {base_url} → HTTP {resp.status_code}: {resp.text[:120]}")
                continue
            data = resp.json()
            if data.get("status") not in ("tunnel", "redirect", "stream"):
                print(f"  [cobalt] {base_url} → status={data.get('status')} {data.get('error', {}).get('code','')}")
                continue

            out_path = out_dir / f"{video_id}.mp3"
            with requests.get(data["url"], stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(out_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

            if out_path.stat().st_size < 10_000:
                out_path.unlink()
                raise RuntimeError("Downloaded file too small")

            print(f"  [cobalt] Downloaded via {base_url}")
            return out_path

        except Exception as e:
            print(f"  [cobalt] {base_url} failed: {e}")
            continue

    raise RuntimeError("All cobalt instances failed")


def download_audio(video_url: str, out_dir: Path) -> Path:
    """Download audio as MP3. If cookies are available, tries yt-dlp first.
    Otherwise falls back to Invidious → Piped → cobalt → yt-dlp."""
    video_id = video_url.split("v=")[-1].split("&")[0]
    cookies_file = os.environ.get("YOUTUBE_COOKIES_FILE")
    has_cookies = bool(cookies_file and Path(cookies_file).exists())

    # When cookies are available, yt-dlp is the most reliable path — use it directly.
    # Proxy chain (Invidious/Piped/cobalt) is only tried when we have no cookies.
    if not has_cookies:
        try:
            return download_via_invidious(video_url, video_id, out_dir)
        except Exception as e:
            print(f"  [invidious] All instances failed ({e}) — trying Piped")

        try:
            return download_via_piped(video_url, video_id, out_dir)
        except Exception as e:
            print(f"  [piped] All instances failed ({e}) — trying cobalt")

        try:
            return download_via_cobalt(video_url, video_id, out_dir)
        except Exception as e:
            print(f"  [cobalt] Failed ({e}) — falling back to yt-dlp")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "postprocessors": [{
            "key":              "FFmpegExtractAudio",
            "preferredcodec":   "mp3",
            "preferredquality": "128",
        }],
        "quiet":            True,
        "no_warnings":      True,
        "noprogress":       True,
        "retries":          10,
        "fragment_retries": 10,
        "ignoreerrors":     False,
    }
    cookies_file = os.environ.get("YOUTUBE_COOKIES_FILE")
    if cookies_file and Path(cookies_file).exists():
        ydl_opts["cookiefile"] = cookies_file
    info = None
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
    except Exception as e:
        _flag_auth_error_if_needed(e)
        raise

    mp3_files = list(out_dir.glob("*.mp3"))
    if not mp3_files:
        raise FileNotFoundError(f"No MP3 found in {out_dir} after downloading {video_url}")
    duration_secs = int((info or {}).get("duration") or 0)
    return mp3_files[0], duration_secs


_AUTH_PATTERNS = ("sign in", "bot", "403", "cookies", "login", "authentication", "private")

def _flag_auth_error_if_needed(exc: Exception) -> None:
    msg = str(exc).lower()
    if any(p in msg for p in _AUTH_PATTERNS):
        Path("/tmp/auth_error").write_text(str(exc))



# ---------------------------------------------------------------------------
# RSS feed helpers
# ---------------------------------------------------------------------------

def load_feed_entries(feed_path: Path) -> list[dict]:
    """Load existing feed entries from a JSON sidecar file."""
    sidecar = feed_path.with_suffix(".entries.json")
    if sidecar.exists():
        return json.loads(sidecar.read_text(encoding="utf-8"))
    return []


def save_feed_entries(feed_path: Path, entries: list[dict]):
    sidecar = feed_path.with_suffix(".entries.json")
    sidecar.write_text(json.dumps(entries, indent=2, ensure_ascii=False))


def build_rss_feed(channel_cfg: dict, channel_info: dict, entries: list[dict], feed_path: Path):
    """Regenerate the RSS XML from the entries list."""
    fg = FeedGenerator()
    fg.load_extension("podcast")

    feed_url = BASE_URL + f"feeds/{channel_cfg['slug']}.xml"

    fg.id(feed_url)
    fg.title(channel_info["title"])
    fg.author({"name": channel_cfg["podcast_author"], "email": channel_cfg["podcast_email"]})
    fg.link(href=f"{BASE_URL}{channel_cfg['slug']}.html", rel="alternate")
    fg.link(href=feed_url, rel="self")
    fg.description(channel_info["description"] or channel_info["title"])
    fg.language(channel_cfg.get("podcast_language", "en"))
    fg.podcast.itunes_category(channel_cfg.get("podcast_category", "Religion & Spirituality"))
    fg.podcast.itunes_author(channel_cfg["podcast_author"])
    fg.podcast.itunes_owner(name=channel_cfg["podcast_author"], email=channel_cfg["podcast_email"])
    fg.podcast.itunes_explicit("no")

    if channel_info.get("thumbnail"):
        fg.image(channel_info["thumbnail"])
    # Apple Podcasts requires 1400-3000px artwork on a HEAD-friendly server.
    # Use the pre-generated artwork hosted on GitHub Pages; fall back to YouTube thumbnail.
    artwork_url = BASE_URL + f"artwork/{channel_cfg['slug']}.png"
    fg.podcast.itunes_image(artwork_url)

    for entry in sorted(entries, key=lambda e: e["published"], reverse=True):
        fe = fg.add_entry()
        fe.id(entry["video_id"])
        fe.title(entry["title"])
        fe.description(entry.get("description") or entry["title"])
        fe.published(entry["published"])
        ep_url = ep_page_url(channel_cfg["slug"], entry)
        fe.link(href=ep_url, rel="alternate")
        _u = urlsplit(entry["audio_url"])
        _safe_url = urlunsplit(_u._replace(path=quote(_u.path, safe="/-_.~()")))
        fe.enclosure(_safe_url, str(entry.get("file_size", 0)), "audio/mpeg")
        fe.podcast.itunes_duration(str(entry.get("duration_secs", 0)))
        fe.podcast.itunes_explicit("no")
        if entry.get("thumbnail"):
            fe.podcast.itunes_image(entry["thumbnail"])

    fg.rss_file(str(feed_path), pretty=True)
    print(f"  RSS feed written -> {feed_path} ({len(entries)} episodes)")


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_processed() -> dict:
    p = Path(PROCESSED_FILE)
    if p.exists():
        text = p.read_text(encoding="utf-8-sig").strip()
        if text:
            return json.loads(text)
    return {}


def save_processed(data: dict):
    Path(PROCESSED_FILE).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Per-channel processing
# ---------------------------------------------------------------------------

def process_channel(channel_cfg: dict, processed: dict, budget: int = 5) -> int:
    slug       = channel_cfg["slug"]
    channel_id = channel_cfg["youtube_channel_id"]
    print(f"\n{'='*60}")
    print(f"Channel: {slug} ({channel_id})")
    print(f"{'='*60}")

    print("  Fetching channel info from YouTube...")
    channel_info = get_channel_info(channel_id)
    print(f"  Channel name: {channel_info['title']}")

    feed_path = FEEDS_DIR / f"{slug}.xml"
    entries   = load_feed_entries(feed_path)

    # Combine processed.json IDs + entries.json IDs to avoid re-downloading
    already_done = set(processed.get(slug, []))
    already_done.update(e["video_id"] for e in entries)

    new_videos     = get_new_videos(channel_id, already_done)
    entries_before = len(entries)

    if not new_videos:
        print("  No new videos found.")
        if entries:
            build_rss_feed(channel_cfg, channel_info, entries, feed_path)
        return 0

    print(f"  Found {len(new_videos)} new video(s).")

    slots_used = 0
    for video in new_videos:
        if slots_used >= budget:
            print(f"  Budget reached, deferring remaining new videos to next run.")
            break
        print(f"\n  Processing: {video['title']}")

        safe_title   = "".join(c if c.isalnum() or c in "-_" else "_" for c in video["title"])
        safe_title   = safe_title[:80].strip("_")
        mp3_filename = f"{video['id']}_{safe_title}.mp3"

        existing_url = asset_exists_in_r2(mp3_filename, video_id=video["id"])
        if existing_url:
            print(f"  Already uploaded - skipping download.")
            audio_url = existing_url
            file_size = 0
            yt_duration = int(video.get("duration") or 0)
        else:
            for f in AUDIO_DIR.iterdir():
                if f.is_file():
                    f.unlink()
            try:
                mp3_path, yt_duration = download_audio(video["url"], AUDIO_DIR)
                file_size  = mp3_path.stat().st_size
                final_path = AUDIO_DIR / mp3_filename
                mp3_path.rename(final_path)
                print(f"  Uploading to R2...")
                audio_url = upload_audio_to_r2(final_path, mp3_filename)
                print(f"  Uploaded -> {audio_url}")
            except Exception as e:
                print(f"  ERROR downloading/uploading: {e}")
                slots_used += 1  # consume the slot — download was attempted, don't re-attempt this run
                continue

        pub_dt = datetime.fromisoformat(video["published"].replace("Z", "+00:00"))
        entry = {
            "video_id":      video["id"],
            "title":         video["title"],
            "description":   video.get("description", ""),
            "published":     pub_dt.isoformat(),
            "audio_url":     audio_url,
            "file_size":     file_size,
            "duration_secs": yt_duration or int(video.get("duration") or 0),
            "thumbnail":     video.get("thumbnail", ""),
        }
        entries.append(entry)

        processed.setdefault(slug, []).append(video["id"])
        save_processed(processed)
        print(f"  Marked {video['id']} as processed.")
        if not existing_url:  # don't count free "already uploaded" skips against budget
            slots_used += 1
        time.sleep(2)

    if len(entries) > entries_before:
        save_feed_entries(feed_path, entries)
        build_rss_feed(channel_cfg, channel_info, entries, feed_path)
    else:
        print("  No entries were successfully added (all downloads may have failed).")

    return slots_used


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=int, default=5,
                        help="Max videos to download this run (new episodes first)")
    args = parser.parse_args()

    print("=== Podcast Update Run ===")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"Budget: {args.budget} video(s) this run")

    channels  = json.loads(Path(CHANNELS_FILE).read_text(encoding="utf-8-sig"))
    processed = load_processed()

    # Sync processed.json with entries.json so they're always in agreement
    for ch in channels:
        if not ch.get("enabled", True) or ch.get("source") == "rss":
            continue
        slug = ch["slug"]
        feed_path = FEEDS_DIR / f"{slug}.xml"
        entries = load_feed_entries(feed_path)
        if entries:
            known = set(processed.get(slug, []))
            new_ids = [e["video_id"] for e in entries if e["video_id"] not in known]
            if new_ids:
                processed.setdefault(slug, []).extend(new_ids)
    save_processed(processed)

    budget_remaining = args.budget
    errors = []
    for ch in channels:
        if budget_remaining <= 0:
            print(f"\nBudget exhausted — skipping remaining channels.")
            break
        if not ch.get("enabled", True) or ch.get("source") == "rss":
            continue
        try:
            used = process_channel(ch, processed, budget_remaining)
            budget_remaining -= used
        except Exception as e:
            print(f"\nERROR on channel {ch['slug']}: {e}")
            errors.append((ch["slug"], str(e)))

    # Write remaining budget so backfill_podcasts.py knows how many slots are left
    Path("/tmp/backfill_budget").write_text(str(budget_remaining))
    print(f"\nBudget remaining for backfill: {budget_remaining}")

    if errors:
        print(f"\n{'='*60}")
        print(f"Completed with {len(errors)} error(s):")
        for slug, err in errors:
            print(f"  {slug}: {err}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print("New episode check complete.")


if __name__ == "__main__":
    main()
