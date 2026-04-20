"""
backfill_podcasts.py
--------------------
Gradually backfills historical episodes for all enabled channels.
Called after process_podcasts.py with the remaining video budget.

Strategy:
  - 1 slot per channel per run (round-robin fairness)
  - Fetches videos published before the oldest episode we already have
  - Skips channels marked as exhausted in backfill_state.json
  - Adds a 15s delay between downloads to stay gentle on YouTube
"""

import argparse
import html
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# Reuse all helpers from process_podcasts
sys.path.insert(0, str(Path(__file__).parent))
from process_podcasts import (
    get_channel_info,
    upload_audio_to_r2,
    asset_exists_in_r2,
    download_audio,
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

BACKFILL_STATE_FILE = Path("backfill_state.json")


def load_backfill_state() -> dict:
    if BACKFILL_STATE_FILE.exists():
        content = BACKFILL_STATE_FILE.read_text().strip()
        return json.loads(content) if content else {}
    return {}


def save_backfill_state(state: dict):
    BACKFILL_STATE_FILE.write_text(json.dumps(state, indent=2))


def get_historical_videos(channel_id: str, before_date: str,
                          already_processed: set, max_results: int = 5) -> list[dict]:
    """Fetch videos published strictly before before_date, excluding already processed."""
    url = (
        f"https://www.googleapis.com/youtube/v3/search"
        f"?key={API_KEY}&channelId={channel_id}"
        f"&part=snippet,id&order=date&maxResults={max_results}"
        f"&publishedBefore={before_date}&type=video"
    )
    r = requests.get(url)
    data = r.json()

    if "error" in data:
        raise Exception(f"YouTube API error: {data['error']['message']}")

    videos = []
    for item in data.get("items", []):
        vid_id = item["id"]["videoId"]
        if vid_id in already_processed:
            continue
        thumbs = item["snippet"]["thumbnails"]
        videos.append({
            "id":          vid_id,
            "title":       html.unescape(item["snippet"]["title"]),
            "description": html.unescape(item["snippet"].get("description", "")),
            "published":   item["snippet"]["publishedAt"],
            "thumbnail":   (thumbs.get("maxres") or thumbs.get("high") or {}).get("url", ""),
            "url":         f"https://www.youtube.com/watch?v={vid_id}",
        })
    return videos


def backfill_channel(channel_cfg: dict, processed: dict, state: dict) -> int:
    """
    Download 1 historical episode for this channel.
    Returns 1 if a slot was used, 0 otherwise.
    """
    slug       = channel_cfg["slug"]
    channel_id = channel_cfg["youtube_channel_id"]

    ch_state = state.setdefault(slug, {"exhausted": False})
    if ch_state.get("exhausted"):
        print(f"  [{slug}] Already exhausted, skipping.")
        return 0

    feed_path = FEEDS_DIR / f"{slug}.xml"
    entries   = load_feed_entries(feed_path)

    if not entries:
        print(f"  [{slug}] No feed yet — bootstrap should handle this channel first.")
        return 0

    # Find our oldest episode to use as the publishedBefore boundary
    oldest_date = min(e["published"] for e in entries)
    # YouTube API wants RFC 3339 with Z suffix
    if oldest_date.endswith("+00:00"):
        oldest_date = oldest_date.replace("+00:00", "Z")

    already_done = set(processed.get(slug, []))
    already_done.update(e["video_id"] for e in entries)

    print(f"  [{slug}] Fetching history before {oldest_date}...")
    candidates = get_historical_videos(channel_id, oldest_date, already_done, max_results=5)

    if not candidates:
        print(f"  [{slug}] No more historical videos — marking exhausted.")
        ch_state["exhausted"] = True
        save_backfill_state(state)
        return 0

    # Take only the most recent candidate (1 slot per channel per run)
    video = candidates[0]
    print(f"  [{slug}] Backfilling: {video['title']} ({video['published']})")

    channel_info = get_channel_info(channel_id)

    safe_title   = "".join(c if c.isalnum() or c in " -_" else "_" for c in video["title"])
    safe_title   = safe_title[:80].strip()
    mp3_filename = f"{video['id']}_{safe_title}.mp3"

    existing_url = asset_exists_in_r2(mp3_filename, video_id=video["id"])
    if existing_url:
        print(f"  [{slug}] Already uploaded — recording entry only.")
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
            print(f"  [{slug}] Uploading to R2...")
            audio_url = upload_audio_to_r2(final_path, mp3_filename)
            print(f"  [{slug}] Uploaded -> {audio_url}")
        except Exception as e:
            print(f"  [{slug}] ERROR: {e} — skipping this video.")
            return 1  # consume the slot — download was attempted, don't retry this run

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
    save_feed_entries(feed_path, entries)
    build_rss_feed(channel_cfg, channel_info, entries, feed_path)
    print(f"  [{slug}] Done.")
    return 0 if existing_url else 1  # free skip doesn't consume a budget slot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=int, default=0,
                        help="Number of backfill slots available this run")
    args = parser.parse_args()

    if args.budget <= 0:
        print("=== Backfill: no budget remaining, skipping. ===")
        return

    print(f"=== Backfill Run — budget: {args.budget} slot(s) ===")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")

    channels  = json.loads(Path("channels.json").read_text())
    processed = load_processed()
    state     = load_backfill_state()

    enabled = [ch for ch in channels if ch.get("enabled", True)]

    # Check if all channels are exhausted
    all_exhausted = all(
        state.get(ch["slug"], {}).get("exhausted", False) for ch in enabled
    )
    if all_exhausted:
        print("All channels fully backfilled — nothing to do.")
        return

    budget_remaining = args.budget
    slots_used = 0

    # Multi-pass round-robin: keep looping through channels until budget is
    # exhausted or a full pass produces no progress at all.
    # Free skips (already-uploaded orphans) don't consume budget but DO advance
    # a channel's oldest-date frontier, so we count them as progress and keep going.
    while budget_remaining > 0:
        all_exhausted = all(
            state.get(ch["slug"], {}).get("exhausted", False) for ch in enabled
        )
        if all_exhausted:
            print("All channels fully backfilled — nothing to do.")
            break

        progress_this_pass = False  # any channel did something this pass
        for ch in enabled:
            if budget_remaining <= 0:
                break
            if state.get(ch["slug"], {}).get("exhausted", False):
                continue
            try:
                used = backfill_channel(ch, processed, state)
                now_exhausted = state.get(ch["slug"], {}).get("exhausted", False)
                if used:
                    slots_used += 1
                    budget_remaining -= 1
                    progress_this_pass = True
                    if budget_remaining > 0:
                        delay = random.uniform(15, 30)
                        print(f"  Waiting {delay:.0f}s before next download...")
                        time.sleep(delay)
                elif not now_exhausted:
                    # returned 0 but not exhausted = free skip, frontier advanced
                    progress_this_pass = True
            except Exception as e:
                print(f"  ERROR on {ch['slug']}: {e}")

        if not progress_this_pass:
            break  # nothing happened anywhere — genuinely nothing left to do

    print(f"\n=== Backfill complete: {slots_used} episode(s) added ===")


if __name__ == "__main__":
    main()
