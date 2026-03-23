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
import time
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


def upload_audio_to_release(release_id: int, mp3_path: Path) -> str:
    """Upload mp3_path to the release. Returns the public download URL."""
    filename = mp3_path.name
    upload_url = (
        f"https://uploads.github.com/repos/{GITHUB_REPO}/releases/{release_id}/assets"
        f"?name={requests.utils.quote(filename)}"
    )
    headers = {**_gh_headers(), "Content-Type": "audio/mpeg"}
    with open(mp3_path, "rb") as f:
        r = requests.post(upload_url, headers=headers, data=f)
    if not r.ok:
        raise Exception(f"Upload failed: {r.status_code} {r.text}")
    return r.json()["browser_download_url"]


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
        "title":       snippet["title"],
        "description": snippet.get("description", ""),
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
                "title":       item["snippet"]["title"],
                "description": item["snippet"].get("description", ""),
                "published":   item["snippet"]["publishedAt"],
                "thumbnail":   (thumbs.get("maxres") or thumbs.get("high") or {}).get("url", ""),
                "url":         f"https://www.youtube.com/watch?v={vid_id}",
            })
    return new_videos


# ---------------------------------------------------------------------------
# Audio download
# ---------------------------------------------------------------------------

def download_audio(video_url: str, out_dir: Path) -> Path:
    """Download audio from video_url as MP3 into out_dir. Returns the MP3 path."""
    ydl_opts = {
        "format": "bestaudio/best",
        "extractor_args": {"youtube": {"player_client": ["web_creator", "ios", "mweb"]}},
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "postprocessors": [{
            "key":              "FFmpegExtractAudio",
            "preferredcodec":   "mp3",
            "preferredquality": "128",
            }],
        "quiet":       False,
        "no_warnings": False,
        "retries":       10,
        "fragment_retries": 10,
        "ignoreerrors": False,
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

def process_channel(channel_cfg: dict, processed: dict):
    slug       = channel_cfg["slug"]
    channel_id = channel_cfg["youtube_channel_id"]
    print(f"\n{'='*60}")
    print(f"Channel: {slug} ({channel_id})")
    print(f"{'='*60}")

    print("  Fetching channel info from YouTube...")
    channel_info = get_channel_info(channel_id)
    print(f"  Channel name: {channel_info['title']}")

    already_done = set(processed.get(slug, []))
    new_videos   = get_new_videos(channel_id, already_done)

    feed_path      = FEEDS_DIR / f"{slug}.xml"
    entries        = load_feed_entries(feed_path)
    entries_before = len(entries)

    if not new_videos:
        print("  No new videos found.")
        if entries:
            build_rss_feed(channel_cfg, channel_info, entries, feed_path)
        return

    print(f"  Found {len(new_videos)} new video(s).")

    release_tag = f"audio-{slug}"
    release     = get_or_create_release(release_tag, f"Audio: {channel_info['title']}")
    release_id  = release["id"]

    for video in new_videos:
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
        time.sleep(2)

    if len(entries) > entries_before:
        save_feed_entries(feed_path, entries)
        build_rss_feed(channel_cfg, channel_info, entries, feed_path)
    else:
        print("  No entries were successfully added (all downloads may have failed).")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    print("=== Podcast Update Run ===")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")

    channels  = json.loads(Path(CHANNELS_FILE).read_text())
    processed = load_processed()

    errors = []
    for ch in channels:
        if not ch.get("enabled", True):
            print(f"\nSkipping disabled channel: {ch['slug']}")
            continue
        try:
            process_channel(ch, processed)
        except Exception as e:
            print(f"\nERROR on channel {ch['slug']}: {e}")
            errors.append((ch["slug"], str(e)))

    if errors:
        print(f"\n{'='*60}")
        print(f"Completed with {len(errors)} error(s):")
        for slug, err in errors:
            print(f"  {slug}: {err}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print("All channels processed successfully.")


if __name__ == "__main__":
    main()
