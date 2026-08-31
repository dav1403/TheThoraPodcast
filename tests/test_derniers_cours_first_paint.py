"""Guard for what derniers-cours.html downloads before it paints.

History of this page, all of it invisible from the rendered result:
  1. it awaited `Promise.all(channels.map(fetchChannelEntries))` — every
     per-channel feed — before drawing a single row;
  2. it was changed to paint from home.json first and stream the feeds in
     behind that first paint, which fixed the blank screen but still pulled
     33 672 889 bytes raw / 4 573 683 gzipped on every visit (measured
     31/08/2026) for a first screen of 30 items;
  3. it now reads `latest.json` — the pre-computed chronological index written
     by build_latest_index() — and fetches a per-channel feed ONLY when the
     visitor picks that rav's chip.

Re-introducing (1) or (2) still renders a correct page, just after megabytes of
download, so nothing else in the suite would notice. This test pins the
contract statically: cheap, no browser, no network.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "derniers-cours.html"

# The main inline block (skip `src=` externals and JSON payloads).
_INLINE_JS_RE = re.compile(
    r'<script(?!\s[^>]*\bsrc\b)'
    r'(?!\s[^>]*type=["\']application/(?:ld\+)?json["\'])'
    r'[^>]*>([\s\S]*?)</script>',
    re.IGNORECASE,
)


def _main_script():
    blocks = _INLINE_JS_RE.findall(PAGE.read_text(encoding="utf-8"))
    assert blocks, "no inline <script> found in derniers-cours.html"
    return max(blocks, key=len)


def _boot_iife():
    """The trailing `(async () => { ... })()` that drives the page load."""
    js = _main_script()
    start = js.rfind("(async () => {")
    assert start != -1, "the boot IIFE of derniers-cours.html could not be located"
    return js[start:]


def test_the_index_is_what_the_page_loads():
    js = _main_script()
    assert re.search(r"""fetch\(\s*['"]latest\.json['"]""", js), (
        "derniers-cours.html no longer fetches latest.json — the whole page "
        "hangs on that pre-computed index (build_latest_index)"
    )


def test_no_feed_is_fetched_before_the_visitor_asks():
    """The boot path must not touch `feeds/<slug>.entries.json` at all.

    A feed is ~1 MB and there are 22 of them; they are only legitimate behind a
    chip click (ensureFullHistory).
    """
    boot = _boot_iife()
    for m in re.finditer(r"fetchChannelEntries\s*\(|['\"]feeds/", boot):
        raise AssertionError(
            "the boot path loads a per-channel feed again (offset "
            f"{m.start()}) — feeds belong in ensureFullHistory(), behind a chip"
        )


def test_first_render_needs_only_the_index():
    boot = _boot_iife()
    fetches = [m.group(1) for m in re.finditer(r"""fetch\(\s*['"]([^'"]+)['"]""", boot)]
    assert fetches, "the boot IIFE fetches nothing at all"
    # The chips, the counts, the guest patterns and the totals all come from
    # latest.json now: re-adding channels.json / speakers.json / home.json here
    # would be three round-trips for data the page already holds.
    assert fetches == ["latest.json"], (
        f"the boot path fetches more than the index: {fetches}"
    )


def test_no_promise_all_blocks_the_first_paint():
    js = _main_script()
    # The original shape: every feed awaited at once, before anything was drawn.
    assert not re.search(r"Promise\.all\(\s*channels\.map", js), (
        "channels.map() inside Promise.all() is the blocking pattern this page "
        "was fixed for"
    )
    boot = _boot_iife()
    assert "Promise.all" not in boot, (
        "nothing may be awaited in bulk ahead of the first paint"
    )


def test_a_chip_still_opens_the_whole_history():
    """The index is only a slice — picking a rav must not silently truncate him."""
    js = _main_script()
    assert "ensureFullHistory" in js and "fetchChannelEntries" in js, (
        "the lazy per-rav feed load is gone: a chip would only ever show the "
        "handful of that rav's classes that made it into the index"
    )


def test_search_still_reaches_the_whole_catalogue():
    js = _main_script()
    assert re.search(r"""fetch\(\s*['"]search-index\.json['"]""", js), (
        "the whole-catalogue search fallback is gone — the search box would "
        "silently stop finding anything older than the index"
    )
    assert "ensureSearchIndex" in js


def test_the_announced_total_is_the_catalogue_not_the_index():
    js = _main_script()
    assert re.search(r"courseLangCount\(\s*indexMeta", js), (
        "the subtitle must count the whole catalogue (latest.json totals, "
        "per course language), not the rows that happen to be loaded"
    )


def test_page_machinery_is_still_wired():
    js = _main_script()
    for marker in ("epStore", "catalogLoaded", "restorePlayingBtn",
                   "courseLangKeeps", "pendingFeeds"):
        assert marker in js, (
            f"`{marker}` is missing from derniers-cours.html — the list store, "
            "the loading state or the player button re-attached across "
            "re-renders appears to have been dropped"
        )


def test_hitat_episodes_stay_out_of_the_chronological_list():
    js = _main_script()
    assert re.search(r"HITAT DU JOUR", js), (
        "the daily HITAT shiurim must still be filtered out of this list — "
        "they live on hitat.html"
    )
