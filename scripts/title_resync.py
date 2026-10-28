"""
title_resync.py
---------------
Re-read on YouTube the titles of the episodes whose title is only a date.

A rav often publishes a class under a placeholder ("24 août 2026", YouTube's
default "28 באוקטובר 2026") and renames it later. The pipeline reads a title
once, at ingestion (process_podcasts.py / backfill_podcasts.py), so without
this pass the placeholder stays forever in the RSS feed (hence Spotify/Apple),
the episode page, the search index and the mobile app.

Each run (cheap: one videos.list call per 50 date-titled episodes, ~4 quota
units for the whole catalog, plus one channels.list call per channel that
actually changed):
  1. collect the entries whose title is a date (scripts/date_titles.py) in the
     feeds/<slug>.entries.json of every enabled YouTube channel (speaker feeds
     are derived by the generator and never touched; RSS-sourced channels have
     no YouTube video);
  2. ask YouTube for their current metadata;
  3. when the YouTube title is no longer a date: update `title` (and
     `description` when YouTube has one) in the entry, keep the previous page
     file name in `page_aliases` (the generator writes a redirect stub there,
     see generate_channel_pages.write_episode_alias_stubs) and rebuild that
     channel's RSS. The RSS <guid> is the video id, so platforms update the
     episode in place - no duplicate.
A video absent from the answer (deleted, private) or still titled with a date
is left untouched. `published`, `thumbnail`, audio... are never modified.

The decision logic (plan_resync / apply_rename / resync_entries) is stdlib-only
and unit-tested (tests/test_title_resync.py); the YouTube and RSS helpers come
from process_podcasts.py, imported inside main() only (it needs feedgen,
yt-dlp, boto3 and the pipeline secrets at import time).

Never fatal for the pipeline: any error is printed and the script exits 0
(the workflow step also carries a `|| echo WARNING` guard).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Iterable

from date_titles import is_date_title
from generate_channel_pages import ep_filename

CHANNELS_FILE = Path("channels.json")
FEEDS_DIR = Path("feeds")


def resync_channels(channels: list[dict]) -> list[dict]:
    """Channels whose entries come from YouTube (same filter as process_podcasts.main)."""
    return [ch for ch in channels
            if ch.get("enabled", True) and ch.get("source") != "rss" and ch.get("slug")]


def date_titled(entries: Iterable[dict]) -> list[dict]:
    """Entries whose title is a date placeholder and that can be looked up."""
    return [e for e in entries if e.get("video_id") and is_date_title(e.get("title"))]


def plan_resync(entries: list[dict], yt_by_id: dict[str, dict]) -> list[tuple[dict, str, str]]:
    """(entry, new_title, new_description) for each date-titled entry renamed on YouTube.

    yt_by_id maps a video id to the metadata returned by
    process_podcasts.fetch_video_metadata ({"title", "description", ...}).
    An id missing from it (deleted / private video) is left alone, as is a
    YouTube title that is still a date or identical to the stored one."""
    plan = []
    for entry in date_titled(entries):
        video = yt_by_id.get(entry["video_id"])
        if not video:
            continue
        new_title = (video.get("title") or "").strip()
        if not new_title or is_date_title(new_title) or new_title == (entry.get("title") or "").strip():
            continue
        plan.append((entry, new_title, video.get("description") or ""))
    return plan


def apply_rename(entry: dict, new_title: str, new_description: str, ch_slug: str) -> str | None:
    """Rename one entry in place. Returns the previous page file name when the
    page moves (it is then recorded in `page_aliases`), else None."""
    old_name = ep_filename(entry, ch_slug) if entry.get("published") else None
    entry["title"] = new_title
    if new_description.strip():
        entry["description"] = new_description
    if not old_name:
        return None
    new_name = ep_filename(entry, ch_slug)
    aliases = [a for a in (entry.get("page_aliases") or []) if a != new_name]
    moved = old_name != new_name
    if moved and old_name not in aliases:
        aliases.append(old_name)
    if aliases:
        entry["page_aliases"] = aliases
    else:
        entry.pop("page_aliases", None)
    return old_name if moved else None


def resync_entries(entries: list[dict], ch_slug: str, yt_by_id: dict[str, dict]) -> list[dict]:
    """Apply every rename of one channel; returns a log record per renamed entry."""
    changes = []
    for entry, new_title, new_desc in plan_resync(entries, yt_by_id):
        old_title = entry.get("title", "")
        old_page = apply_rename(entry, new_title, new_desc, ch_slug)
        changes.append({"video_id": entry["video_id"], "old_title": old_title,
                        "new_title": new_title, "old_page": old_page})
    return changes


def run(channels: list[dict], feeds_dir: Path,
        fetch_metadata: Callable[[list[str]], list[dict]],
        on_channel_changed: Callable[[dict, list[dict]], None]) -> dict:
    """The whole pass, I/O injected (tests pass fakes).

    fetch_metadata(video_ids) -> list of metadata dicts with an "id" key.
    on_channel_changed(channel_cfg, entries) is called AFTER the channel's
    entries.json has been saved, to rebuild its RSS feed."""
    per_channel: list[tuple[dict, Path, list[dict]]] = []
    ids: list[str] = []
    for ch in resync_channels(channels):
        path = feeds_dir / f"{ch['slug']}.entries.json"
        if not path.exists():
            continue
        entries = json.loads(path.read_text(encoding="utf-8"))
        candidates = date_titled(entries)
        if candidates:
            per_channel.append((ch, path, entries))
            ids += [e["video_id"] for e in candidates]

    summary = {"checked": len(ids), "channels_checked": len(per_channel),
               "found_on_youtube": 0, "renamed": 0, "by_channel": {}, "changes": []}
    if not ids:
        return summary

    # dict.fromkeys: one lookup per video even if two channels carry it.
    yt_by_id = {v["id"]: v for v in fetch_metadata(list(dict.fromkeys(ids))) if v.get("id")}
    summary["found_on_youtube"] = len(yt_by_id)

    for ch, path, entries in per_channel:
        changes = resync_entries(entries, ch["slug"], yt_by_id)
        if not changes:
            continue
        path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
        summary["renamed"] += len(changes)
        summary["by_channel"][ch["slug"]] = len(changes)
        summary["changes"] += [dict(c, slug=ch["slug"]) for c in changes]
        on_channel_changed(ch, entries)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    print("=== Title resync (date-only titles renamed on YouTube) ===")
    try:
        channels = json.loads(CHANNELS_FILE.read_text(encoding="utf-8-sig"))
        import process_podcasts as pp  # needs the pipeline env + deps: CI only

        def rebuild_rss(ch: dict, entries: list[dict]) -> None:
            try:
                info = pp.get_channel_info(ch["youtube_channel_id"])
                pp.build_rss_feed(ch, info, entries, FEEDS_DIR / f"{ch['slug']}.xml")
            except Exception as exc:  # entries are saved; the next process run rebuilds the RSS
                print(f"  WARNING: {ch['slug']}: RSS rebuild failed ({exc}); "
                      "entries.json is updated, the next pipeline run rewrites the feed")

        summary = run(channels, FEEDS_DIR, pp.fetch_video_metadata, rebuild_rss)
    except (Exception, SystemExit) as exc:  # process_podcasts sys.exit()s on missing env
        print(f"WARNING: title resync skipped: {type(exc).__name__}: {exc}")
        return 0

    for c in summary["changes"]:
        print(f"  [{c['slug']}] {c['video_id']}: {c['old_title']!r} -> {c['new_title']!r}"
              + (f" (old page {c['old_page']} kept as a redirect)" if c["old_page"] else ""))
    print(f"title resync: checked={summary['checked']} date-titled episodes in "
          f"{summary['channels_checked']} channel(s), found on YouTube={summary['found_on_youtube']}, "
          f"renamed={summary['renamed']} {summary['by_channel'] or ''}".rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
