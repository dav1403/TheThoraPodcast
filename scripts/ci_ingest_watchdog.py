"""
ci_ingest_watchdog.py
---------------------
Detects "green but empty" runs: the pipeline can finish successfully while
downloading nothing at all (per-episode errors are caught and logged, expired
YouTube cookies give silent 403s). A single zero run is normal (nothing new on
YouTube), so we only alert on a *streak* of zero runs while work is still
pending.

Mechanism
  - "Ingested this run" = same diff generate_summary.py uses: the growth of
    feeds/<slug>.entries.json versus /tmp/pre_run_counts.json written by
    ci_pre_run_counts.py at the start of the run. That is the ground truth
    (an episode only lands in entries.json once its audio is on R2).
  - "Pending work" = historical backlog still to fetch, from backfill_state.json:
    queued items (`todo`) plus, per channel, yt_video_count - episodes in feed.
    When nothing is pending, a zero run means "nothing to do", not a failure,
    and the streak is left untouched.

State is persisted in feeds/ingest_state.json (committed by the workflow, which
already stages the whole feeds/ directory).

Outputs (GITHUB_OUTPUT, when running in Actions): added, pending, zero_streak.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

FEEDS_DIR = Path("feeds")
STATE_FILE = FEEDS_DIR / "ingest_state.json"
PRE_RUN_COUNTS = Path("/tmp/pre_run_counts.json")


def load_json(path: Path, default):
    try:
        text = Path(path).read_text(encoding="utf-8-sig").strip()
        return json.loads(text) if text else default
    except Exception:
        return default


def count_added() -> int:
    """Episodes newly written to the feeds during this run."""
    pre_run = load_json(PRE_RUN_COUNTS, {})
    if not pre_run:
        # No baseline (first run ever, or the pre-run step was skipped) — we
        # cannot measure a delta, so report 0 added but let the caller know via
        # the absence of pending data. Being conservative here is fine: the
        # streak only grows when work is pending.
        return 0
    total = 0
    for f in FEEDS_DIR.glob("*.entries.json"):
        slug = f.stem.replace(".entries", "")
        entries = load_json(f, [])
        now = len(entries) if isinstance(entries, list) else 0
        before = pre_run.get(slug, now)
        total += max(0, now - before)
    return total


def count_pending() -> int:
    """Episodes known to be missing from the feeds (historical backlog)."""
    state = load_json(Path("backfill_state.json"), {})
    if not isinstance(state, dict):
        return 0
    pending = 0
    for slug, ch in state.items():
        if not isinstance(ch, dict):
            continue
        todo = ch.get("todo")
        if isinstance(todo, list):
            pending += len(todo)
        yt_count = ch.get("yt_video_count")
        if isinstance(yt_count, int):
            entries = load_json(FEEDS_DIR / f"{slug}.entries.json", [])
            have = len(entries) if isinstance(entries, list) else 0
            pending += max(0, yt_count - have)
    return pending


def main() -> None:
    FEEDS_DIR.mkdir(parents=True, exist_ok=True)
    added = count_added()
    pending = count_pending()

    state = load_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    streak = state.get("consecutive_zero_runs", 0)
    if not isinstance(streak, int) or streak < 0:
        streak = 0

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if added > 0:
        streak = 0
        state["last_nonzero_run"] = now_iso
    elif pending > 0:
        streak += 1
    # else: nothing ingested but nothing pending either -> not a failure.

    state["consecutive_zero_runs"] = streak
    state["last_run"] = now_iso
    state["last_added"] = added
    state["last_pending"] = pending
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"Ingested this run: {added} | pending backlog: {pending} | "
          f"consecutive zero runs: {streak}")

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"added={added}\n")
            fh.write(f"pending={pending}\n")
            fh.write(f"zero_streak={streak}\n")


if __name__ == "__main__":
    main()
