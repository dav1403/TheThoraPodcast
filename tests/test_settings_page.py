"""Guard for reglages.html and its injection point in js/utils.js.

Both were added by hand (no generator, no CI) in commit 522dedb1: a standalone
settings page (`reglages.html`) plus a shared entry point injected into
`js/utils.js` (`TTP_SETTINGS_URL`, `_appendSettingsRow`, the `nav-settings-link`
/ `mnav-settings-link` classes) so every page's nav menu links to it.

Nothing else in the test suite touches these files. If a future change rewrites
`js/utils.js` wholesale (the exact failure mode that has already happened once
on this repo — see ttp-dev-gotchas), the injection disappears silently with no
signal anywhere. This test is that signal: it does not validate behaviour, only
that the markers this work depends on are still present verbatim.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The preference keys this project owns (see TTPPrefs in js/utils.js).
# reglages.html must never grow a localStorage key of its own — settings are
# meant to flow through the existing TTPPrefs module, not duplicate it. The one
# direct read allowed is `ttp_theme`, by the inline <head> script that has to
# run before utils.js to avoid a white flash (see test_theme_* below).
_ALLOWED_LOCALSTORAGE_KEYS = {"lang", "ttp_course_lang", "ttp_theme"}
_LOCALSTORAGE_CALL_RE = re.compile(
    r"localStorage\.(?:setItem|getItem|removeItem)\(\s*['\"]([^'\"]+)['\"]"
)


def _read(path):
    return path.read_text(encoding="utf-8")


def test_settings_page_exists_at_root():
    page = ROOT / "reglages.html"
    assert page.is_file(), "reglages.html must live at the site root"


def test_settings_page_has_the_two_setting_groups():
    html = _read(ROOT / "reglages.html")
    assert 'id="set-ui"' in html, "missing the site-language control (#set-ui)"
    assert 'id="set-course"' in html, "missing the course-language control (#set-course)"


def test_settings_page_has_the_theme_and_speed_groups():
    html = _read(ROOT / "reglages.html")
    assert 'id="set-theme"' in html, "missing the theme control (#set-theme)"
    assert 'id="set-speed"' in html, "missing the playback-speed control (#set-speed)"


def test_settings_page_touches_no_localstorage_key_besides_the_known_two():
    html = _read(ROOT / "reglages.html")
    keys = set(_LOCALSTORAGE_CALL_RE.findall(html))
    assert keys, "expected at least one localStorage call in reglages.html"
    unexpected = keys - _ALLOWED_LOCALSTORAGE_KEYS
    assert not unexpected, (
        f"reglages.html references unexpected localStorage key(s) {unexpected}; "
        f"only {_ALLOWED_LOCALSTORAGE_KEYS} are allowed — settings must flow "
        "through the existing TTPPrefs module, not add their own storage"
    )


def test_utils_js_still_wires_the_settings_link():
    js = _read(ROOT / "js" / "utils.js")
    for marker in (
        "TTP_SETTINGS_URL",
        "_appendSettingsRow",
        "nav-settings-link",
        "mnav-settings-link",
    ):
        assert marker in js, (
            f"js/utils.js is missing `{marker}` — the reglages.html injection "
            "appears to have been dropped (e.g. by a wholesale rewrite of this "
            "file). Re-wire the settings link before merging."
        )


# ─── Dark theme ──────────────────────────────────────────────────────────────
# Pages that carry the dark palette ([data-theme="dark"]) must also carry the
# inline head script that sets <html data-theme> before first paint, and that
# script must be the same everywhere (static pages + generator templates) and
# read the very key TTPPrefs owns.
_THEME_PAGES = ["index.html", "derniers-cours.html", "rabbins.html",
                "mes-favoris.html", "reglages.html"]
# Secondary pages migrated afterwards (same mechanism). The list is explicit so
# that a page dropping out fails by name; test_every_root_page_is_theme_ready
# below also catches a NEW page that forgets the mechanism.
_SECONDARY_THEME_PAGES = [
    "paracha.html", "themes.html", "hayom-yom.html", "daf-hayomi.html", "hitat.html",
    "hiloula.html", "bein-hametsarim.html", "paracha-de-la-semaine.html", "embed.html",
    "cgv.html", "contact.html", "politique-confidentialite.html", "espace-rav.html",
    "tableau-de-bord.html", "episode.html",
]
_HEAD_SCRIPT_RE = re.compile(r"<script>\(function\(\)\{var t;try\{t=localStorage\.getItem\('ttp_theme'\).*?</script>")
# The canonical palette: light roles then dark overrides, identical everywhere.
_ROOT_BLOCK_RE = re.compile(r"    :root \{\n      /\* Page \*/.*?\n    \}", re.S)
_DARK_BLOCK_RE = re.compile(r"    \[data-theme=\"dark\"\] \{\n      color-scheme: dark;.*?\n    \}", re.S)
_GENERATORS = ["generate_channel_pages.py", "weekly_paracha_pages.py"]


def _all_theme_pages():
    weekly = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "paracha").glob("*.html"))
    return _THEME_PAGES + _SECONDARY_THEME_PAGES + weekly


def _assert_theme_ready(name, html):
    assert '[data-theme="dark"]' in html, f"{name}: dark palette missing"
    m = _HEAD_SCRIPT_RE.search(html)
    assert m, f"{name}: inline theme script missing"
    assert m.start() < html.index("<style"), f"{name}: theme script must run before the CSS"
    assert m.start() < html.index("</head>")
    return m.group(0)


def test_theme_key_is_owned_by_ttpprefs():
    js = _read(ROOT / "js" / "utils.js")
    assert re.search(r"theme:\s*\{\s*key:\s*'ttp_theme',\s*values:\s*\['auto', 'light', 'dark'\]", js)
    assert "window.TTPTheme" in js


def test_theme_ready_pages_set_the_theme_before_first_paint():
    scripts = set()
    for name in _THEME_PAGES:
        html = _read(ROOT / name)
        assert '[data-theme="dark"]' in html, f"{name}: dark palette missing"
        m = _HEAD_SCRIPT_RE.search(html)
        assert m, f"{name}: inline theme script missing"
        head_end = html.index("</head>")
        assert m.start() < html.index("<style"), f"{name}: theme script must run before the CSS"
        assert m.start() < head_end
        scripts.add(m.group(0))
    gen = _read(ROOT / "scripts" / "generate_channel_pages.py")
    m = _HEAD_SCRIPT_RE.search(gen)
    assert m, "generator: THEME_HEAD missing"
    scripts.add(m.group(0))
    assert gen.count("{THEME_HEAD}") == 2, "both generator templates must inject THEME_HEAD"
    assert len(scripts) == 1, "the inline theme script differs between pages"


def test_secondary_pages_set_the_theme_before_first_paint():
    scripts = set()
    pages = _all_theme_pages()
    assert any(p.startswith("paracha/") for p in pages), "no weekly paracha archive page found"
    for name in pages:
        scripts.add(_assert_theme_ready(name, _read(ROOT / name)))
    weekly = _read(ROOT / "scripts" / "weekly_paracha_pages.py")
    m = _HEAD_SCRIPT_RE.search(weekly)
    assert m, "weekly_paracha_pages.py: THEME_HEAD missing"
    scripts.add(m.group(0))
    assert weekly.count("{THEME_HEAD}") == 1, "the weekly paracha template must inject THEME_HEAD"
    scripts.add(_HEAD_SCRIPT_RE.search(_read(ROOT / "index.html")).group(0))
    assert len(scripts) == 1, "the inline theme script differs between pages"


def test_palette_blocks_are_identical_everywhere():
    # One palette: a role added or a dark value tuned on one page only would
    # leave the others silently out of step.
    ref = _read(ROOT / "index.html")
    ref_root, ref_dark = _ROOT_BLOCK_RE.search(ref).group(0), _DARK_BLOCK_RE.search(ref).group(0)
    sources = [(n, _read(ROOT / n)) for n in _all_theme_pages()]
    sources += [(g, _read(ROOT / "scripts" / g)) for g in _GENERATORS]
    for name, text in sources:
        root, dark = _ROOT_BLOCK_RE.search(text), _DARK_BLOCK_RE.search(text)
        assert root and root.group(0) == ref_root, f"{name}: :root palette differs from index.html"
        assert dark and dark.group(0) == ref_dark, f"{name}: dark palette differs from index.html"


def test_every_root_page_is_theme_ready():
    # A new static page with its own CSS must join the theme (or be listed
    # here with the reason it cannot).
    exempt = set()
    known = set(_THEME_PAGES + _SECONDARY_THEME_PAGES)
    for page in sorted(ROOT.glob("*.html")):
        html = _read(page)
        if "<style" not in html or page.name in exempt:
            continue
        assert page.name in known, f"{page.name}: page with CSS not covered by the dark theme tests"
        _assert_theme_ready(page.name, html)
