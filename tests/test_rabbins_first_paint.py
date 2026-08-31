"""Guard for what rabbins.html downloads before it paints.

The page shows ~37 bubbles, one per rav, each carrying a course COUNT and a
recency dot, sorts them by last class, offers a per-rav modal and a 15-card
"derniers cours" carousel. To produce those it used to download the 22
`feeds/<slug>.entries.json` — **33 703 263 bytes raw / 4 577 320 gzipped**,
measured with curl on 31/08/2026 — and hold ~32 000 episodes in memory.

Everything it actually needed was already pre-computed:
  * `home.json`   — per-channel AND per-speaker counts (count / count_fr /
    count_he) plus the `last_*` blocks written by `_last_class_block()`;
  * `latest.json` — the newest classes site-wide, HITAT already excluded and
    guests already matched server-side (`sp`), which feeds the carousel and
    seeds the course-title search.

Only the whole-catalogue title search still needs more, and it is fetched when
the visitor types, never before (`ensureSearchIndex`).

Re-introducing the feed fan-out still renders a perfectly correct page — just
after megabytes of download — so no other test in this suite would notice. This
one pins the contract statically: cheap, no browser, no network.
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "rabbins.html"
UTILS = ROOT / "js" / "utils.js"

# The main inline block (skip `src=` externals and JSON / JSON-LD payloads).
_INLINE_JS_RE = re.compile(
    r'<script(?!\s[^>]*\bsrc\b)'
    r'(?!\s[^>]*type=["\']application/(?:ld\+)?json["\'])'
    r'[^>]*>([\s\S]*?)</script>',
    re.IGNORECASE,
)


def _main_script() -> str:
    blocks = _INLINE_JS_RE.findall(PAGE.read_text(encoding="utf-8"))
    assert blocks, "no inline <script> found in rabbins.html"
    return max(blocks, key=len)


def _boot_iife() -> str:
    """The trailing `(async () => { ... })()` that drives the page load."""
    js = _main_script()
    start = js.rfind("(async () => {")
    assert start != -1, "the boot IIFE of rabbins.html could not be located"
    return js[start:]


def test_no_feed_is_fetched_anywhere():
    """`feeds/<slug>.entries.json` must not be touched by this page at all.

    Unlike derniers-cours.html — which legitimately loads one rav's full
    history behind a chip — nothing on this page ever lists episodes: the modal
    shows a count and the title of the last class, both pre-computed. So there
    is no lazy path either; a feed reference here is a regression, period.
    """
    js = _main_script()
    for m in re.finditer(r"fetchChannelEntries\s*\(|['\"]feeds/", js):
        raise AssertionError(
            "rabbins.html loads a per-channel feed again (offset "
            f"{m.start()}) — 22 requests / 33.7 MB for a count and a date"
        )


def test_boot_only_fetches_precomputed_indexes():
    boot = _boot_iife()
    fetched = [m.group(1) for m in re.finditer(r"""fetch\(\s*['"]([^'"]+)['"]""", boot)]
    assert sorted(fetched) == [
        "channels.json", "home.json", "latest.json", "speakers.json"
    ], (
        "the boot path of rabbins.html fetches something else than the four "
        f"small pre-computed files: {fetched}"
    )


def test_the_catalogue_search_index_is_lazy():
    """search-index.json (~1.1 MB gzipped) must stay behind a keystroke."""
    js = _main_script()
    assert "search-index.json" in js, (
        "the whole-catalogue title search is gone — searching a rav by one of "
        "his course titles would silently stop finding the older ones"
    )
    boot = _boot_iife()
    assert "search-index.json" not in boot, (
        "search-index.json is fetched at boot — it belongs in "
        "ensureSearchIndex(), called when the visitor actually types"
    )
    assert re.search(r"if \(searchQuery\) ensureSearchIndex\(\);", js), (
        "nothing arms ensureSearchIndex() on input any more"
    )


def test_counts_and_last_class_come_from_the_precomputed_records():
    """The two figures the feeds used to provide must read home.json."""
    js = _main_script()
    assert "courseLangCount(d.meta, 'count')" in js, (
        "the per-rav count no longer comes from the pre-computed home.json record"
    )
    assert "courseLangLast(d.meta)" in js, (
        "the per-rav last class no longer comes from the pre-computed home.json record"
    )


# ── courseLangLast(): the rule that is easy to get silently wrong ────────────

def _node():
    """Path to the Node binary, or None. CI fails rather than skips (see below)."""
    return shutil.which("node")


def _course_lang_last_src() -> str:
    src = UTILS.read_text(encoding="utf-8")
    start = src.index("function courseLangLast(")
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def _run_node(script: str) -> str:
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".js")
        os.write(fd, script.encode("utf-8"))
        os.close(fd)
        proc = subprocess.run(
            # Explicit UTF-8: a Hebrew title comes back mojibake under the
            # Windows console codepage otherwise.
            [_node(), tmp], capture_output=True, text=True,
            encoding="utf-8", timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


# A mono-language rav gets NO suffixed block (it would be a byte-for-byte copy
# of the unsuffixed one), a bilingual one does — see _last_class_block().
_MONO_HE = {
    "count": 10, "count_fr": 0, "count_he": 10,
    "last_published": "2026-08-01T00:00:00+00:00", "last_title": "שיעור",
}
_BILINGUAL = {
    "count": 20, "count_fr": 12, "count_he": 8,
    "last_published": "2026-08-30T00:00:00+00:00", "last_title": "FR newest",
    "last_published_fr": "2026-08-30T00:00:00+00:00", "last_title_fr": "FR newest",
    "last_published_he": "2026-07-01T00:00:00+00:00", "last_title_he": "HE older",
}
_SILENT = {"count": 0, "count_fr": 0, "count_he": 0,
           "last_published": None, "last_title": None}


@pytest.mark.parametrize(
    "rec, pref, expected",
    [
        # Bilingual rav: each language gets its own last class.
        (_BILINGUAL, "all", "FR newest"),
        (_BILINGUAL, "fr", "FR newest"),
        (_BILINGUAL, "he", "HE older"),
        # 🔑 Mono-language rav under a filter matching his language: the
        # UNSUFFIXED block IS that language's last class. Reading
        # `last_title_he` naively would yield undefined and hide him.
        (_MONO_HE, "he", "שיעור"),
        (_MONO_HE, "all", "שיעור"),
        # ...and he must disappear entirely under the other language rather
        # than show a Hebrew class to someone filtering on French.
        (_MONO_HE, "fr", ""),
        # A rav with no class at all never yields an invented date.
        (_SILENT, "all", ""),
    ],
)
def test_course_lang_last_follows_the_home_json_rule(rec, pref, expected):
    node = _node()
    if node is None:  # pragma: no cover - local convenience only
        if os.environ.get("CI"):
            pytest.fail("Node is required in CI: this test must not silently skip")
        pytest.skip("node not installed")
    import json as _json
    script = (
        "var window = { TTPPrefs: { courseLang: function () { return "
        + _json.dumps(pref) + "; } } };\n"
        "function courseLangCount(rec, base) {\n"
        "  if (!rec) return 0;\n"
        "  base = base || 'count';\n"
        "  var pref = window.TTPPrefs.courseLang();\n"
        "  if (pref === 'all') return rec[base] || 0;\n"
        "  var v = rec[base + '_' + pref];\n"
        "  return (typeof v === 'number') ? v : (rec[base] || 0);\n"
        "}\n"
        + _course_lang_last_src()
        + "\nvar out = courseLangLast(" + _json.dumps(rec) + ");\n"
        "console.log(out ? out.title : '');\n"
    )
    assert _run_node(script) == expected
