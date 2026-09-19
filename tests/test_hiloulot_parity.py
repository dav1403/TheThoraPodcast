"""The hiloulot list lives in two places that must stay in lockstep.

`hiloula.html` (site, client-side `const HILOULOT = [...]`) and
`scripts/build_mobile_index.py` (app, manual port of the same array). The app
addresses each tsadik BY POSITION (`mobile/hiloula.json` keys "0".."N"), so a
divergence in order, dates or keywords silently shows the wrong classes or the
wrong date in the app. These tests fail as soon as the two copies drift.
"""
import re
from pathlib import Path

import build_mobile_index as bmi

ROOT = Path(__file__).resolve().parents[1]

_JS_STR = r"'((?:\\.|[^'\\])*)'"


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", r"\1", s)


def _site_hiloulot() -> list[dict]:
    html = (ROOT / "hiloula.html").read_text(encoding="utf-8")
    block = re.search(r"const HILOULOT = \[(.*?)\n\s*\];", html, re.S)
    assert block, "HILOULOT array not found in hiloula.html"
    out = []
    for line in block.group(1).splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        fr = re.search(r"\bfr:" + _JS_STR, line)
        he = re.search(r"\bhe:" + _JS_STR, line)
        extra = re.search(r"\bextra:" + _JS_STR, line)
        hm = re.search(r"\bhm:" + _JS_STR, line)
        hd = re.search(r"\bhd:(\d+)", line)
        kw = re.search(r"\bkw:\[(.*)\]", line)
        assert fr and he and hm and hd and kw, f"unparsable hiloula line: {line}"
        out.append({
            "fr": _unescape(fr.group(1)),
            "he": _unescape(he.group(1)),
            "extra": _unescape(extra.group(1)) if extra else None,
            "hm": hm.group(1),
            "hd": int(hd.group(1)),
            "kw": [_unescape(k) for k in re.findall(_JS_STR, kw.group(1))],
        })
    return out


def test_same_entries_in_same_order():
    site = _site_hiloulot()
    app = bmi.HILOULOT
    assert len(site) == len(app)
    for i, (s, a) in enumerate(zip(site, app)):
        for key in ("fr", "he", "extra", "hm", "hd", "kw"):
            assert s[key] == a[key], f"index {i} differs on {key!r}: site={s[key]!r} app={a[key]!r}"


def test_no_mixed_ben_baba_netivot_entry():
    # 19/09/2026: index 9 mixed R. Yehuda ben Bava and the Netivot HaMishpat
    # under a wrong date (25 Cheshvan). It was corrected in place, not removed.
    entry = bmi.HILOULOT[9]
    assert "ben Baba" not in entry["fr"]
    assert (entry["hm"], entry["hd"]) == ("Iyyar", 25)
    # Bare 'netivot' matches the town of Netivot, not the book.
    assert "netivot" not in entry["kw"]
