"""first_text(): a thinking block in content[0] must never crash the callers
(social_post, fetch_channel_info, tag_episodes, weekly_paracha)."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from llm_util import first_text  # noqa: E402


def _msg(*blocks):
    return SimpleNamespace(content=[SimpleNamespace(**b) for b in blocks])


def test_skips_leading_thinking_block():
    msg = _msg({"type": "thinking", "thinking": "..."}, {"type": "text", "text": "  hello \n"})
    assert first_text(msg) == "hello"


def test_plain_text_response():
    assert first_text(_msg({"type": "text", "text": "[[\"Paracha\"]]"})) == '[["Paracha"]]'


def test_no_text_block_returns_empty():
    assert first_text(_msg({"type": "thinking", "thinking": "..."})) == ""
    assert first_text(SimpleNamespace(content=[])) == ""
    assert first_text(SimpleNamespace(content=None)) == ""


def test_social_post_generate_text_falls_back_on_thinking_only(monkeypatch):
    import social_post as sp

    class _Client:
        def __init__(self, **_):
            self.messages = SimpleNamespace(
                create=lambda **_: _msg({"type": "thinking", "thinking": "..."}))

    monkeypatch.setattr(sp, "ANTHROPIC_KEY", "x")
    monkeypatch.setattr(sp, "_anthropic", SimpleNamespace(Anthropic=_Client))
    assert sp.generate_text("prompt") is None
