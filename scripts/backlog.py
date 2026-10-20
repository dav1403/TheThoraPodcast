"""Historical ingest backlog — "courses still to integrate", one definition.

Shared by scripts/ci_ingest_watchdog.py (alerts on "green but empty" runs)
and the private dashboard (stats_update.py -> stats_core.build_dashboard).
Stdlib only, no I/O: callers load backfill_state.json and the per-channel
entry counts themselves (local files for the watchdog, R2 for the dashboard).

Per channel of backfill_state.json:
    len(todo)  +  max(0, yt_video_count - entries in feeds/<slug>.entries.json)

`todo` is the queue of video ids found on YouTube (/videos, /streams,
/shorts) but missing from the feed at the last scan; `yt_video_count` is the
number of ids on the channel at that scan. NB: the two terms overlap — a
queued id is also part of the yt_video_count - entries gap — so while a
channel has a non-empty queue the figure counts it twice. It is an upper
bound, kept as-is so the dashboard and the watchdog stay in agreement.
"""
from __future__ import annotations


def channel_pending(ch: dict, have: int) -> int:
    """Pending episodes of one channel; `have` = len(entries.json)."""
    if not isinstance(ch, dict):
        return 0
    n = 0
    todo = ch.get("todo")
    if isinstance(todo, list):
        n += len(todo)
    yt_count = ch.get("yt_video_count")
    if isinstance(yt_count, int):
        n += max(0, yt_count - have)
    return n


def pending_backlog(state, entry_counts: dict[str, int]) -> dict:
    """Aggregate backlog: {total, channels, top: {slug, n} | None}.

    `state` = parsed backfill_state.json; `entry_counts` maps a slug to the
    number of entries in its entries.json (missing slug = 0).
    """
    total = 0
    per: dict[str, int] = {}
    if isinstance(state, dict):
        for slug, ch in state.items():
            n = channel_pending(ch, int(entry_counts.get(slug, 0) or 0))
            if n > 0:
                per[slug] = n
                total += n
    top = None
    if per:
        slug = min(per, key=lambda s: (-per[s], s))
        top = {"slug": slug, "n": per[slug]}
    return {"total": total, "channels": len(per), "top": top}
