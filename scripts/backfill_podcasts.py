import argparse, json, os, random, sys, time
from datetime import datetime, timezone
from pathlib import Path
import requests
from yt_dlp import YoutubeDL
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))
from process_podcasts import (
    get_channel_info, upload_audio_to_r2, asset_exists_in_r2,
    download_audio, channel_intro_trim_sec, build_rss_feed, load_feed_entries, save_feed_entries,
    load_processed, save_processed, FEEDS_DIR, AUDIO_DIR, API_KEY, GITHUB_REPO,
    discover_channel_tab_ids, fetch_video_metadata, fetch_video_status,
)

BACKFILL_STATE_FILE = Path("backfill_state.json")
RESCAN_DAYS = 7  # re-scan channel for new videos after this many days

def load_backfill_state():
    if BACKFILL_STATE_FILE.exists():
        content = BACKFILL_STATE_FILE.read_text(encoding="utf-8-sig").strip()
        return json.loads(content) if content else {}
    return {}

def save_backfill_state(state):
    tmp = BACKFILL_STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(BACKFILL_STATE_FILE)

def discover_missing_videos(channel_id, already_done):
    print("    Scanning /videos, /streams and /shorts via yt-dlp...")
    all_ids = discover_channel_tab_ids(channel_id, tabs=("/videos", "/streams", "/shorts"))
    missing = [v for v in all_ids if v not in already_done]
    missing.reverse()  # process oldest-first
    print("    " + str(len(all_ids)) + " total on channel, " + str(len(missing)) + " not yet in feed.")
    return missing, len(all_ids)

# Number of independent scan passes a video must be missing from the YouTube
# API before we treat it as permanently gone and burn it into processed.json.
ABSENT_CONFIRM_THRESHOLD = 2

def get_video_info(video_id):
    """Classify a video, distinguishing transient/premiere from confirmed-absent.

    Returns a (status, video) tuple:
      ("ok", dict) | ("premiere", None) | ("absent", None).
    Raises on a transient API error so the caller can re-queue instead of
    burning the ID.
    """
    return fetch_video_status(video_id)

def backfill_channel(channel_cfg, processed, state):
    slug = channel_cfg["slug"]
    channel_id = channel_cfg["youtube_channel_id"]
    ch_state = state.setdefault(slug, {})
    feed_path = FEEDS_DIR / (slug + ".xml")
    entries = load_feed_entries(feed_path)
    if not entries:
        print("  [" + slug + "] No feed yet.")
        return 0
    already_done = set(processed.get(slug, []))
    already_done.update(e["video_id"] for e in entries)
    todo = ch_state.get("todo")

    # Decide whether to run a fresh channel scan.
    # todo=None  → never scanned yet.
    # todo=[]    → previous scan found nothing new; re-scan after RESCAN_DAYS.
    needs_rescan = False
    if todo is None:
        needs_rescan = True
    elif not todo:
        last_scan = ch_state.get("last_scan", "")
        if not last_scan:
            needs_rescan = True
        else:
            try:
                days_since = (datetime.utcnow() - datetime.fromisoformat(last_scan)).days
                needs_rescan = days_since >= RESCAN_DAYS
            except Exception:
                needs_rescan = True

    if needs_rescan:
        todo, yt_count = discover_missing_videos(channel_id, already_done)
        ch_state["last_scan"] = datetime.utcnow().isoformat()
        ch_state["yt_video_count"] = yt_count
        ch_state.pop("exhausted", None)  # clean up legacy flag
        if not todo:
            ch_state["todo"] = []
            save_backfill_state(state)
            print("  [" + slug + "] No missing videos found.")
            return 0
        ch_state["todo"] = todo
        save_backfill_state(state)
    else:
        todo = [v for v in todo if v not in already_done]
        if not todo:
            ch_state["todo"] = []
            if not ch_state.get("last_scan"):
                ch_state["last_scan"] = datetime.utcnow().isoformat()
            save_backfill_state(state)
            print("  [" + slug + "] Todo list empty.")
            return 0
        ch_state["todo"] = todo
    video_id = todo[0]
    ch_state["todo"] = todo[1:]
    save_backfill_state(state)

    try:
        status, video = get_video_info(video_id)
    except Exception as e:
        # Transient API error (quota/auth/network) — do NOT burn the ID.
        # Re-queue at the end of todo and retry on a later pass, like download-fail.
        print("  [" + slug + "] Metadata fetch failed for " + video_id + ": " + str(e) + " (will retry).")
        ch_state["todo"] = ch_state.get("todo", []) + [video_id]
        save_backfill_state(state)
        return 1

    if status == "premiere":
        # Live/upcoming Premiere — not yet downloadable. Re-queue, never burn.
        print("  [" + slug + "] Video " + video_id + " is live/upcoming (premiere) - deferring.")
        ch_state["todo"] = ch_state.get("todo", []) + [video_id]
        save_backfill_state(state)
        return 1

    if status == "absent":
        # Absent from the API response. Could still be a transient blip, so only
        # confirm as permanently gone after ABSENT_CONFIRM_THRESHOLD passes.
        misses = ch_state.setdefault("miss_counts", {})
        misses[video_id] = misses.get(video_id, 0) + 1
        if misses[video_id] >= ABSENT_CONFIRM_THRESHOLD:
            print("  [" + slug + "] Video " + video_id + " confirmed unavailable (private/deleted) after "
                  + str(misses[video_id]) + " misses.")
            misses.pop(video_id, None)
            save_backfill_state(state)
            processed.setdefault(slug, []).append(video_id)
            save_processed(processed)
            return 0
        # First miss — re-queue for a confirmation pass rather than burning it.
        print("  [" + slug + "] Video " + video_id + " missing from API (miss "
              + str(misses[video_id]) + "/" + str(ABSENT_CONFIRM_THRESHOLD) + ") - will re-check.")
        ch_state["todo"] = ch_state.get("todo", []) + [video_id]
        save_backfill_state(state)
        return 1

    # status == "ok": clear any stale miss counter and proceed to download.
    ch_state.get("miss_counts", {}).pop(video_id, None)
    print("  [" + slug + "] Backfilling: " + video["title"] + " (" + video["published"][:10] + ")")
    channel_info = get_channel_info(channel_id)
    safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in video["title"])
    safe_title = safe_title[:80].strip("_")
    vid_id = video["id"]
    mp3_filename = vid_id + "_" + safe_title + ".mp3"
    existing_url = asset_exists_in_r2(mp3_filename, video_id=video["id"])
    if existing_url:
        print("  [" + slug + "] Already in R2 - recording entry only.")
        audio_url = existing_url
        file_size = 0
    else:
        for f in AUDIO_DIR.iterdir():
            if f.is_file():
                f.unlink()
        try:
            mp3_path, yt_duration = download_audio(
                video["url"], AUDIO_DIR, channel_intro_trim_sec(channel_cfg))
            file_size = mp3_path.stat().st_size
            final_path = AUDIO_DIR / mp3_filename
            mp3_path.rename(final_path)
            print("  [" + slug + "] Uploading to R2...")
            audio_url = upload_audio_to_r2(final_path, mp3_filename)
            print("  [" + slug + "] Uploaded -> " + audio_url)
        except Exception as e:
            print("  [" + slug + "] ERROR: " + str(e))
            return 1
    pub_dt = datetime.fromisoformat(video["published"].replace("Z", "+00:00"))
    entries.append({
        "video_id": video["id"], "title": video["title"],
        "description": video.get("description", ""),
        "published": pub_dt.isoformat(), "audio_url": audio_url,
        "file_size": file_size, "duration_secs": int(video.get("duration") or yt_duration or 0),
        "thumbnail": video.get("thumbnail", ""),
    })
    processed.setdefault(slug, []).append(video["id"])
    save_processed(processed)
    save_feed_entries(feed_path, entries)
    build_rss_feed(channel_cfg, channel_info, entries, feed_path)
    print("  [" + slug + "] Done.")
    return 0 if existing_url else 1

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=int, default=0)
    args = parser.parse_args()
    if args.budget <= 0:
        print("=== Backfill: no budget remaining, skipping. ===")
        return
    print("=== Backfill Run - budget: " + str(args.budget) + " slot(s) ===")
    print("Timestamp: " + datetime.now(timezone.utc).isoformat())
    channels = json.loads(Path("channels.json").read_text())
    processed = load_processed()
    state = load_backfill_state()
    enabled = [ch for ch in channels if ch.get("enabled", True) and ch.get("source") != "rss"]
    def is_done(slug):
        s = state.get(slug, {})
        todo = s.get("todo")
        if todo is None or todo:
            return False  # never scanned, or has work to do
        # todo is an empty list — done only if scanned recently enough
        last_scan = s.get("last_scan", "")
        if not last_scan:
            return False
        try:
            days_since = (datetime.utcnow() - datetime.fromisoformat(last_scan)).days
            return days_since < RESCAN_DAYS
        except Exception:
            return False
    if all(is_done(ch["slug"]) for ch in enabled):
        print("All channels fully backfilled.")
        return
    budget_remaining = args.budget
    slots_used = 0
    while budget_remaining > 0:
        if all(is_done(ch["slug"]) for ch in enabled):
            print("All channels fully backfilled.")
            break
        progress_this_pass = False
        for ch in enabled:
            if budget_remaining <= 0:
                break
            slug = ch["slug"]
            if is_done(slug):
                continue
            try:
                used = backfill_channel(ch, processed, state)
                if used:
                    slots_used += 1
                    budget_remaining -= 1
                    progress_this_pass = True
                    if budget_remaining > 0:
                        delay = random.uniform(15, 30)
                        print("  Waiting " + str(int(delay)) + "s before next download...")
                        time.sleep(delay)
                elif not is_done(slug):
                    progress_this_pass = True
            except Exception as e:
                print("  ERROR on " + slug + ": " + str(e))
        if not progress_this_pass:
            break
    print("" + chr(10) + "=== Backfill complete: " + str(slots_used) + " episode(s) added ===")

if __name__ == "__main__":
    main()
