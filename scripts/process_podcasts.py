"""
process_podcasts.py
-------------------
Monitors multiple YouTube channels for new videos.
For each new video:
  1. Downloads audio as MP3 via yt-dlp
  2. Uploads MP3 to GitHub Releases (free public hosting)
  3. Updates the channel's RSS feed XML (hosted on GitHub Pages)
  4. Records the video ID in processed.json to avoid re-processing

Designed to run daily via GitHub Actions.
"""

import os
import sys
import json
import argparse
import time
import subprocess
import html
import requests
from datetime import datetime, timezone
from pathlib import Path
from yt_dlp import YoutubeDL
from feedgen.feed import FeedGenerator

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
BASE_URL = f"https://{_owner}.github.io/{_repo_name}/"

CHANNELS_FILE  = "channels.json"
PROCESSED_FILE = "processed.json"
FEEDS_DIR      = Path("feeds")
AUDIO_DIR      = Path("tmp_audio")

FEEDS_DIR.mkdir(exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# GitHub Releases helpers
# ---------------------------------------------------------------------------

def _gh_headers() -> dict:
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def get_or_create_release(tag: str, name: str) -> dict:
    """Return the release dict for `tag`, creating it if it doesn't exist."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{tag}"
    r = requests.get(url, headers=_gh_headers())
    if r.status_code == 200:
        return r.json()

    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
    payload = {
        "tag_name":         tag,
        "name":             name,
        "draft":            False,
        "prerelease":       False,
        "target_commitish": "main",
    }
    r = requests.post(url, headers=_gh_headers(), json=payload)
    if not r.ok:
        raise Exception(f"Failed to create release '{tag}': {r.status_code} {r.text}")
    return r.json()


def upload_audio_to_release(release_id: int, mp3_path: Path, retries: int = 3) -> str:
    """Upload mp3_path to the release. Returns the public download URL."""
    filename = mp3_path.name
    upload_url = (
        f"https://uploads.github.com/repos/{GITHUB_REPO}/releases/{release_id}/assets"
        f"?name={requests.utils.quote(filename)}"
    )
    headers = {**_gh_headers(), "Content-Type": "audio/mpeg"}
    for attempt in range(retries):
        try:
            with open(mp3_path, "rb") as f:
                r = requests.post(upload_url, headers=headers, data=f, timeout=300)
            if r.ok:
                return r.json()["browser_download_url"]
            raise Exception(f"Upload failed: {r.status_code} {r.text[:200]}")
        except Exception as e:
            if attempt < retries - 1:
                wait = 30 * (attempt + 1)
                print(f"  Upload attempt {attempt + 1} failed: {e}. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise


def asset_already_exists(release: dict, filename: str) -> str | None:
    """Return the download URL if this filename is already in the release."""
    for asset in release.get("assets", []):
        if asset["name"] == filename:
            return asset["browser_download_url"]
    return None

# ---------------------------------------------------------------------------
# YouTube API helpers
# ---------------------------------------------------------------------------

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
    thumb_url  = (
        thumbnails.get("maxres") or
        thumbnails.get("high")   or
        thumbnails.get("medium") or
        thumbnails.get("default") or
        {}
    ).get("url", "")

    return {
        "title":       html.unescape(snippet["title"]),
        "description": html.unescape(snippet.get("description", "")),
        "thumbnail":   thumb_url,
    }


def get_new_videos(channel_id: str, already_processed: set) -> list[dict]:
    """
    Fetch the latest 10 videos from the channel.
    Return only those whose IDs are not in already_processed.
    """
    url = (
        f"https://www.googleapis.com/youtube/v3/search"
        f"?key={API_KEY}&channelId={channel_id}"
        f"&part=snippet,id&order=date&maxResults=10&type=video"
    )
    r = requests.get(url)
    data = r.json()

    if "error" in data:
        raise Exception(f"YouTube API error: {data['error']['message']}")
    if "items" not in data:
        print(f"  No items returned for channel {channel_id}. Response: {data}")
        return []

    new_videos = []
    for item in data["items"]:
        vid_id = item["id"]["videoId"]
        if vid_id not in already_processed:
            thumbs = item["snippet"]["thumbnails"]
            new_videos.append({
                "id":          vid_id,
                "title":       html.unescape(item["snippet"]["title"]),
                "description": html.unescape(item["snippet"].get("description", "")),
                "published":   item["snippet"]["publishedAt"],
                "thumbnail":   (thumbs.get("maxres") or thumbs.get("high") or {}).get("url", ""),
                "url":         f"https://www.youtube.com/watch?v={vid_id}",
            })
    return new_videos


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
        "quiet":            False,
        "no_warnings":      False,
        "retries":          10,
        "fragment_retries": 10,
        "ignoreerrors":     False,
    }
    cookies_file = os.environ.get("YOUTUBE_COOKIES_FILE")
    if cookies_file and Path(cookies_file).exists():
        ydl_opts["cookiefile"] = cookies_file
    with YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(video_url, download=True)

    mp3_files = list(out_dir.glob("*.mp3"))
    if not mp3_files:
        raise FileNotFoundError(f"No MP3 found in {out_dir} after downloading {video_url}")
    return mp3_files[0]


# ---------------------------------------------------------------------------
# RSS feed helpers
# ---------------------------------------------------------------------------

def load_feed_entries(feed_path: Path) -> list[dict]:
    """Load existing feed entries from a JSON sidecar file."""
    sidecar = feed_path.with_suffix(".entries.json")
    if sidecar.exists():
        return json.loads(sidecar.read_text())
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
    fg.link(href=BASE_URL, rel="alternate")
    fg.link(href=feed_url, rel="self")
    fg.description(channel_info["description"] or channel_info["title"])
    fg.language(channel_cfg.get("podcast_language", "en"))
    fg.podcast.itunes_category(channel_cfg.get("podcast_category", "Technology"))
    fg.podcast.itunes_author(channel_cfg["podcast_author"])
    fg.podcast.itunes_owner(name=channel_cfg["podcast_author"], email=channel_cfg["podcast_email"])
    fg.podcast.itunes_explicit("no")

    if channel_info.get("thumbnail"):
        fg.image(channel_info["thumbnail"])
        fg.podcast.itunes_image(channel_info["thumbnail"])

    for entry in sorted(entries, key=lambda e: e["published"], reverse=True):
        fe = fg.add_entry()
        fe.id(entry["video_id"])
        fe.title(entry["title"])
        fe.description(entry.get("description") or entry["title"])
        fe.published(entry["published"])
        fe.enclosure(entry["audio_url"], str(entry.get("file_size", 0)), "audio/mpeg")
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
        text = p.read_text().strip()
        if text:
            return json.loads(text)
    return {}


def save_processed(data: dict):
    Path(PROCESSED_FILE).write_text(json.dumps(data, indent=2, ensure_ascii=False))


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

    release_tag = f"audio-{slug}"
    release     = get_or_create_release(release_tag, f"Audio: {channel_info['title']}")
    release_id  = release["id"]

    slots_used = 0
    for video in new_videos:
        if slots_used >= budget:
            print(f"  Budget reached, deferring remaining new videos to next run.")
            break
        print(f"\n  Processing: {video['title']}")

        safe_title   = "".join(c if c.isalnum() or c in " -_" else "_" for c in video["title"])
        safe_title   = safe_title[:80].strip()
        mp3_filename = f"{video['id']}_{safe_title}.mp3"

        existing_url = asset_already_exists(release, mp3_filename)
        if existing_url:
            print(f"  Already uploaded - skipping download.")
            audio_url = existing_url
            file_size = 0
        else:
            for f in AUDIO_DIR.iterdir():
                if f.is_file():
                    f.unlink()
            try:
                mp3_path   = download_audio(video["url"], AUDIO_DIR)
                file_size  = mp3_path.stat().st_size
                final_path = AUDIO_DIR / mp3_filename
                mp3_path.rename(final_path)
                print(f"  Uploading to GitHub Releases...")
                audio_url = upload_audio_to_release(release_id, final_path)
                release["assets"].append({"name": mp3_filename, "browser_download_url": audio_url})
                print(f"  Uploaded -> {audio_url}")
            except Exception as e:
                print(f"  ERROR downloading/uploading: {e}")
                slots_used += 1  # consume the slot — download was attempted, don't re-attempt this run
                continue

        pub_dt = datetime.fromisoformat(video["published"].replace("Z", "+00:00"))
        entries.append({
            "video_id":      video["id"],
            "title":         video["title"],
            "description":   video.get("description", ""),
            "published":     pub_dt.isoformat(),
            "audio_url":     audio_url,
            "file_size":     file_size,
            "duration_secs": 0,
            "thumbnail":     video.get("thumbnail", ""),
        })

        processed.setdefault(slug, []).append(video["id"])
        save_processed(processed)
        print(f"  Marked {video['id']} as processed.")
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

    channels  = json.loads(Path(CHANNELS_FILE).read_text())
    processed = load_processed()

    # Sync processed.json with entries.json so they're always in agreement
    for ch in channels:
        if not ch.get("enabled", True):
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
        if not ch.get("enabled", True):
            print(f"\nSkipping disabled channel: {ch['slug']}")
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
