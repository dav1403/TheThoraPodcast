import argparse, json, os, random, sys, time
from datetime import datetime, timezone
from pathlib import Path
import requests
from yt_dlp import YoutubeDL
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))
from process_podcasts import (
    get_channel_info, upload_audio_to_r2, asset_exists_in_r2,
    download_audio, build_rss_feed, load_feed_entries, save_feed_entries,
    load_processed, save_processed, FEEDS_DIR, AUDIO_DIR, API_KEY, GITHUB_REPO,
)

BACKFILL_STATE_FILE = Path("backfill_state.json")

def load_backfill_state():
    if BACKFILL_STATE_FILE.exists():
        content = BACKFILL_STATE_FILE.read_text().strip()
        return json.loads(content) if content else {}
    return {}

def save_backfill_state(state):
    BACKFILL_STATE_FILE.write_text(json.dumps(state, indent=2))

def discover_missing_videos(channel_id, already_done):
    channel_url = "https://www.youtube.com/channel/" + channel_id + "/videos"
    ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "ignoreerrors": True}
    cookies_file = os.environ.get("YOUTUBE_COOKIES_FILE")
    if cookies_file and Path(cookies_file).exists():
        ydl_opts["cookiefile"] = cookies_file
    print("    Scanning channel via yt-dlp (may take a moment for large channels)...")
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
        all_ids = [e["id"] for e in (info.get("entries") or []) if e.get("id")]
        missing = [v for v in all_ids if v not in already_done]
        missing.reverse()
        print("    " + str(len(all_ids)) + " total on channel, " + str(len(missing)) + " not yet in feed.")
        return missing
    except Exception as e:
        print("    yt-dlp discovery error: " + str(e))
        return []

def get_video_info(video_id):
    if not API_KEY:
        return None
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/videos",
            params={"key": API_KEY, "id": video_id, "part": "snippet"}, timeout=10)
        items = r.json().get("items", [])
        if not items:
            return None
        snippet = items[0]["snippet"]
        thumbs = snippet.get("thumbnails", {})
        return {
            "id": video_id, "title": snippet["title"],
            "description": snippet.get("description", ""),
            "published": snippet["publishedAt"],
            "thumbnail": (thumbs.get("maxres") or thumbs.get("high") or {}).get("url", ""),
            "url": "https://www.youtube.com/watch?v=" + video_id,
        }
    except Exception:
        return None

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
    if todo is None:
        if ch_state.get("exhausted"):
            return 0
        todo = discover_missing_videos(channel_id, already_done)
        if not todo:
            ch_state["exhausted"] = True
            save_backfill_state(state)
            print("  [" + slug + "] No missing videos found.")
            return 0
        ch_state["todo"] = todo
        save_backfill_state(state)
    else:
        todo = [v for v in todo if v not in already_done]
        if not todo:
            ch_state.pop("todo", None)
            ch_state["exhausted"] = True
            save_backfill_state(state)
            print("  [" + slug + "] Todo list empty.")
            return 0
        ch_state["todo"] = todo
    video_id = todo[0]
    ch_state["todo"] = todo[1:]
    save_backfill_state(state)
    video = get_video_info(video_id)
    if not video:
        print("  [" + slug + "] Video " + video_id + " unavailable (private/deleted).")
        processed.setdefault(slug, []).append(video_id)
        save_processed(processed)
        return 0
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
            mp3_path = download_audio(video["url"], AUDIO_DIR)
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
        "file_size": file_size, "duration_secs": 0,
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
    enabled = [ch for ch in channels if ch.get("enabled", True)]
    def is_done(slug):
        s = state.get(slug, {})
        return s.get("exhausted") and "todo" not in s
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
