"""
bootstrap_channel.py
--------------------
Run this ONCE when adding a new channel to backfill historical episodes.

Usage:
    python scripts/bootstrap_channel.py --slug the-thora-podcast --max 20

This will:
  1. Fetch the last N videos from the channel
  2. Download each as MP3
  3. Upload to GitHub Releases
  4. Build the initial RSS feed

After this, process_podcasts.py handles new episodes automatically.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Reuse all helpers from process_podcasts
sys.path.insert(0, str(Path(__file__).parent))
from process_podcasts import (
    get_channel_info,
    get_or_create_release,
    upload_audio_to_release,
    asset_already_exists,
    download_audio,
    build_rss_feed,
    load_feed_entries,
    save_feed_entries,
    load_processed,
    save_processed,
    FEEDS_DIR,
    AUDIO_DIR,
    API_KEY,
)
import requests
from datetime import datetime, timezone


def get_videos_for_channel(channel_id: str, max_results: int = 20) -> list[dict]:
    """Fetch up to max_results videos from a channel (most recent first)."""
    all_videos = []
    page_token = None

    while len(all_videos) < max_results:
        batch = min(50, max_results - len(all_videos))
        url = (
            f"https://www.googleapis.com/youtube/v3/search"
            f"?key={API_KEY}&channelId={channel_id}"
            f"&part=snippet,id&order=date&maxResults={batch}&type=video"
        )
        if page_token:
            url += f"&pageToken={page_token}"

        r = requests.get(url)
        data = r.json()

        if "error" in data:
            raise Exception(f"YouTube API error: {data['error']['message']}")

        for item in data.get("items", []):
            all_videos.append({
                "id": item["id"]["videoId"],
                "title": item["snippet"]["title"],
                "description": item["snippet"].get("description", ""),
                "published": item["snippet"]["publishedAt"],
                "thumbnail": item["snippet"]["thumbnails"].get("maxres",
                             item["snippet"]["thumbnails"].get("high", {})).get("url", ""),
                "url": f"https://www.youtube.com/watch?v={item['id']['videoId']}",
            })

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return all_videos


def main():
    parser = argparse.ArgumentParser(description="Bootstrap a new podcast channel feed")
    parser.add_argument("--slug", required=True, help="Channel slug from channels.json")
    parser.add_argument("--max", type=int, default=10, help="Max historical episodes to backfill (default: 10)")
    args = parser.parse_args()

    channels = json.loads(Path("channels.json").read_text())
    channel_cfg = next((c for c in channels if c["slug"] == args.slug), None)
    if not channel_cfg:
        print(f"ERROR: No channel with slug '{args.slug}' found in channels.json")
        sys.exit(1)

    # Fetch live channel metadata from YouTube
    print(f"Fetching channel info from YouTube...")
    channel_info = get_channel_info(channel_cfg["youtube_channel_id"])
    print(f"Channel name : {channel_info['title']}")
    print(f"Channel image: {channel_info['thumbnail']}")
    print(f"Fetching up to {args.max} videos...")

    videos = get_videos_for_channel(channel_cfg["youtube_channel_id"], args.max)
    print(f"Found {len(videos)} videos.")

    processed = load_processed()
    already_done = set(processed.get(args.slug, []))

    feed_path = FEEDS_DIR / f"{args.slug}.xml"
    entries = load_feed_entries(feed_path)
    existing_ids = {e["video_id"] for e in entries}

    release_tag = f"audio-{args.slug}"
    release = get_or_create_release(release_tag, f"Audio: {channel_info['title']}")
    release_id = release["id"]

    for video in reversed(videos):  # oldest first so feed order is correct
        if video["id"] in already_done or video["id"] in existing_ids:
            print(f"  Skipping already-processed: {video['title']}")
            continue

        print(f"\n  Processing: {video['title']}")

        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in video["title"])
        safe_title = safe_title[:80].strip()
        mp3_filename = f"{video['id']}_{safe_title}.mp3"

        existing_url = asset_already_exists(release, mp3_filename)
        if existing_url:
            print(f"  Already uploaded: {existing_url}")
            audio_url = existing_url
            file_size = 0
        else:
            for f in AUDIO_DIR.iterdir():
                f.unlink()
            try:
                mp3_path = download_audio(video["url"], AUDIO_DIR)
                file_size = mp3_path.stat().st_size
                final_path = AUDIO_DIR / mp3_filename
                mp3_path.rename(final_path)
                print(f"  Uploading to GitHub Releases...")
                audio_url = upload_audio_to_release(release_id, final_path)
                print(f"  ✓ {audio_url}")
            except Exception as e:
                print(f"  ERROR: {e} — skipping this video")
                continue

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

        processed.setdefault(args.slug, []).append(video["id"])
        save_processed(processed)
        time.sleep(2)

    save_feed_entries(feed_path, entries)
    build_rss_feed(channel_cfg, channel_info, entries, feed_path)

    repo = os.environ.get("GITHUB_REPO", "dav1403/TheThoraPodcast")
    owner = repo.split("/")[0]
    repo_name = repo.split("/")[1]
    feed_url = f"https://{owner}.github.io/{repo_name}/feeds/{args.slug}.xml"
    print(f"\n✓ Bootstrap complete. Feed: feeds/{args.slug}.xml ({len(entries)} episodes)")
    print(f"\nNext step: Submit this RSS URL to Spotify for Podcasters:")
    print(f"  {feed_url}")


if __name__ == "__main__":
    main()
