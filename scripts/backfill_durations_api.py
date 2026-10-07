"""Fill missing `duration_secs` in feeds/<slug>.entries.json via the YouTube API.

Phase 4: was inline Python in a 14-job matrix where every job committed its
own entries.json to git. The state now lives in R2, so the workflow runs ONE
job under the state lease: restore the entries, fill them here, partial push.

Writes a Markdown report to $GITHUB_STEP_SUMMARY (or stdout).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from feeds_util import channel_entry_files  # noqa: E402

BATCH = 50


def parse_iso8601(s: str) -> int:
    """PT1H2M3S -> seconds"""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)


def fetch_batch(video_ids: list[str], api_key: str) -> dict[str, int]:
    url = ("https://www.googleapis.com/youtube/v3/videos"
           f"?id={urllib.parse.quote(','.join(video_ids))}&part=contentDetails&key={api_key}")
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:  # one bad batch must not stop the others
        print(f"  API error: {type(e).__name__}", file=sys.stderr)
        return {}
    return {item["id"]: parse_iso8601(item["contentDetails"]["duration"]) for item in data.get("items", [])}


def fill(entries: list[dict], api_key: str) -> int:
    missing = [ep["video_id"] for ep in entries if not ep.get("duration_secs") and ep.get("video_id")]
    idx = {ep["video_id"]: ep for ep in entries if ep.get("video_id")}
    filled = 0
    for i in range(0, len(missing), BATCH):
        for vid, dur in fetch_batch(missing[i:i + BATCH], api_key).items():
            if dur > 0 and vid in idx:
                idx[vid]["duration_secs"] = dur
                filled += 1
    return filled


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feeds", default="feeds")
    ap.add_argument("slugs", nargs="+")
    args = ap.parse_args(argv)
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    feeds = Path(args.feeds)
    per_slug: dict[str, int] = {}
    for slug in args.slugs:
        f = feeds / f"{slug}.entries.json"
        if not f.exists():
            print(f"{slug}: no entries file, skipping.")
            continue
        entries = json.loads(f.read_text(encoding="utf-8"))
        n_missing = sum(1 for ep in entries if not ep.get("duration_secs") and ep.get("video_id"))
        print(f"{slug}: {n_missing} missing / {len(entries)} total")
        if not n_missing or not api_key:
            per_slug[slug] = 0
            continue
        filled = fill(entries, api_key)
        per_slug[slug] = filled
        if filled:
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(f)
            print(f"  saved: +{filled}")
    if not api_key:
        print("No YOUTUBE_API_KEY: nothing filled.", file=sys.stderr)

    rows, total_eps, total_missing = [], 0, 0
    for path in channel_entry_files(feeds):
        slug = Path(path).name.replace(".entries.json", "")
        try:
            eps = json.loads(Path(path).read_text(encoding="utf-8"))
        except ValueError:
            continue
        n, m = len(eps), sum(1 for e in eps if not e.get("duration_secs"))
        total_eps += n
        total_missing += m
        rows.append((slug, n - m, n, m, int(100 * (n - m) / n) if n else 0, per_slug.get(slug)))
    done = total_eps - total_missing
    pct = int(100 * done / total_eps) if total_eps else 0
    out = ["# Backfill durées — rapport\n",f"## Ce run : **+{sum(per_slug.values())} durées ajoutées**\n",
           f"## État global : {done}/{total_eps} ({pct}%)\n",
           "| Chaîne | +ce run | Complétés | Total | Manquants | % |", "|---|---|---|---|---|---|"]
    for slug, d, n, m, p, f in rows:
        st = "✅" if m == 0 else ("🔄" if p >= 50 else "⏳")
        out.append(f"| {st} {slug} | {'—' if f is None else f'+{f}'} | {d} | {n} | {m} | {p}% |")
    out.append(f"\n> Run du {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · {total_missing} épisodes encore sans durée")
    text = "\n".join(out) + "\n"
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
