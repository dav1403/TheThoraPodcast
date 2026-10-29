"""
title_resync.py
---------------
Re-read on YouTube the titles of our episodes and apply the renames.

The pipeline reads a title once, at ingestion (process_podcasts.py,
backfill_podcasts.py, bootstrap_channel.py: all store
`html.unescape(snippet["title"])` from videos.list). A rav who renames a class
on YouTube later - typically a placeholder ("24 août 2026", YouTube's default
"28 באוקטובר 2026") replaced by the real subject, but any rename - left the
old title forever in the RSS feed (hence Spotify/Apple), the episode page, the
search index and the mobile app. This pass closes that gap.

Two modes (env TITLE_RESYNC_MODE, the workflow chooses):
  dates  (default) the original pass: every date-titled episode, every run
         (~160 videos, ~4 quota units);
  all    every episode, paced:
           * "hot" pool = published in the last TITLE_RESYNC_RECENT_DAYS days
             (default 60) + every date-titled episode: all of it, every run;
           * "cold" pool = the rest of the catalog: a rotating slice of
             TITLE_RESYNC_SLICE ids (default 250) per run. The ids are sorted
             (video id order, which mixes the channels) and the cursor is the
             last id checked, persisted in feeds/title_resync_state.json (the
             R2 state, scripts/site_state.py mirrors all of feeds/). New
             episodes therefore never shift it.
         ~1 150 hot ids (23 units) + 250 cold ids (5 units) = ~28 quota units
         per run (videos.list = 1 unit per 50 ids), + 1 channels.list per
         channel whose RSS is rebuilt. A tour of the ~38 500 episodes takes
         ~155 runs, i.e. 2-3 weeks at the observed 10-12 runs/day.

What counts as a rename - never a false one:
  both titles go through title_key() (HTML entities, Unicode NFC, invisible
  format characters such as LRM/RLM/ZWJ, runs of whitespace and the edges) and
  only a different key is a rename. ~4 000 stored titles carry double or edge
  spaces exactly as YouTube returns them, 71 carry LRM/RLM marks: none of them
  is a rename. The NEW title is stored exactly as ingestion would store it
  (html.unescape of the API title, nothing else), so the next pass is a no-op.
  Never renamed:
    * a video absent from the answer (deleted, private) or live/upcoming;
    * a new title that is a date placeholder (LOG-04 would hide the episode);
    * an entry carrying "title_locked": true - the hook for a title we rewrite
      by hand on purpose (none exists as of 29/10/2026: no code or config
      rewrites a title, see channels.json / speakers.json).

On a rename:
  * `title` = the YouTube title (as ingested);
  * `description` = the YouTube description when it has one. Safe by
    construction: `description` only ever holds the raw YouTube description
    written at ingestion (no code writes anything else into it); the
    hand-written / anti-promo SEO text is computed at render time from it
    (generate_channel_pages.episode_description_fields), never stored.
    An empty YouTube description keeps ours. A description change alone (title
    unchanged) is never applied: ravs edit descriptions constantly, that would
    rewrite entries.json and the RSS of every channel on every run;
  * `tags` is dropped: they were derived from the old title, the next step
    (tag_episodes.py, tags only entries without the key) re-tags the episode;
  * the previous page file name goes to `page_aliases` (the generator writes a
    redirect stub there, see generate_channel_pages.write_episode_alias_stubs)
    and the channel RSS is rebuilt. The RSS <guid> is the video id, so the
    platforms update the episode in place - no duplicate.
`published`, `thumbnail`, audio... are never modified.

Anti-mass guard: when one run finds more than TITLE_RESYNC_MAX_RENAMES renames
(default 50) it applies NOTHING, does not advance the cursor, prints the count
and 10 examples, and raises a ::warning:: (+ the job summary). It repeats on
every run until a human looks - raise the limit for one run, or lock the
titles. It also holds back the hot pool while it trips: a brake, by design.

Local passage-a-blanc (read only, nothing written):
    py scripts/title_resync.py --dry-run --all --feeds-dir <state>/feeds \\
        --report out.json [--source oembed --workers 4 --cache cache.jsonl]
--source api needs YOUTUBE_API_KEY; oembed (title only, no key) reads
https://www.youtube.com/oembed per video - 401 there also means "embedding
disabled", so its "unavailable" count is an upper bound.

The decision logic (title_key / plan_resync / select_ids / run) is stdlib-only
and unit-tested (tests/test_title_resync.py); the RSS helpers come from
process_podcasts.py, imported inside main() only (it needs feedgen, yt-dlp,
boto3 and the pipeline secrets at import time).

Never fatal for the pipeline: any error is printed and the script exits 0
(the workflow step also carries a `|| echo WARNING` guard). A failed YouTube
call applies nothing and leaves the cursor where it was.
"""
from __future__ import annotations

import argparse
import bisect
import html
import json
import os
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

from date_titles import is_date_title
from generate_channel_pages import ep_filename

CHANNELS_FILE = Path("channels.json")
FEEDS_DIR = Path("feeds")
STATE_NAME = "title_resync_state.json"

DEFAULT_MODE = "dates"
DEFAULT_RECENT_DAYS = 60
DEFAULT_SLICE = 250
DEFAULT_MAX_RENAMES = 50
API_BATCH = 50  # videos.list: up to 50 ids for 1 quota unit
GUARD_EXAMPLES = 10


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------

def as_ingested(raw_title: str) -> str:
    """The exact transformation ingestion applies to snippet.title
    (process_podcasts.fetch_video_metadata / fetch_video_status)."""
    return html.unescape(raw_title or "")


def title_key(title) -> str:
    """Comparison key: two titles with the same key are the same title.

    HTML entities decoded, Unicode NFC, invisible format characters (Cf: LRM,
    RLM, ZWJ, BOM...) removed, every run of whitespace (NBSP included) made one
    space, edges stripped. For comparison only - never stored."""
    t = unicodedata.normalize("NFC", html.unescape(title or ""))
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Cf")
    return " ".join(t.split())


def change_kind(old: str, new: str) -> str:
    """Rough class of a rename, for the reports (a whitespace-only class would
    mean title_key is wrong: it cannot happen)."""
    a, b = title_key(old), title_key(new)
    if a == b:
        return "same"
    if a.casefold() == b.casefold():
        return "case"
    alnum = lambda s: "".join(ch for ch in s.casefold() if ch.isalnum())  # noqa: E731
    if alnum(a) == alnum(b):
        return "punctuation"
    if b.startswith(a) or a.startswith(b):
        return "extended_or_cut"
    if is_date_title(old):
        return "date_to_subject"
    return "rewrite"


# --------------------------------------------------------------------------
# Decision logic (pure)
# --------------------------------------------------------------------------

def resync_channels(channels: list[dict]) -> list[dict]:
    """Channels whose entries come from YouTube (same filter as process_podcasts.main)."""
    return [ch for ch in channels
            if ch.get("enabled", True) and ch.get("source") != "rss" and ch.get("slug")]


def date_titled(entries: Iterable[dict]) -> list[dict]:
    """Entries whose title is a date placeholder and that can be looked up."""
    return [e for e in entries if e.get("video_id") and is_date_title(e.get("title"))]


def plan_resync(entries: list[dict], yt_by_id: dict[str, dict]) -> list[tuple[dict, str, str]]:
    """(entry, new_title, new_description) for each entry renamed on YouTube.

    yt_by_id maps a video id to {"title", "description"} already transformed
    like ingestion (see as_ingested). An id missing from it (deleted, private,
    live, or simply not checked this run) is left alone."""
    plan = []
    for entry in entries:
        vid = entry.get("video_id")
        video = yt_by_id.get(vid) if vid else None
        if not video or entry.get("title_locked"):
            continue
        new_title = video.get("title") or ""
        new_key = title_key(new_title)
        if not new_key or new_key == title_key(entry.get("title")) or is_date_title(new_key):
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
    entry.pop("tags", None)  # derived from the old title: re-tagged next step
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


def _published(entry: dict) -> datetime | None:
    try:
        dt = datetime.fromisoformat((entry.get("published") or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def split_pools(entries: Iterable[dict], now: datetime, recent_days: int) -> tuple[list[str], list[str]]:
    """(hot ids, cold ids). Hot = published in the last `recent_days` days, or
    titled with a date (the original resync, kept on every run)."""
    cutoff = now - timedelta(days=recent_days)
    hot, cold = [], []
    for e in entries:
        vid = e.get("video_id")
        if not vid:
            continue
        pub = _published(e)
        if is_date_title(e.get("title")) or (pub is not None and pub >= cutoff):
            hot.append(vid)
        else:
            cold.append(vid)
    return hot, cold


def select_slice(cold_ids: Iterable[str], cursor: str | None, size: int) -> tuple[list[str], str | None, bool]:
    """Next `size` ids after `cursor` in sorted order, wrapping around.

    Returns (ids, new cursor, wrapped). The cursor is an id, not an index:
    episodes added or aged into the pool never shift it."""
    pool = sorted(set(cold_ids))
    if not pool or size <= 0:
        return [], cursor, False
    size = min(size, len(pool))
    start = bisect.bisect_right(pool, cursor) if cursor else 0
    take = pool[start:start + size]
    wrapped = False
    if len(take) < size or start >= len(pool):
        wrapped = True
        take = take + pool[:size - len(take)]
    return take, take[-1], wrapped


def run(channels: list[dict], feeds_dir: Path,
        fetch_metadata: Callable[[list[str]], dict[str, dict]],
        on_channel_changed: Callable[[dict, list[dict]], None],
        *, mode: str = DEFAULT_MODE, state: dict | None = None, now: datetime | None = None,
        recent_days: int = DEFAULT_RECENT_DAYS, slice_size: int = DEFAULT_SLICE,
        max_renames: int = DEFAULT_MAX_RENAMES, check_all: bool = False,
        dry_run: bool = False) -> dict:
    """The whole pass, I/O injected (tests pass fakes).

    fetch_metadata(video_ids) -> {video_id: {"title", "description"}} (only the
    videos YouTube answered for; raises on an API error).
    on_channel_changed(channel_cfg, entries) is called AFTER the channel's
    entries.json has been saved, to rebuild its RSS feed.
    Returns a summary; summary["state"] is the state to persist (unchanged
    when nothing may advance: dry run, guard tripped)."""
    now = now or datetime.now(timezone.utc)
    state = dict(state or {})
    per_channel: list[tuple[dict, Path, list[dict]]] = []
    all_entries: list[dict] = []
    for ch in resync_channels(channels):
        path = feeds_dir / f"{ch['slug']}.entries.json"
        if not path.exists():
            continue
        entries = json.loads(path.read_text(encoding="utf-8"))
        per_channel.append((ch, path, entries))
        all_entries += entries

    summary = {"mode": "all" if check_all else mode, "dry_run": dry_run, "hot": 0, "cold": 0,
               "checked": 0, "units": 0, "found_on_youtube": 0, "renamed": 0,
               "by_channel": {}, "changes": [], "guard_tripped": False,
               "max_renames": max_renames, "tour_completed": False, "state": state}

    new_state = dict(state)
    if check_all:
        ids = [e["video_id"] for e in all_entries if e.get("video_id")]
    elif mode == "all":
        hot, cold = split_pools(all_entries, now, recent_days)
        cold_slice, cursor, wrapped = select_slice(cold, state.get("cursor"), slice_size)
        ids = hot + cold_slice
        summary.update(hot=len(hot), cold=len(cold_slice), cold_pool=len(set(cold)))
        new_state["cursor"] = cursor
        new_state.setdefault("tour_started_at", now.isoformat(timespec="seconds"))
        if wrapped:
            summary["tour_completed"] = True
            new_state["tours_completed"] = int(state.get("tours_completed", 0)) + 1
            new_state["last_tour_completed_at"] = now.isoformat(timespec="seconds")
            new_state["tour_started_at"] = now.isoformat(timespec="seconds")
    else:
        ids = [e["video_id"] for e in date_titled(all_entries)]
        summary["hot"] = len(ids)

    ids = list(dict.fromkeys(ids))  # one lookup per video even if two channels carry it
    summary["checked"] = len(ids)
    summary["units"] = -(-len(ids) // API_BATCH)
    if not ids:
        return summary

    yt_by_id = fetch_metadata(ids)
    summary["found_on_youtube"] = len(yt_by_id)

    planned = []
    for ch, path, entries in per_channel:
        plan = plan_resync(entries, yt_by_id)
        if plan:
            planned.append((ch, path, entries, len(plan)))
            summary["by_channel"][ch["slug"]] = len(plan)
        summary["changes"] += [{"slug": ch["slug"], "video_id": e["video_id"],
                                "old_title": e.get("title", ""), "new_title": t,
                                "kind": change_kind(e.get("title", ""), t),
                                "published": (e.get("published") or "")[:10]}
                               for e, t, _ in plan]
    summary["renamed"] = len(summary["changes"])

    if dry_run:
        return summary
    if summary["renamed"] > max_renames:
        summary["guard_tripped"] = True
        return summary  # nothing written, cursor not advanced

    for ch, path, entries, _ in planned:
        changes = resync_entries(entries, ch["slug"], yt_by_id)
        old_pages = {c["video_id"]: c["old_page"] for c in changes}
        for c in summary["changes"]:
            if c["slug"] == ch["slug"] and c["video_id"] in old_pages:
                c["old_page"] = old_pages[c["video_id"]]
        path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
        on_channel_changed(ch, entries)

    new_state["last_run_at"] = now.isoformat(timespec="seconds")
    new_state["last_run"] = {k: summary[k] for k in ("mode", "checked", "units", "found_on_youtube",
                                                     "renamed", "by_channel")}
    summary["state"] = new_state
    return summary


# --------------------------------------------------------------------------
# YouTube I/O
# --------------------------------------------------------------------------

def _get_json(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "thetorahpodcast-title-resync"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_api(video_ids: list[str], api_key: str, get_json: Callable[[str], dict] = _get_json) -> dict[str, dict]:
    """videos.list (part=snippet, 1 unit per 50 ids), same transformation as
    ingestion. Live/upcoming videos are left out (their title is provisional).
    Raises on any API error: the caller then applies nothing."""
    out: dict[str, dict] = {}
    for i in range(0, len(video_ids), API_BATCH):
        chunk = video_ids[i:i + API_BATCH]
        query = urllib.parse.urlencode({"key": api_key, "id": ",".join(chunk),
                                        "part": "snippet", "maxResults": API_BATCH})
        try:
            data = get_json("https://www.googleapis.com/youtube/v3/videos?" + query)
        except urllib.error.HTTPError as exc:  # quota / key errors come back as 4xx
            body = exc.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"YouTube API HTTP {exc.code}: {body}") from None
        if "error" in data:
            raise RuntimeError(f"YouTube API error: {data['error'].get('message')}")
        for item in data.get("items", []):
            snippet = item.get("snippet") or {}
            if not item.get("id") or snippet.get("liveBroadcastContent", "none") in ("live", "upcoming"):
                continue
            out[item["id"]] = {"title": as_ingested(snippet.get("title", "")),
                               "description": html.unescape(snippet.get("description", ""))}
    return out


def fetch_oembed(video_ids: list[str], *, workers: int = 1, pause: float = 0.2,
                 cache_path: Path | None = None, status: dict | None = None) -> dict[str, dict]:
    """Title only, no key (passage-a-blanc). Resumable through a JSONL cache.
    `status` (optional) receives video_id -> HTTP status (200, 401, 404...)."""
    status = status if status is not None else {}
    done: dict[str, dict] = {}
    if cache_path and cache_path.exists():
        for line in cache_path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("status") in (200, 400, 401, 403, 404):
                done[rec["id"]] = rec
    todo = [v for v in video_ids if v not in done]
    lock = threading.Lock()
    sink = cache_path.open("a", encoding="utf-8") if cache_path else None
    progress = {"n": 0}

    def one(vid: str) -> None:
        url = ("https://www.youtube.com/oembed?format=json&url="
               + urllib.parse.quote(f"https://www.youtube.com/watch?v={vid}", safe=""))
        rec = {"id": vid, "status": None, "title": None}
        for attempt in range(6):
            try:
                data = _get_json(url)
                rec = {"id": vid, "status": 200, "title": data.get("title")}
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (400, 401, 403, 404):
                    rec = {"id": vid, "status": exc.code, "title": None}
                    break
                rec["status"] = exc.code
            except Exception as exc:  # network blip
                rec["status"] = f"err:{type(exc).__name__}"
            time.sleep(min(60, 2 ** attempt * 2))
        time.sleep(pause)
        with lock:
            done[vid] = rec
            if sink:
                sink.write(json.dumps(rec, ensure_ascii=False) + "\n")
                sink.flush()
            progress["n"] += 1
            if progress["n"] % 1000 == 0:
                print(f"  oembed: {progress['n']}/{len(todo)}", flush=True)

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(one, todo))
    finally:
        if sink:
            sink.close()
    out = {}
    for vid in video_ids:
        rec = done.get(vid) or {}
        status[vid] = rec.get("status")
        if rec.get("status") == 200 and rec.get("title") is not None:
            out[vid] = {"title": as_ingested(rec["title"]), "description": ""}
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _spread_examples(changes: list[dict], n: int) -> list[dict]:
    """Up to n examples, round-robin over the channels."""
    by: dict[str, list[dict]] = {}
    for c in changes:
        by.setdefault(c["slug"], []).append(c)
    out: list[dict] = []
    while len(out) < n and any(by.values()):
        for slug in list(by):
            if by[slug] and len(out) < n:
                out.append(by[slug].pop(0))
    return out


def _fmt(c: dict) -> str:
    return f"[{c['slug']}] {c['video_id']} ({c.get('published', '')}): {c['old_title']!r} -> {c['new_title']!r}"


def _report_guard(summary: dict) -> None:
    msg = (f"title resync: {summary['renamed']} renames detected (> {summary['max_renames']}), "
           "NOTHING applied, cursor not advanced - review, then raise TITLE_RESYNC_MAX_RENAMES "
           "for one run or lock the titles")
    print(f"::warning title=Title resync blocked::{msg}")
    examples = _spread_examples(summary["changes"], GUARD_EXAMPLES)
    for c in examples:
        print("  " + _fmt(c))
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        try:
            with open(step_summary, "a", encoding="utf-8") as f:
                f.write(f"### Title resync blocked\n\n{msg}\n\nBy channel: {summary['by_channel']}\n\n")
                for c in examples:
                    f.write(f"- `{c['slug']}` {c['video_id']}: {c['old_title']} → {c['new_title']}\n")
                f.write("\n")
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="read only: nothing is written")
    parser.add_argument("--all", action="store_true", help="check every episode (no cursor)")
    parser.add_argument("--mode", choices=("dates", "all"),
                        default=os.environ.get("TITLE_RESYNC_MODE") or DEFAULT_MODE)
    parser.add_argument("--feeds-dir", type=Path, default=FEEDS_DIR)
    parser.add_argument("--channels", type=Path, default=CHANNELS_FILE)
    parser.add_argument("--source", choices=("auto", "api", "oembed"), default="auto")
    parser.add_argument("--workers", type=int, default=1, help="oembed only")
    parser.add_argument("--cache", type=Path, help="oembed only: resumable JSONL cache")
    parser.add_argument("--report", type=Path, help="write the summary as JSON")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if args.all and not args.dry_run:
        parser.error("--all is a passage-a-blanc tool: use it with --dry-run")

    recent_days = _env_int("TITLE_RESYNC_RECENT_DAYS", DEFAULT_RECENT_DAYS)
    slice_size = _env_int("TITLE_RESYNC_SLICE", DEFAULT_SLICE)
    max_renames = _env_int("TITLE_RESYNC_MAX_RENAMES", DEFAULT_MAX_RENAMES)
    state_path = args.feeds_dir / STATE_NAME
    mode = "all" if args.all else args.mode
    print(f"=== Title resync (mode={mode}{', dry run' if args.dry_run else ''}) ===")

    oembed_status: dict[str, object] = {}
    try:
        channels = json.loads(args.channels.read_text(encoding="utf-8-sig"))
        api_key = os.environ.get("YOUTUBE_API_KEY", "")
        source = args.source if args.source != "auto" else ("api" if api_key else "oembed")
        if source == "oembed" and not args.dry_run:
            raise RuntimeError("oembed has no descriptions nor live status: dry runs only")
        if source == "api" and not api_key:
            raise RuntimeError("YOUTUBE_API_KEY is not set")
        if source == "api":
            def fetch(ids: list[str]) -> dict[str, dict]:
                return fetch_api(ids, api_key)
        else:
            def fetch(ids: list[str]) -> dict[str, dict]:
                return fetch_oembed(ids, workers=args.workers, cache_path=args.cache, status=oembed_status)
        print(f"source={source} recent_days={recent_days} slice={slice_size} max_renames={max_renames}")

        if args.dry_run:
            def on_changed(ch: dict, entries: list[dict]) -> None:
                raise AssertionError("dry run must not write")
        else:
            import process_podcasts as pp  # needs the pipeline env + deps: CI only

            def on_changed(ch: dict, entries: list[dict]) -> None:
                try:
                    info = pp.get_channel_info(ch["youtube_channel_id"])
                    pp.build_rss_feed(ch, info, entries, args.feeds_dir / f"{ch['slug']}.xml")
                except Exception as exc:  # entries are saved; the next process run rebuilds the RSS
                    print(f"  WARNING: {ch['slug']}: RSS rebuild failed ({exc}); "
                          "entries.json is updated, the next pipeline run rewrites the feed")

        summary = run(channels, args.feeds_dir, fetch, on_changed, mode=mode,
                      state=load_state(state_path), recent_days=recent_days, slice_size=slice_size,
                      max_renames=max_renames, check_all=args.all, dry_run=args.dry_run)
    except (Exception, SystemExit) as exc:  # process_podcasts sys.exit()s on missing env
        print(f"::warning title=Title resync skipped::{type(exc).__name__}: {exc}")
        return 0

    if summary["guard_tripped"]:
        _report_guard(summary)
    elif not args.dry_run:
        if summary["mode"] == "all":
            state_path.write_text(json.dumps(summary["state"], indent=2, ensure_ascii=False) + "\n",
                                  encoding="utf-8")
        for c in summary["changes"]:
            print("  " + _fmt(c) + (f" (old page {c['old_page']} kept as a redirect)" if c.get("old_page") else ""))

    if args.dry_run:
        answered = summary["found_on_youtube"]
        unavailable = summary["checked"] - answered
        kinds: dict[str, int] = {}
        for c in summary["changes"]:
            kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
        statuses: dict[str, int] = {}
        for s in oembed_status.values():
            statuses[str(s)] = statuses.get(str(s), 0) + 1
        summary.update(unavailable=unavailable, kinds=kinds, oembed_status=statuses,
                       examples=_spread_examples(summary["changes"], 20))
        print(f"dry run: checked={summary['checked']} answered={answered} unavailable={unavailable} "
              f"renamed={summary['renamed']} kinds={kinds} statuses={statuses or '-'}")
        print(f"by channel: {summary['by_channel']}")
        for c in summary["examples"]:
            print("  " + _fmt(c))
    print(f"title resync: mode={summary['mode']} checked={summary['checked']} (hot={summary['hot']}, "
          f"cold={summary['cold']}) units~{summary['units']} found={summary['found_on_youtube']} "
          f"renamed={summary['renamed']}{' BLOCKED by the guard' if summary['guard_tripped'] else ''}"
          + (" tour completed" if summary["tour_completed"] else "")
          + (f" {summary['by_channel']}" if summary["by_channel"] else ""))
    if args.report:
        rep = {k: v for k, v in summary.items() if k != "state"}
        args.report.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
