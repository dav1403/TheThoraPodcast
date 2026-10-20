"""GA4 listening events: one emitter, in TTPPlayer (js/utils.js).

scripts/stats_core.py counts site plays from the GA4 events `audio_play` /
`audio_complete` with the custom dimensions `rav` and `ep_title`. Until
21/10/2026 only the rav-page template emitted them, so plays started from
episode pages, the home, the paracha pages or legacy pages were never
counted (4 site plays in 30 days against 83k downloads).

* static guards (always run): the emitter lives in TTPPlayer and the
  generated rav pages no longer carry a second one (double count);
* browser check (Playwright + Chromium, skipped when not installed): a real
  play through load() / attach() / a legacy page fires each event once per
  episode with the expected parameters.
"""
import base64
import io
import os
import struct
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UTILS = ROOT / "js" / "utils.js"


def test_emitter_lives_in_ttpplayer():
    src = UTILS.read_text(encoding="utf-8")
    assert "_gaSend('audio_play'" in src
    assert "_gaSend('audio_complete'" in src
    # wired on every <audio> the bar binds, with the dimensions stats_core reads
    assert "_gaPlay(audio)" in src and "_gaComplete(audio, true)" in src
    assert "rav: info.rav, ep_title: info.ep_title" in src
    assert "typeof gtag === 'function'" in src


def test_rav_and_speaker_pages_have_no_second_emitter(gen, channels, speakers, entries_un):
    enabled = [c for c in channels if c.get("enabled")]
    html = gen.render_page(enabled[0], entries_un, enabled, 4, 28, 21)
    assert "'audio_play'" not in html and "'audio_complete'" not in html
    ep = next(e for e in entries_un if e.get("audio_url"))
    ep_html = gen.render_episode_page(ep, enabled[0], entries_un, enabled)
    assert "'audio_play'" not in ep_html and "'audio_complete'" not in ep_html


def _wav_data_uri(seconds: float = 0.6) -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(struct.pack("<h", 0) * int(8000 * seconds))
    return "data:audio/wav;base64," + base64.b64encode(buf.getvalue()).decode()


_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<script>window.GA=[];function gtag(){GA.push(Array.prototype.slice.call(arguments));}</script>
</head><body>%BODY%</body></html>"""


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            # TTP_CHROMIUM: path to an already-installed Chromium when the one
            # pinned by the local playwright version is not downloaded.
            exe = os.environ.get("TTP_CHROMIUM") or None
            b = p.chromium.launch(executable_path=exe,
                                  args=["--autoplay-policy=no-user-gesture-required"])
        except Exception as exc:  # pragma: no cover - browser not installed
            pytest.skip(f"chromium unavailable: {exc}")
        yield b
        b.close()


def _open(browser, body: str):
    page = browser.new_page()
    # A real origin: utils.js needs localStorage, denied on about:blank.
    html = _PAGE.replace("%BODY%", body)
    page.route("https://ttp.test/**", lambda r: r.fulfill(status=200, content_type="text/html", body=html))
    page.goto("https://ttp.test/page.html")
    page.add_script_tag(content=UTILS.read_text(encoding="utf-8"))
    page.wait_for_function("window.TTPPlayer && window.TTPPlayer.el()")
    return page


def _events(page, n: int, timeout: int = 8000):
    page.wait_for_function(f"GA.filter(a => a[0] === 'event' && /^audio_/.test(a[1])).length >= {n}",
                           timeout=timeout)
    page.wait_for_timeout(300)  # let a would-be duplicate arrive
    return page.evaluate("GA.filter(a => a[0] === 'event' && /^audio_/.test(a[1]))")


def test_load_fires_play_then_complete_once(browser):
    wav = _wav_data_uri()
    page = _open(browser, "")
    page.evaluate("""(src) => TTPPlayer.load({id: 'vid1', title: 'Cours 1', channel: 'Lev',
                                              art: '', src: src})""", wav)
    evs = _events(page, 2)
    assert evs == [["event", "audio_play", {"rav": "Lev", "ep_title": "Cours 1"}],
                   ["event", "audio_complete", {"rav": "Lev", "ep_title": "Cours 1"}]]
    # replaying the same class in the same page load is not a new listen
    page.evaluate("TTPPlayer.audio().currentTime = 0; TTPPlayer.audio().play()")
    page.wait_for_timeout(1200)
    assert len(page.evaluate("GA.filter(a => /^audio_/.test(a[1]))")) == 2
    page.close()


def test_attach_from_page_play_handler(browser):
    # Episode pages: the page's own 'play' listener calls attach() (resume
    # banner path), i.e. after the bar could have listened for this start.
    wav = _wav_data_uri()
    page = _open(browser, f'<audio id="ep-audio" src="{wav}"></audio>')
    page.evaluate("""() => {
        const a = document.getElementById('ep-audio');
        a.addEventListener('play', () => TTPPlayer.attach(a, {id: 'vid2', title: 'Cours 2',
                                                              channel: 'Rav X', src: a.getAttribute('src')}));
        a.play();
    }""")
    evs = _events(page, 2)
    assert [e[1] for e in evs] == ["audio_play", "audio_complete"]
    assert evs[0][2] == {"rav": "Rav X", "ep_title": "Cours 2"}
    page.close()


def test_legacy_page_falls_back_on_bar_text(browser):
    # derniers-cours.html drives #player-audio itself and never calls load().
    wav = _wav_data_uri()
    body = ('<div id="player" class="hidden"><audio id="player-audio"></audio>'
            '<div id="player-title"></div><div id="player-channel"></div></div>')
    page = _open(browser, body)
    page.evaluate("""(src) => {
        document.getElementById('player-title').textContent = 'Cours 3';
        document.getElementById('player-channel').textContent = 'Rav Y';
        const a = document.getElementById('player-audio');
        a.src = src; a.play();
    }""", wav)
    evs = _events(page, 1)
    assert evs[0] == ["event", "audio_play", {"rav": "Rav Y", "ep_title": "Cours 3"}]
    page.close()
