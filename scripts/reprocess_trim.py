"""Re-apply the per-channel intro trim to episodes ALREADY published on R2.

The normal pipeline (``process_podcasts.py`` / ``backfill_podcasts.py``) only
trims NEW downloads. When you set ``intro_trim_sec`` on a channel that already
has episodes live on Cloudflare R2, use this one-off tool to retro-trim them:
for each episode it pulls the existing MP3 from R2, cuts the leading N seconds,
and re-uploads it under the SAME R2 key (overwriting the old file), then updates
``file_size`` / ``duration_secs`` in the feed sidecar and rebuilds the RSS.

SAFETY
------
* ``--dry-run`` is the DEFAULT — it only lists what WOULD be reprocessed and
  writes nothing. Pass ``--apply`` to actually download / trim / overwrite.
* Only channels whose ``intro_trim_sec`` > 0 are ever touched. A channel left at
  0 is skipped entirely.
* Idempotent: each reprocessed entry is stamped ``intro_trimmed`` with the trim
  value; re-running skips entries already trimmed at the same value, so you can
  never double-trim.

USAGE
-----
    # preview every channel that has a trim configured (no writes):
    py scripts/reprocess_trim.py

    # preview a single channel:
    py scripts/reprocess_trim.py --channel rav-itshak-cohen

    # actually apply (overwrites R2 objects in place):
    py scripts/reprocess_trim.py --channel rav-itshak-cohen --apply

    # limit how many episodes to process in one go:
    py scripts/reprocess_trim.py --channel rav-itshak-cohen --apply --limit 20

Requires the same R2_* env vars as the main pipeline (see .env).
"""
import argparse
import json
import os
import sys
from pathlib import Path

# Allow running from the repo root as `py scripts/reprocess_trim.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from process_podcasts import (  # noqa: E402
    CHANNELS_FILE, FEEDS_DIR, AUDIO_DIR,
    channel_intro_trim_sec, apply_intro_trim,
    get_r2_client, upload_audio_to_r2,
    load_feed_entries, save_feed_entries, build_rss_feed,
    get_channel_info,
)


def load_channels() -> list[dict]:
    return json.loads(Path(CHANNELS_FILE).read_text(encoding="utf-8-sig"))


def r2_key_from_url(audio_url: str, public_base: str) -> str | None:
    """Derive the R2 object key from a stored public audio_url."""
    prefix = public_base.rstrip("/") + "/"
    if audio_url and audio_url.startswith(prefix):
        return audio_url[len(prefix):]
    return None


def load_channel_info(slug: str, channel_cfg: dict) -> dict:
    """Load cached channel_info sidecar, else fetch from YouTube."""
    sidecar = FEEDS_DIR / f"{slug}.channel_info.json"
    if sidecar.exists():
        try:
            return json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            pass
    return get_channel_info(channel_cfg["youtube_channel_id"])


def reprocess_channel(channel_cfg: dict, apply: bool, limit: int | None) -> dict:
    slug = channel_cfg["slug"]
    trim = channel_intro_trim_sec(channel_cfg)
    stats = {"slug": slug, "trim": trim, "candidates": 0, "skipped_done": 0,
             "no_key": 0, "processed": 0, "errors": 0}

    if trim <= 0:
        print(f"[{slug}] intro_trim_sec = 0 — nothing to do.")
        return stats

    feed_path = FEEDS_DIR / f"{slug}.xml"
    entries = load_feed_entries(feed_path)
    if not entries:
        print(f"[{slug}] no entries sidecar — nothing to reprocess.")
        return stats

    public_base = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
    if not public_base and (apply or True):
        # We only strictly need it to derive keys; warn if absent.
        print(f"[{slug}] WARNING: R2_PUBLIC_URL not set — cannot derive R2 keys.")

    bucket = os.environ.get("R2_BUCKET_NAME")
    client = None
    changed = False
    done_count = 0

    for entry in entries:
        if entry.get("intro_trimmed") == trim:
            stats["skipped_done"] += 1
            continue
        key = r2_key_from_url(entry.get("audio_url", ""), public_base)
        if not key:
            stats["no_key"] += 1
            continue
        stats["candidates"] += 1

        if limit is not None and done_count >= limit:
            continue

        if not apply:
            print(f"[{slug}] WOULD trim {trim}s → {key}")
            done_count += 1
            continue

        # --- real work -----------------------------------------------------
        try:
            if client is None:
                client = get_r2_client()
            AUDIO_DIR.mkdir(exist_ok=True)
            local = AUDIO_DIR / Path(key).name
            client.download_file(bucket, key, str(local))
            apply_intro_trim(local, trim)
            new_url = upload_audio_to_r2(local, key)
            new_size = local.stat().st_size
            local.unlink(missing_ok=True)

            entry["audio_url"] = new_url
            entry["file_size"] = new_size
            if entry.get("duration_secs"):
                entry["duration_secs"] = max(0, int(entry["duration_secs"]) - trim)
            entry["intro_trimmed"] = trim
            changed = True
            done_count += 1
            stats["processed"] += 1
            print(f"[{slug}] trimmed {trim}s + re-uploaded {key} ({new_size} bytes)")
            # Persist progress after each episode so a crash is resumable.
            save_feed_entries(feed_path, entries)
        except Exception as e:
            stats["errors"] += 1
            print(f"[{slug}] ERROR on {key}: {e}")

    if apply and changed:
        save_feed_entries(feed_path, entries)
        try:
            channel_info = load_channel_info(slug, channel_cfg)
            build_rss_feed(channel_cfg, channel_info, entries, feed_path)
            print(f"[{slug}] RSS rebuilt.")
        except Exception as e:
            print(f"[{slug}] WARNING: could not rebuild RSS: {e}")

    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--channel", help="slug of a single channel (default: all with trim>0)")
    parser.add_argument("--apply", action="store_true",
                        help="actually download/trim/overwrite (default is dry-run)")
    parser.add_argument("--limit", type=int, default=None,
                        help="max episodes to process per channel this run")
    args = parser.parse_args()

    channels = load_channels()
    if args.channel:
        channels = [c for c in channels if c.get("slug") == args.channel]
        if not channels:
            print(f"No channel with slug '{args.channel}' found.")
            sys.exit(1)

    mode = "APPLY (writes to R2)" if args.apply else "DRY-RUN (no writes)"
    print(f"=== reprocess_trim — {mode} ===\n")

    totals = {"candidates": 0, "processed": 0, "errors": 0, "skipped_done": 0}
    for ch in channels:
        stats = reprocess_channel(ch, args.apply, args.limit)
        for k in totals:
            totals[k] += stats.get(k, 0)
        print()

    print("=" * 50)
    print(f"Candidates: {totals['candidates']} | already-trimmed: {totals['skipped_done']} | "
          f"processed: {totals['processed']} | errors: {totals['errors']}")
    if not args.apply and totals["candidates"]:
        print("\nThis was a DRY-RUN. Re-run with --apply to overwrite R2 in place.")
    if totals["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
