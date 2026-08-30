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

# The only two preference keys this project owns (see TTPPrefs in js/utils.js).
# reglages.html must never grow a localStorage key of its own — settings are
# meant to flow through the existing TTPPrefs module, not duplicate it.
_ALLOWED_LOCALSTORAGE_KEYS = {"lang", "ttp_course_lang"}
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
