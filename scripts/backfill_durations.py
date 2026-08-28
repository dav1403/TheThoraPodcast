#!/usr/bin/env python3
"""One-time backfill: fetch real duration for every entry that has duration_secs == 0."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from feeds_util import channel_entry_files

ROOT = Path(__file__).resolve().parent.parent
FEEDS = ROOT / "feeds"

cookies_file = os.environ.get("YOUTUBE_COOKIES_FILE", "")


def fetch_duration(video_id: str) -> int:
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = ["yt-dlp", "--print", "%(duration)s", "--skip-download", "--no-warnings"]
    if cookies_file and Path(cookies_file).exists():
        cmd += ["--cookies", cookies_file]
    cmd.append(url)
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=30).strip()
        return int(out) if out and out != "NA" else 0
    except Exception as e:
        print(f"    skipped ({e})", file=sys.stderr)
        return 0


total_updated = 0
# Channel feeds only: the speaker feeds duplicate these episodes, and their
# durations come along when the generator rebuilds them.
for entries_file in channel_entry_files(FEEDS, ROOT):
    entries = json.loads(entries_file.read_text(encoding="utf-8"))
    missing = [ep for ep in entries if not ep.get("duration_secs")]
    if not missing:
        print(f"{entries_file.name}: all durations present, skipping")
        continue

    print(f"{entries_file.name}: {len(missing)} episodes to backfill…")
    changed = False
    for ep in missing:
        vid = ep.get("video_id")
        if not vid:
            continue
        dur = fetch_duration(vid)
        if dur > 0:
            ep["duration_secs"] = dur
            changed = True
            total_updated += 1
            print(f"  {vid}: {dur}s")

    if changed:
        tmp = entries_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(entries_file)
        print(f"  → saved {entries_file.name}")

print(f"\nDone. {total_updated} episodes updated.")
