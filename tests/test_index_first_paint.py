"""Guard for what index.html downloads before it paints.

The home page had been reading its channels, its "recents", its spotlight and
its hero from the pre-built `home.json` for a while — but it still paid the
full price anyway, because of ONE line of text.

`js/utils.js` shipped an IIFE, `loadHeaderStats()`, that filled the
`#header-stats` banner ("37 rabbins · 32 428 cours · ~24 321h de Torah") by
downloading `channels.json` and then the 22 `feeds/<slug>.entries.json` —
**33 069 KB over 23 requests, measured with curl on 02/09/2026** — just to
count episodes that `home.json` already counts in its `stats` block. On
index.html the page's own boot then overwrote that very text with the same
numbers, so the megabytes were pure waste. The same IIFE fired on the ~52
generated channel pages too (their inline `applyLang()` bakes the figure, but
it runs *after* utils.js, so the guard saw an empty node).

`rabbins.html` escaped only by accident: its `#header-stats` ships the
"Chargement…" placeholder, which is non-empty, so the guard returned early.

Re-introducing the fan-out would still render a perfectly correct page — just
after 33 MB of download — so no other test in this suite would notice. This one
pins the contract statically: cheap, no browser, no network.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "index.html"
UTILS = ROOT / "js" / "utils.js"
HOME_JSON = ROOT / "home.json"

_INLINE_JS_RE = re.compile(
    r'<script(?!\s[^>]*\bsrc\b)'
    r'(?!\s[^>]*type=["\']application/(?:ld\+)?json["\'])'
    r'[^>]*>([\s\S]*?)</script>',
    re.IGNORECASE,
)


def _main_script() -> str:
    blocks = _INLINE_JS_RE.findall(PAGE.read_text(encoding="utf-8"))
    assert blocks, "no inline <script> found in index.html"
    return max(blocks, key=len)


def _utils() -> str:
    return UTILS.read_text(encoding="utf-8")


# ── The regression that cost 33 MB on the most visited page ──────────────────

def test_header_stats_does_not_fan_out_over_the_feeds():
    src = _utils()
    start = src.index("function _loadHeaderStats(")
    end = src.index("\n(function initHeaderStats()", start)
    block = src[start:end]
    for needle in ("fetchChannelEntries", "feeds/", "channels.json"):
        assert needle not in block, (
            "js/utils.js composes the header line from the per-channel feeds "
            f"again ({needle!r}) — 23 requests / 33 MB for one line of text, "
            "on every page that carries #header-stats"
        )
    assert "fetchHomeJson()" in block, (
        "the header line no longer reads the pre-computed home.json digest"
    )


def test_header_stats_waits_for_dom_ready():
    """The ~52 generated pages bake the figure from an inline script that runs
    AFTER utils.js. Checking at parse time saw an empty node and fetched for
    nothing; the check must happen once the document is parsed."""
    src = _utils()
    assert re.search(
        r"function initHeaderStats\(\) \{\s*if \(document\.readyState === 'loading'\)"
        r"\s*\{\s*document\.addEventListener\('DOMContentLoaded', _loadHeaderStats\);",
        src,
    ), "the header-stats loader no longer waits for DOMContentLoaded"


def test_index_never_touches_a_channel_feed():
    js = _main_script()
    for m in re.finditer(r"fetchChannelEntries\s*\(|['\"]feeds/(?!transcripts/)", js):
        raise AssertionError(
            "index.html loads a per-channel feed again (offset "
            f"{m.start()}) — everything the home page shows is baked in home.json"
        )


def test_home_json_is_fetched_once_and_shared():
    """Two consumers on the same page (the header line and the page boot) must
    not mean two requests: js/utils.js memoises the promise, index.html uses
    that shared loader instead of its own fetch."""
    src = _utils()
    assert "_ttpHomePromise" in src and "function fetchHomeJson()" in src, (
        "the memoised home.json loader is gone from js/utils.js"
    )
    js = _main_script()
    assert "await fetchHomeJson()" in js, (
        "index.html fetches home.json on its own again — the header line then "
        "pays for a second request"
    )
    assert not re.search(r"""fetch\(\s*['"]home\.json['"]""", js), (
        "index.html still has a raw fetch('home.json')"
    )


def test_boot_only_fetches_the_precomputed_digest():
    js = _main_script()
    boot_start = js.rfind("(async () => {")
    assert boot_start != -1, "the boot IIFE of index.html could not be located"
    boot = js[boot_start:]
    fetched = [m.group(1) for m in re.finditer(r"""fetch\(\s*['"]([^'"]+)['"]""", boot)]
    assert fetched == [], (
        f"the boot path of index.html fetches raw URLs again: {fetched} — it "
        "must go through the shared fetchHomeJson()"
    )


def test_heavy_indexes_stay_lazy():
    """search-index.json (~1.1 MB gzip) and the full-text shards must stay
    behind a keystroke, never at boot."""
    js = _main_script()
    assert "search-index.json" in js, "the catalogue title search is gone"
    boot_start = js.rfind("(async () => {")
    assert "search-index.json" not in js[boot_start:], (
        "search-index.json is fetched at boot instead of on the first keystroke"
    )


# ── homeHeaderStats(): the counting rule, run for real ───────────────────────
#
# The rule is subtle: `stats.channels` counts channels AND speakers (37 vs 22),
# so it is kept verbatim with no filter; under a course-language filter it would
# read "37 rabbins · 5 135 cours", so the ravs with nothing left in that
# language are dropped instead.

def _node():
    return shutil.which("node")


def _fn_src(name: str) -> str:
    src = _utils()
    start = src.index("function %s(" % name)
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def _run_node(script: str) -> str:
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".js")
        os.write(fd, script.encode("utf-8"))
        os.close(fd)
        proc = subprocess.run(
            [_node(), tmp], capture_output=True, text=True,
            encoding="utf-8", timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def _eval_header_stats(home: dict, pref: str) -> dict:
    node = _node()
    if node is None:  # pragma: no cover - local convenience only
        if os.environ.get("CI"):
            pytest.fail("Node is required in CI: this test must not silently skip")
        pytest.skip("node not installed")
    script = (
        "var window = { TTPPrefs: { courseLang: function () { return "
        + json.dumps(pref) + "; }, uiLang: function () { return 'fr'; } } };\n"
        + _fn_src("courseLangCount")
        + _fn_src("homeHeaderStats")
        + _fn_src("headerStatsText")
        + "var home = " + json.dumps(home, ensure_ascii=False) + ";\n"
        "var s = homeHeaderStats(home);\n"
        "console.log(JSON.stringify({ s: s, text: headerStatsText(s.channels, s.episodes) }));\n"
    )
    return json.loads(_run_node(script))


_HOME = {
    "stats": {"channels": 4, "episodes": 100, "episodes_fr": 70, "episodes_he": 30},
    "channels": [
        {"slug": "a", "count": 60, "count_fr": 60, "count_he": 0},
        {"slug": "b", "count": 30, "count_fr": 0, "count_he": 30},
    ],
    "speakers": [
        {"slug": "s1", "count": 10, "count_fr": 10, "count_he": 0},
        {"slug": "s2"},  # older artefact: no count at all
    ],
}


def test_unfiltered_header_keeps_the_baked_totals():
    out = _eval_header_stats(_HOME, "all")
    assert out["s"] == {"channels": 4, "episodes": 100}
    assert out["text"] == "4 rabbins · 100 cours · ~75h de Torah"


def test_hebrew_filter_drops_the_ravs_with_nothing_left():
    """`b` teaches in Hebrew, `a` and `s1` do not, `s2` has no per-language
    count and is kept rather than wrongly hidden."""
    out = _eval_header_stats(_HOME, "he")
    assert out["s"] == {"channels": 2, "episodes": 30}


def test_french_filter():
    out = _eval_header_stats(_HOME, "fr")
    assert out["s"] == {"channels": 3, "episodes": 70}


def test_rule_runs_on_the_committed_home_json():
    """Sanity check against the real artefact, not only a fixture."""
    home = json.loads(HOME_JSON.read_text(encoding="utf-8"))
    out = _eval_header_stats(home, "all")
    assert out["s"]["channels"] == home["stats"]["channels"]
    assert out["s"]["episodes"] == home["stats"]["episodes"]
    assert out["s"]["channels"] > 0 and out["s"]["episodes"] > 0

    he = _eval_header_stats(home, "he")
    assert he["s"]["episodes"] == home["stats"]["episodes_he"]
    assert 0 < he["s"]["channels"] <= out["s"]["channels"], (
        "the Hebrew filter must narrow the rav count, never widen it"
    )
