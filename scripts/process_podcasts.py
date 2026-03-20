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
import hashlib
import subprocess
import requests
from datetime import datetime, timezone
from pathlib import Path
from yt_dlp import YoutubeDL
from feedgen.feed import FeedGenerator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_KEY          = os.environ["YOUTUBE_API_KEY"]
GITHUB_TOKEN     = os.environ["GITHUB_TOKEN"]
GITHUB_REPO      = os.environ.get("GITHUB_REPO", "dav1403/TheThoraPodcast")
BASE_URL         = f"https://{GITHUB_REPO.split('/')[0]}.github.io/{GITHUB_REPO.split('/')[1]}/"

CHANNELS_FILE    = "channels.json"
PROCESSED_FILE   = "processed.json"
FEEDS_DIR        = Path("feeds")
AUDIO_DIR        = Path("tmp_audio")

FEEDS_DIR.mkdir(exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# GitHub Releases helpers
# ---------------------------------------------------------------------------

def get_or_create_release(tag: str, name: str) -> dict:
    """Return the release dict for `tag`, creating it if it doesn't exist."""
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{tag}"
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return r.json()

    # Create it
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
    payload = {"tag_name": tag, "name": name, "draft": False, "prerelease": False}
    r = requests.post(url, headers=headers, json=payload)
    r.raise_for_status()
    return r.json()


def upload_audio_to_release(release_id: int, mp3_path: Path) -> str:
    """Upload mp3_path to the release. Returns the public download URL."""
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Content-Type": "audio/mpeg",
    }
    filename = mp3_path.name
    upload_url = (
        f"https://uploads.github.com/repos/{GITHUB_REPO}/releases/{release_id}/assets"
        f"?name={requests.utils.quote(filename)}"
    )
    with open(mp3_path, "rb") as f:
        r = requests.post(upload_url, headers=headers, data=f)
    r.raise_for_status()
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
            new_videos.append({
                "id": vid_id,
                "title": item["snippet"]["title"],
                "description": item["snippet"].get("description", ""),
                "published": item["snippet"]["publishedAt"],
                "thumbnail": item["snippet"]["thumbnails"].get("maxres",
                             item["snippet"]["thumbnails"].get("high", {})).get("url", ""),
                "url": f"https://www.youtube.com/watch?v={vid_id}",
            })
    return new_videos


# ---------------------------------------------------------------------------
# Audio download
# ---------------------------------------------------------------------------

def download_audio(video_url: str, out_dir: Path) -> Path:
    """Download audio from video_url as MP3 into out_dir. Returns the MP3 path."""
    ydl_opts = {
        "format": "bestaudio/best",
        "extractor_args": {"youtube": {"player_client": ["android", "ios"]}},
        "outtmpl": str(out_dir / "%(title)s.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }],
        "quiet": False,
        "no_warnings": False,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        title = info["title"]

    # Find the resulting mp3
    for f in out_dir.iterdir():
        if f.suffix == ".mp3":
            return f
    raise FileNotFoundError(f"No MP3 found in {out_dir} after downloading '{title}'")


# ---------------------------------------------------------------------------
# RSS feed
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


def build_rss_feed(channel_cfg: dict, entries: list[dict], feed_path: Path):
    """Regenerate the RSS XML from the entries list."""
    fg = FeedGenerator()
    fg.load_extension("podcast")

    fg.id(BASE_URL + f"feeds/{channel_cfg['slug']}.xml")
    fg.title(channel_cfg["podcast_title"])
    fg.author({"name": channel_cfg["podcast_author"], "email": channel_cfg["podcast_email"]})
    fg.link(href=BASE_URL, rel="alternate")
    fg.link(href=BASE_URL + f"feeds/{channel_cfg['slug']}.xml", rel="self")
    fg.description(channel_cfg["podcast_description"])
    fg.language(channel_cfg.get("podcast_language", "en"))
    fg.podcast.itunes_category(channel_cfg.get("podcast_category", "Technology"))
    fg.podcast.itunes_author(channel_cfg["podcast_author"])
    fg.podcast.itunes_explicit("no")

    if channel_cfg.get("podcast_image_url"):
        fg.image(channel_cfg["podcast_image_url"])
        fg.podcast.itunes_image(channel_cfg["podcast_image_url"])

    # Most recent first
    for entry in sorted(entries, key=lambda e: e["published"], reverse=True):
        fe = fg.add_entry()
        fe.id(entry["video_id"])
        fe.title(entry["title"])
        fe.description(entry.get("description") or entry["title"])
        fe.published(entry["published"])
        fe.enclosure(entry["audio_url"], str(entry.get("file_size", 0)), "audio/mpeg")
        fe.podcast.itunes_duration(str(entry.get("duration_secs", 0)))
        fe.podcast.itunes_explicit("no")
        # Per-episode thumbnail (shown on Spotify as episode artwork)
        if entry.get("thumbnail"):
            fe.podcast.itunes_image(entry["thumbnail"])

    fg.rss_file(str(feed_path), pretty=True)
    print(f"  RSS feed written → {feed_path} ({len(entries)} episodes)")


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_processed() -> dict:
    if Path(PROCESSED_FILE).exists():
        return json.loads(Path(PROCESSED_FILE).read_text())
    return {}


def save_processed(data: dict):
    Path(PROCESSED_FILE).write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_channel(channel_cfg: dict, processed: dict):
    slug = channel_cfg["slug"]
    channel_id = channel_cfg["youtube_channel_id"]
    print(f"\n{'='*60}")
    print(f"Channel: {channel_cfg['podcast_title']} ({slug})")
    print(f"{'='*60}")

    already_done = set(processed.get(slug, []))
    new_videos = get_new_videos(channel_id, already_done)

    if not new_videos:
        print("  No new videos found.")
        return

    print(f"  Found {len(new_videos)} new video(s).")

    feed_path = FEEDS_DIR / f"{slug}.xml"
    entries = load_feed_entries(feed_path)

    # GitHub release tag for this channel's audio files
    release_tag = f"audio-{slug}"
    release = get_or_create_release(release_tag, f"Audio: {channel_cfg['podcast_title']}")
    release_id = release["id"]

    for video in new_videos:
        print(f"\n  Processing: {video['title']}")

        # Sanitize filename for GitHub release asset
        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in video["title"])
        safe_title = safe_title[:80].strip()
        mp3_filename = f"{video['id']}_{safe_title}.mp3"

        # Check if already uploaded to this release
        existing_url = asset_already_exists(release, mp3_filename)
        if existing_url:
            print(f"  Already uploaded, skipping download.")
            audio_url = existing_url
            file_size = 0
        else:
            # Clean tmp dir before each download
            for f in AUDIO_DIR.iterdir():
                f.unlink()

            try:
                mp3_path = download_audio(video["url"], AUDIO_DIR)
                file_size = mp3_path.stat().st_size

                # Rename to our canonical filename
                final_path = AUDIO_DIR / mp3_filename
                mp3_path.rename(final_path)

                print(f"  Uploading to GitHub Releases...")
                audio_url = upload_audio_to_release(release_id, final_path)
                print(f"  Uploaded → {audio_url}")

            except Exception as e:
                print(f"  ERROR downloading/uploading: {e}")
                continue

        # Add to feed entries
        pub_dt = datetime.fromisoformat(video["published"].replace("Z", "+00:00"))
        entries.append({
            "video_id": video["id"],
            "title": video["title"],
            "description": video.get("description", ""),
            "published": pub_dt.isoformat(),
            "audio_url": audio_url,
            "file_size": file_size,
            "duration_secs": 0,
            "thumbnail": video.get("thumbnail", ""),
        })

        # Mark as processed
        processed.setdefault(slug, []).append(video["id"])
        save_processed(processed)
        print(f"  Marked {video['id']} as processed.")

        # Small delay to be kind to APIs
        time.sleep(2)

    # Rebuild RSS
    save_feed_entries(feed_path, entries)
    build_rss_feed(channel_cfg, entries, feed_path)


def main():
    print("=== Podcast Update Run ===")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")

    channels = json.loads(Path(CHANNELS_FILE).read_text())
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
