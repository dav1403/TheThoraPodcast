"""Guard for the first paint of derniers-cours.html.

The page used to `await Promise.all(channels.map(fetchChannelEntries))` before
rendering a single row: ~37 per-channel feeds, ~35 MB of JSON (one of them
12.5 MB on its own), all downloaded before the visitor saw anything. It was
rewritten to paint immediately from `home.json` (the 20 newest classes of the
whole site, ~40 KB) and to stream the feeds in behind that first paint.

The regression is invisible: re-introducing a blocking `Promise.all` — or simply
moving the `home.json` fetch after the feed loop — still renders a correct page,
just several seconds later. Nothing else in the suite looks at this file, so
this test pins the ordering statically: cheap, no browser, no network.
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


def test_home_json_is_fetched_before_any_channel_feed():
    js = _main_script()

    home = re.search(r"""fetch\(\s*['"]home\.json['"]""", js)
    assert home, (
        "derniers-cours.html no longer fetches home.json — the first paint "
        "depends on it (the per-channel feeds weigh ~35 MB in total)"
    )

    # Feeds are read through fetchChannelEntries() (js/utils.js), which is what
    # builds the `feeds/<slug>.entries.json` URL.
    feed_calls = [m.start() for m in re.finditer(r"fetchChannelEntries\s*\(", js)]
    feed_calls += [m.start() for m in re.finditer(r"""['"]feeds/""", js)]
    assert feed_calls, "expected the page to still load the per-channel feeds"

    assert home.start() < min(feed_calls), (
        "the home.json fetch must come before the first channel-feed load: "
        "it is the only thing standing between the visitor and a blank page"
    )


def _boot_iife():
    """The trailing `(async () => { ... })()` that drives the page load."""
    js = _main_script()
    start = js.rfind("(async () => {")
    assert start != -1, "the boot IIFE of derniers-cours.html could not be located"
    return js[start:]


def test_first_render_happens_before_the_feeds_are_awaited():
    boot = _boot_iife()

    first_feed = min(
        [m.start() for m in re.finditer(r"fetchChannelEntries\s*\(", boot)]
        or [len(boot)]
    )
    first_render = min(
        [m.start() for m in re.finditer(r"renderList\s*\(\s*\)", boot)] or [len(boot)]
    )
    assert first_render < first_feed, (
        "renderList() must be called before any feed is requested — otherwise "
        "the page is blank for the whole download again"
    )


def test_no_promise_all_blocks_the_first_paint():
    js = _main_script()

    # The old shape: every feed awaited at once, before anything was drawn.
    assert not re.search(
        r"Promise\.all\(\s*channels\.map", js
    ), "channels.map() inside Promise.all() is the blocking pattern this page was fixed for"

    home = re.search(r"""fetch\(\s*['"]home\.json['"]""", js)
    assert home
    for m in re.finditer(r"Promise\.all\s*\(", js):
        assert m.start() > home.start(), (
            "a Promise.all() runs before the home.json fetch — nothing may be "
            f"awaited ahead of the first paint (offset {m.start()})"
        )


def test_streaming_machinery_is_still_wired():
    js = _main_script()
    for marker in ("epStore", "feedWorker", "CONCURRENCY", "catalogLoaded", "restorePlayingBtn"):
        assert marker in js, (
            f"`{marker}` is missing from derniers-cours.html — the streaming "
            "catalog (bounded-concurrency feed loading, live counter, player "
            "button re-attached across re-renders) appears to have been dropped"
        )


def test_hitat_episodes_stay_out_of_the_chronological_list():
    js = _main_script()
    assert re.search(r"HITAT DU JOUR", js), (
        "the daily HITAT shiurim must still be filtered out of this list — "
        "they live on hitat.html"
    )
