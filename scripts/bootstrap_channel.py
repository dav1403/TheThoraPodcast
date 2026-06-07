"""
bootstrap_channel.py
--------------------
Run this ONCE when adding a new channel to backfill historical episodes.

Usage:
    export YOUTUBE_API_KEY=your_key
    export GITHUB_TOKEN=your_personal_access_token
    export GITHUB_REPO=youruser/yourrepo
    export R2_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
    export R2_ACCESS_KEY_ID=your_r2_key
    export R2_SECRET_ACCESS_KEY=your_r2_secret
    export R2_BUCKET_NAME=your_bucket
    export R2_PUBLIC_URL=https://your-public-url.r2.dev
    python scripts/bootstrap_channel.py --slug the-thora-podcast --max 20

This will:
  1. Fetch the last N videos from the channel
  2. Download each as MP3
  3. Upload to Cloudflare R2
  4. Build the initial RSS feed

After this, process_podcasts.py handles new episodes automatically.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from datetime import datetime, timezone

import requests

# Reuse all helpers from process_podcasts
sys.path.insert(0, str(Path(__file__).parent))
from process_podcasts import (
    get_channel_info,
    upload_audio_to_r2,
    asset_exists_in_r2,
    download_audio,
    discover_channel_tab_ids,
    fetch_video_metadata,
    build_rss_feed,
    load_feed_entries,
    save_feed_entries,
    load_processed,
    save_processed,
    FEEDS_DIR,
    AUDIO_DIR,
    API_KEY,
    GITHUB_REPO,
)


def get_videos_for_channel(channel_id: str, max_results: int = 20) -> list[dict]:
    """Fetch up to max_results recent channel items across videos, streams and shorts."""
    ids = discover_channel_tab_ids(channel_id, tabs=("/videos", "/streams", "/shorts"), limit_per_tab=max_results)
    videos = fetch_video_metadata(ids[:max_results])
    videos.sort(key=lambda v: v.get("published", ""), reverse=True)
    return videos[:max_results]


def main():
    parser = argparse.ArgumentParser(description="Bootstrap a new podcast channel feed")
    parser.add_argument("--slug",  required=True, help="Channel slug from channels.json")
    parser.add_argument("--max",   type=int, default=10, help="Max historical episodes to backfill (default: 10)")
    args = parser.parse_args()

    channels    = json.loads(Path("channels.json").read_text(encoding="utf-8-sig"))
    channel_cfg = next((c for c in channels if c["slug"] == args.slug), None)
    if not channel_cfg:
        print(f"ERROR: No channel with slug '{args.slug}' found in channels.json")
        sys.exit(1)

    print(f"Fetching channel info from YouTube...")
    channel_info = get_channel_info(channel_cfg["youtube_channel_id"])
    print(f"Channel name : {channel_info['title']}")
    print(f"Channel image: {channel_info['thumbnail']}")
    print(f"Fetching up to {args.max} videos...")

    videos = get_videos_for_channel(channel_cfg["youtube_channel_id"], args.max)
    print(f"Found {len(videos)} videos.")

    processed  = load_processed()
    already_done = set(processed.get(args.slug, []))

    feed_path    = FEEDS_DIR / f"{args.slug}.xml"
    entries      = load_feed_entries(feed_path)
    existing_ids = {e["video_id"] for e in entries}

    for video in reversed(videos):   # oldest first so feed order is correct
        if video["id"] in already_done or video["id"] in existing_ids:
            print(f"  Skipping already-processed: {video['title']}")
            continue

        print(f"\n  Processing: {video['title']}")

        safe_title   = "".join(c if c.isalnum() or c in "-_" else "_" for c in video["title"])
        safe_title   = safe_title[:80].strip("_")
        mp3_filename = f"{video['id']}_{safe_title}.mp3"

        existing_url = asset_exists_in_r2(mp3_filename)
        if existing_url:
            print(f"  Already uploaded: {existing_url}")
            audio_url = existing_url
            file_size = 0
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
                print(f"  {audio_url}")
            except Exception as e:
                print(f"  ERROR: {e} — skipping this video")
                continue

        pub_dt = datetime.fromisoformat(video["published"].replace("Z", "+00:00"))
        entries.append({
            "video_id":      video["id"],
            "title":         video["title"],
            "description":   video.get("description", ""),
            "published":     pub_dt.isoformat(),
            "audio_url":     audio_url,
            "file_size":     file_size,
            "duration_secs": int(video.get("duration") or yt_duration or 0),
            "thumbnail":     video.get("thumbnail", ""),
        })

        processed.setdefault(args.slug, []).append(video["id"])
        save_processed(processed)
        time.sleep(2)

    save_feed_entries(feed_path, entries)
    build_rss_feed(channel_cfg, channel_info, entries, feed_path)

    feed_url  = f"https://thetorahpodcast.net/feeds/{args.slug}.xml"
    print(f"\nBootstrap complete. Feed: feeds/{args.slug}.xml ({len(entries)} episodes)")
    print(f"\nNext step: Submit this RSS URL to Spotify for Podcasters:")
    print(f"  {feed_url}")


if __name__ == "__main__":
    main()
