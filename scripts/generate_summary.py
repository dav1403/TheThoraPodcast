"""
generate_summary.py
-------------------
Writes a markdown run summary to stdout (piped to $GITHUB_STEP_SUMMARY).
Compares /tmp/pre_run_counts.json (saved before the run) with current
entries.json files to show what was added. Queries YouTube API for total
video counts to estimate remaining backfill work.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import requests

API_KEY   = os.environ.get("YOUTUBE_API_KEY", "")
FEEDS_DIR = Path("feeds")


def load_pre_run_counts() -> dict:
    p = Path("/tmp/pre_run_counts.json")
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8-sig"))
    return {}


def get_channel_video_count(channel_id: str) -> int | None:
    if not API_KEY:
        return None
    try:
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"key": API_KEY, "id": channel_id, "part": "statistics"},
            timeout=10,
        )
        items = r.json().get("items", [])
        if items:
            return int(items[0]["statistics"].get("videoCount", 0))
    except Exception:
        pass
    return None


def main():
    channels = json.loads(Path("channels.json").read_text(encoding="utf-8-sig"))
    backfill_state = {}
    if Path("backfill_state.json").exists():
        content = Path("backfill_state.json").read_text(encoding="utf-8-sig").strip()
        if content:
            backfill_state = json.loads(content)

    pre_run = load_pre_run_counts()
    enabled = [ch for ch in channels if ch.get("enabled", True)]
    now     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [f"## Podcast Run Summary — {now}\n"]
    lines.append("| Channel | Added | In feed | Remaining on YT | Backfill |")
    lines.append("|---------|------:|--------:|----------------:|:--------:|")

    total_added = 0
    for ch in enabled:
        slug         = ch["slug"]
        entries_file = FEEDS_DIR / f"{slug}.entries.json"
        if not entries_file.exists():
            continue

        entries      = json.loads(entries_file.read_text(encoding="utf-8"))
        total_now    = len(entries)
        total_before = pre_run.get(slug, total_now)
        added        = total_now - total_before
        total_added += added

        ch_state  = backfill_state.get(slug, {})
        todo      = ch_state.get("todo")
        last_scan = ch_state.get("last_scan", "")
        if todo:  # non-empty list — actively backfilling
            bf_status = "⏳ ongoing"
        elif todo is None:  # never scanned
            bf_status = "⏳ ongoing"
        elif last_scan:
            try:
                from datetime import timedelta
                days_since = (datetime.now(timezone.utc) - datetime.fromisoformat(last_scan).replace(tzinfo=timezone.utc)).days
                bf_status = "✅ done" if days_since < 7 else "🔄 re-scan due"
            except Exception:
                bf_status = "⏳ ongoing"
        else:
            bf_status = "⏳ ongoing"

        yt_dlp_count = ch_state.get("yt_video_count")
        if yt_dlp_count is not None:
            remaining = f"{max(0, yt_dlp_count - total_now):,}"
        else:
            yt_total  = get_channel_video_count(ch["youtube_channel_id"])
            remaining = f"{yt_total - total_now:,}" if yt_total is not None else "?"

        added_str = f"**+{added}**" if added > 0 else "—"
        lines.append(f"| `{slug}` | {added_str} | {total_now} | {remaining} | {bf_status} |")

    lines.append(f"\n**Total added this run: {total_added} episode(s)**")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
