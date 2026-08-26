"""The rendered channel page must carry the course language of every episode.

The site-wide "langue des cours" filter (js/utils.js) can only hide the classes
of the other language on a MIXED channel page — Rav-benizri ships 1 133 Hebrew
and 515 French classes on one page — if the generator stamps each row with
`data-ep-lang`. That attribute is the whole contract between the generator and
the front-end, so it gets its own guard: a template edit that drops it would
otherwise fail silently (the filter simply becomes a no-op, and the page keeps
showing classes the visitor asked to hide).
"""
import re

from lang_detect import episode_lang

_ROW_RE = re.compile(r'<article class="episode"[^>]*>')
_LANG_RE = re.compile(r'data-ep-lang="(fr|he)"')


def _rows(html):
    return _ROW_RE.findall(html)


def test_every_episode_row_is_language_stamped(gen, channels, entries_un):
    enabled = [c for c in channels if c.get("enabled")]
    html = gen.render_page(enabled[0], entries_un, enabled, 4, 28, 21)
    rows = _rows(html)
    assert rows, "fixture rendered no episode rows"
    for row in rows:
        assert _LANG_RE.search(row), f"row without data-ep-lang: {row}"


def test_stamped_language_matches_lang_detect(gen, channels, entries_deux):
    """The stamp must be the SAME value the rest of the pipeline computes.

    home.json, search-index.json and mobile/*.json all derive their language
    from lang_detect.episode_lang(); if the page disagreed with them, the
    homepage and a channel page would hide different classes for one setting.
    """
    enabled = [c for c in channels if c.get("enabled")]
    ch = next(c for c in enabled if c["slug"] == "rav-test-deux")
    html = gen.render_page(ch, entries_deux, enabled, 4, 28, 21)

    stamped = _LANG_RE.findall(html)
    date_re = re.compile(r"^\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}$|^\d{1,2}\s+\w+\s+\d{4}$")
    expected = [
        episode_lang(ep, ch)
        for ep in sorted(entries_deux, key=lambda x: x["published"], reverse=True)
        if not date_re.match((ep.get("title") or "").strip())
    ]
    assert stamped == expected
