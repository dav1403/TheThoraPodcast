"""Unit tests for the per-channel intro-trim mechanism (process_podcasts).

process_podcasts.py imports heavy runtime deps (boto3, yt_dlp, ...) and exits at
import time unless the pipeline env vars are set. We satisfy both here so the
tests run in CI; locally (where the deps are absent) they skip cleanly.
"""
import os
import sys
from pathlib import Path

import pytest

# process_podcasts sys.exit(1)s at import if these are missing.
os.environ.setdefault("YOUTUBE_API_KEY", "test")
os.environ.setdefault("GITHUB_TOKEN", "test")
os.environ.setdefault("GITHUB_REPO", "owner/repo")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

# Skip the whole module locally if the pipeline's runtime deps aren't installed.
pytest.importorskip("boto3")
pytest.importorskip("yt_dlp")

import process_podcasts as pp  # noqa: E402


def test_encode_cmd_no_trim_has_no_ss():
    cmd = pp.build_ffmpeg_encode_cmd(Path("in.webm"), Path("out.mp3"), 0)
    assert "-ss" not in cmd
    # Matches the historical command exactly (no behaviour change at 0).
    assert cmd == ["ffmpeg", "-i", "in.webm", "-vn", "-ar", "44100",
                   "-ac", "2", "-b:a", "128k", "out.mp3", "-y"]


def test_encode_cmd_with_trim_has_ss_before_input():
    cmd = pp.build_ffmpeg_encode_cmd(Path("in.webm"), Path("out.mp3"), 7)
    assert "-ss" in cmd
    ss_i = cmd.index("-ss")
    i_i = cmd.index("-i")
    assert cmd[ss_i + 1] == "7"
    assert ss_i < i_i, "-ss must come before -i for a fast input seek"


def test_channel_intro_trim_sec_parsing():
    assert pp.channel_intro_trim_sec({}) == 0                       # absent
    assert pp.channel_intro_trim_sec({"intro_trim_sec": 0}) == 0
    assert pp.channel_intro_trim_sec({"intro_trim_sec": 5}) == 5
    assert pp.channel_intro_trim_sec({"intro_trim_sec": "8"}) == 8  # string coerced
    assert pp.channel_intro_trim_sec({"intro_trim_sec": -3}) == 0   # negative clamped
    assert pp.channel_intro_trim_sec({"intro_trim_sec": None}) == 0
    assert pp.channel_intro_trim_sec({"intro_trim_sec": "bad"}) == 0


def test_apply_intro_trim_is_noop_at_zero(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("ffmpeg must NOT run when trim is 0")
    monkeypatch.setattr(pp.subprocess, "run", _boom)
    p = tmp_path / "episode.mp3"
    p.write_bytes(b"original-bytes")
    out = pp.apply_intro_trim(p, 0)
    assert out == p
    assert p.read_bytes() == b"original-bytes"  # untouched
