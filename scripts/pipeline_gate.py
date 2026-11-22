"""Early exit of "Update Podcast Feeds" when a scheduled run has nothing to do.

About 40 % of the runs ingest nothing but still pay ffmpeg/deno/pip, the state
restore, the page generation (~4 min) and the push. This gate decides in a
few seconds, at the very start of a scheduled run, whether the run can stop.

"Nothing new" is not "nothing to do": a run also backfills history, retries
failed downloads, re-reads renamed titles, tags, fetches transcripts,
rebuilds the search index, takes the daily snapshot, checks external feeds
and raises/closes alerts. So the gate does not try to predict that work. It
relies on what the LAST FULL RUN recorded at its end (`record`, R2
`<state prefix>_gate.json`, next to the manifest):

  idle      that run changed NOTHING in the state (its push was a no-op:
            same generation), had no backfill backlog, no channel error, no YouTube
            auth / Anthropic credit error, no search-index rebuild postponed.
            Any of these -> the next run is a full run.
  handled   per channel, the video ids of the channel's YouTube RSS (observed
            at the start of that run) that are in processed.json or
            entries.json at its end.
  rss       digest of each external (source=rss) feed, observed at the start.

`check` skips the run only if ALL of these hold (any doubt -> full run):
  - scheduled run (a workflow_dispatch always runs, `force` or not);
  - a record exists and says idle;
  - same source commit and same state generation as the record (no code
    change, no other writer since);
  - the record is from today (UTC: daily snapshot, daily maintenance) and
    younger than GATE_MAX_IDLE_HOURS (6 h: bounds the delay of the time-based
    work the gate cannot see - title renames, transcript retries, backfill
    rescans, channel info, health checks);
  - every channel's YouTube RSS was read, and every video id in it is either
    handled or not processable yet (live/upcoming, unknown to the API) -
    the same filter as process_podcasts.get_new_videos (fetch_video_metadata);
  - every external feed was read and its digest is unchanged.

The decision is fail-safe: any exception -> run=true.

CLI:
  check   --observed-out F   -> GITHUB_OUTPUT run=true|false, reason=...
  record  --observed F --push-unchanged ... -> writes the record (best effort)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

GATE_VERSION = 1
MAX_IDLE_HOURS = float(os.environ.get("GATE_MAX_IDLE_HOURS") or 6)
YT_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id="
UA = {"User-Agent": "TheTorahPodcast-gate/1.0"}


# --------------------------------------------------------------------------
# Observation (network, a few seconds)
# --------------------------------------------------------------------------

def youtube_feed_ids(channel_id: str, timeout: int = 20) -> list[str]:
    """Video ids of a channel's public RSS feed (15 newest uploads, shorts and
    lives included). Raises on any HTTP/parse problem."""
    req = urllib.request.Request(YT_FEED + channel_id, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    if "<feed" not in body:
        raise ValueError("not an Atom feed")
    ids = re.findall(r"<yt:videoId>([^<]+)</yt:videoId>", body)
    if not ids:  # an empty feed is a doubt (YouTube hiccup), never "nothing new"
        raise ValueError("empty feed")
    return ids


def rss_digest(rss_url: str) -> str:
    """Digest of the episodes of an external feed, parsed exactly like
    sync_rss_channels.py does."""
    import sync_rss_channels as srs
    _meta, entries = srs.parse_channel(srs.fetch_feed(rss_url))
    h = hashlib.sha256()
    for e in sorted(entries, key=lambda e: e["video_id"]):
        h.update(json.dumps([e["video_id"], e["title"], e["audio_url"], e["file_size"],
                             e["duration_secs"]], ensure_ascii=False).encode())
    return f"{len(entries)}:{h.hexdigest()[:16]}"


def enabled_channels(channels: list[dict]) -> list[dict]:
    return [c for c in channels if c.get("enabled", True)]


def observe(channels: list[dict]) -> dict:
    out: dict = {"youtube": {}, "rss": {}, "errors": []}

    def one(ch):
        slug = ch["slug"]
        try:
            if ch.get("source") == "rss":
                return "rss", slug, rss_digest(ch["rss_url"])
            return "youtube", slug, youtube_feed_ids(ch["youtube_channel_id"])
        except Exception as exc:
            # Type only: the repo and its logs are public, and an error text
            # can carry a URL with an API key.
            return "error", slug, type(exc).__name__

    with ThreadPoolExecutor(max_workers=8) as ex:
        for kind, slug, val in ex.map(one, enabled_channels(channels)):
            if kind == "error":
                out["errors"].append(f"{slug}: {val}")
            else:
                out[kind][slug] = val
    return out


def processable(video_ids: list[str]) -> list[str]:
    """The ids process_podcasts would ingest now: its own metadata call and
    live/upcoming filter (ids unknown to the API are dropped there too)."""
    if not video_ids:
        return []
    import process_podcasts as pp  # needs the pipeline env (API key...): CI only
    return [v["id"] for v in pp.fetch_video_metadata(list(video_ids))]


# --------------------------------------------------------------------------
# Decision (pure)
# --------------------------------------------------------------------------

def unknown_ids(observed: dict, record: dict) -> dict[str, list[str]]:
    handled = record.get("handled") or {}
    out = {}
    for slug, ids in (observed.get("youtube") or {}).items():
        known = set(handled.get(slug) or [])
        new = [i for i in ids if i not in known]
        if new:
            out[slug] = new
    return out


def decide(record: dict | None, *, event: str, head_sha: str, manifest_etag: str,
           now: datetime, observed: dict | None, channels: list[dict],
           processable_fn=processable) -> tuple[bool, str]:
    """(run?, reason)."""
    if event != "schedule":
        return True, f"{event or 'unknown'} trigger: always a full run"
    if not record or record.get("version") != GATE_VERSION:
        return True, "no gate record yet"
    if not record.get("idle"):
        return True, "the last full run was not idle: " + "; ".join(record.get("not_idle") or ["?"])
    if record.get("source_sha") != head_sha:
        return True, (f"source changed since the last full run ({str(record.get('source_sha'))[:10]} -> "
                      f"{head_sha[:10]})")
    if not manifest_etag or record.get("manifest_etag") != manifest_etag:
        return True, ("the state manifest changed since the last full run (generation "
                      f"{record.get('generation')}: another writer)")
    generation = record.get("generation")
    try:
        rec_at = datetime.fromisoformat(record["recorded_at"])
    except (KeyError, TypeError, ValueError):
        return True, "gate record without a valid date"
    if rec_at.date() != now.date():
        return True, f"first run of the UTC day {now.date()} (snapshot, daily maintenance)"
    age_h = (now - rec_at).total_seconds() / 3600
    if age_h >= MAX_IDLE_HOURS:
        return True, (f"last full run {age_h:.1f} h ago (>= {MAX_IDLE_HOURS:g} h: title renames, "
                      "retries, rescans)")
    if observed is None:
        return True, "channels not observed"
    if observed.get("errors"):
        return True, "could not check every channel: " + "; ".join(observed["errors"][:3])
    want_yt = {c["slug"] for c in enabled_channels(channels) if c.get("source") != "rss"}
    want_rss = {c["slug"] for c in enabled_channels(channels) if c.get("source") == "rss"}
    if set(observed.get("youtube") or {}) != want_yt or set(observed.get("rss") or {}) != want_rss:
        return True, "observation does not cover every enabled channel"
    for slug, digest in sorted((observed.get("rss") or {}).items()):
        if (record.get("rss") or {}).get(slug) != digest:
            return True, f"external feed {slug} changed"
    unknown = unknown_ids(observed, record)
    if unknown:
        flat = [v for ids in unknown.values() for v in ids]
        ready = processable_fn(flat)
        if ready:
            where = [f"{s}: {', '.join(i for i in ids if i in ready)}" for s, ids in unknown.items()
                     if any(i in ready for i in ids)]
            return True, "new video(s) on YouTube - " + "; ".join(where[:5])
        waiting = f"; {len(flat)} not processable yet (live/upcoming)" if flat else ""
    else:
        waiting = ""
    return False, (f"nothing to do: last full run idle {age_h:.1f} h ago (generation {generation}, "
                   f"source {head_sha[:10]}), no new video on {len(want_yt)} channels, "
                   f"{len(want_rss)} external feed(s) unchanged{waiting}")


def handled_ids(observed: dict, processed: dict, entries_by_slug: dict[str, list[dict]]) -> dict:
    out = {}
    for slug, ids in (observed.get("youtube") or {}).items():
        done = set(processed.get(slug) or []) | {e.get("video_id") for e in entries_by_slug.get(slug, [])}
        out[slug] = [i for i in ids if i in done]
    return out


def idle_problems(*, push_unchanged: str, pending: str, index_pending: str,
                  channel_errors: list, auth_error: bool, credit_error: bool,
                  observed: dict | None) -> list[str]:
    out = []
    if push_unchanged != "true":
        out.append("the state changed (new generation)")
    if str(pending or "0") not in ("0", ""):
        out.append(f"backfill backlog {pending}")
    if index_pending == "true":
        out.append("search index rebuild postponed")
    if channel_errors:
        out.append(f"{len(channel_errors)} channel error(s)")
    if auth_error:
        out.append("YouTube auth error")
    if credit_error:
        out.append("Anthropic credit error")
    if observed is None:
        out.append("channels not observed at the start")
    elif observed.get("errors"):
        out.append("observation errors at the start")
    return out


# --------------------------------------------------------------------------
# R2 + CLI
# --------------------------------------------------------------------------

def _gate_key() -> str:
    import site_state
    return site_state.GATE_KEY


def _manifest_etag(s3, bucket: str) -> str:
    """ETag of the state manifest (a HEAD: the manifest itself is several MB).
    Any new generation, by any writer, changes it."""
    import site_state
    return str(s3.head_object(Bucket=bucket, Key=site_state.MANIFEST_KEY).get("ETag", "")).strip('"')


def _git_head() -> str:
    import subprocess
    return subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()


def _output(**kv) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            for k, v in kv.items():
                fh.write(f"{k}={str(v).replace(chr(10), ' ')}\n")


def _summary(text: str) -> None:
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def _load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def cmd_check(args) -> int:
    run, reason = True, "gate error"
    try:
        import site_state
        channels = json.loads(Path(args.channels).read_text(encoding="utf-8-sig"))
        observed = observe(channels)
        Path(args.observed_out).write_text(json.dumps(observed), encoding="utf-8")
        s3, bucket = site_state.r2_client(4)
        record, _etag = site_state.get_json_with_etag(s3, bucket, _gate_key())
        run, reason = decide(record, event=os.environ.get("GITHUB_EVENT_NAME", ""),
                             head_sha=_git_head(), manifest_etag=_manifest_etag(s3, bucket),
                             now=datetime.now(timezone.utc), observed=observed, channels=channels)
    except Exception as exc:  # fail-safe: a broken gate never stops the pipeline
        run, reason = True, f"gate error ({type(exc).__name__}): full run"
        print(f"::warning::pipeline gate: {reason}")
    _summary(f"### Pipeline gate\n- {'RUN' if run else 'SKIP (nothing to do, run ends here)'}: {reason}")
    _output(run=str(run).lower(), reason=reason)
    return 0


def cmd_record(args) -> int:
    try:
        import channel_errors
        import site_state
        observed = _load_json(Path(args.observed), None) if args.observed else None
        processed = _load_json(Path(args.root) / "processed.json", {}) or {}
        entries = {}
        for slug in (observed or {}).get("youtube", {}):
            entries[slug] = _load_json(Path(args.root) / "feeds" / f"{slug}.entries.json", []) or []
        problems = idle_problems(
            push_unchanged=args.push_unchanged, pending=args.pending,
            index_pending=args.index_pending, channel_errors=channel_errors.load(),
            auth_error=Path("/tmp/auth_error").exists(),
            credit_error=Path("/tmp/anthropic_credit_error").exists(), observed=observed)
        s3, bucket = site_state.r2_client(4)
        rec = {
            "version": GATE_VERSION,
            "recorded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "run_id": os.environ.get("GITHUB_RUN_ID", ""),
            "source_sha": _git_head(),
            "generation": int(args.generation or 0),
            "manifest_etag": _manifest_etag(s3, bucket),
            "idle": not problems,
            "not_idle": problems,
            "handled": handled_ids(observed or {}, processed, entries),
            "rss": (observed or {}).get("rss") or {},
        }
        site_state.put_json(s3, bucket, _gate_key(), rec)
        _summary(f"### Pipeline gate record\n- {'idle' if rec['idle'] else 'not idle: ' + '; '.join(problems)}"
                 f" (generation {rec['generation']})")
    except Exception as exc:  # best effort: without a fresh record the next run is a full run
        print(f"::warning::pipeline gate record not written ({type(exc).__name__})")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--channels", default="channels.json")
    c.add_argument("--observed-out", required=True)
    c.set_defaults(func=cmd_check)
    r = sub.add_parser("record")
    r.add_argument("--root", default=".")
    r.add_argument("--observed", default="")
    r.add_argument("--push-unchanged", default="")
    r.add_argument("--generation", default="", help="push output (shown in logs only)")
    r.add_argument("--pending", default="")
    r.add_argument("--index-pending", default="")
    r.set_defaults(func=cmd_record)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
