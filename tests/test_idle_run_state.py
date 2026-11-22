"""An idle pipeline run must leave the state byte-identical, so that it mints
no new state generation and deploy-site skips the rebuild.

Measured before the fix (run 44900063055, 21/11/2026, 0 episode ingested):
`push: upload=28 (66.6 MB)` and a new generation, i.e. a full site rebuild.
The 28 files: the 25 channel feeds (feedgen stamps <lastBuildDate> = now),
feeds/ingest_state.json (`last_run`), feeds/rss_health.json (`checked_at`)...
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

feeds_util = importlib.import_module("feeds_util")
site_deploy = importlib.import_module("site_deploy")


# -- feeds: <lastBuildDate> only moves with the content ---------------------

FEED = (b"<?xml version='1.0' encoding='UTF-8'?>\n<rss><channel><title>T</title>"
        b"<lastBuildDate>%s</lastBuildDate><item>%s</item></channel></rss>\n")


def test_feed_with_only_a_new_date_is_left_untouched(tmp_path):
    p = tmp_path / "a.xml"
    assert feeds_util.write_feed_if_changed(p, FEED % (b"Sat, 21 Nov 2026 09:00:00 +0000", b"e1"))
    first = p.read_bytes()
    assert not feeds_util.write_feed_if_changed(p, FEED % (b"Sun, 22 Nov 2026 03:00:00 +0000", b"e1"))
    assert p.read_bytes() == first                       # old date kept, byte-identical


def test_feed_with_new_content_is_written_with_its_new_date(tmp_path):
    p = tmp_path / "a.xml"
    feeds_util.write_feed_if_changed(p, FEED % (b"Sat, 21 Nov 2026 09:00:00 +0000", b"e1"))
    new = FEED % (b"Sun, 22 Nov 2026 03:00:00 +0000", b"e2")
    assert feeds_util.write_feed_if_changed(p, new)
    assert p.read_bytes() == new


def test_build_rss_feed_twice_is_byte_identical(tmp_path, monkeypatch):
    """The real builder (feedgen): two builds of the same entries, a second
    apart, give the same file. Needs the pipeline deps (skipped in the light
    tests workflow, runs wherever feedgen/yt-dlp/boto3 are installed)."""
    for mod in ("feedgen", "yt_dlp", "boto3", "requests", "lxml"):
        pytest.importorskip(mod)
    for k, v in {"YOUTUBE_API_KEY": "x", "R2_ENDPOINT_URL": "https://r2.invalid",
                 "R2_ACCESS_KEY_ID": "x", "R2_SECRET_ACCESS_KEY": "x", "R2_BUCKET_NAME": "b",
                 "R2_PUBLIC_URL": "https://cdn.invalid", "GITHUB_TOKEN": "dummy",
                 "GITHUB_REPO": "owner/repo"}.items():
        monkeypatch.setenv(k, v)  # dummies: the module checks them at import, nothing is called
    monkeypatch.chdir(tmp_path)
    # Other tests import process_podcasts against stubbed deps: start clean.
    for name in [n for n in sys.modules if n == "process_podcasts" or n.split(".")[0] in
                 ("feedgen", "yt_dlp", "boto3", "botocore", "lxml")]:
        monkeypatch.delitem(sys.modules, name)
    pp = importlib.import_module("process_podcasts")
    cfg = {"slug": "lev", "podcast_author": "A", "podcast_email": "a@b.c"}
    info = {"title": "Lev", "description": "d", "thumbnail": ""}
    entries = [{"video_id": "v1", "title": "Ep 1", "published": "2026-11-20T10:00:00+00:00",
                "audio_url": "https://cdn.invalid/v1.mp3", "file_size": 1, "duration_secs": 60}]
    p = tmp_path / "lev.xml"
    pp.build_rss_feed(cfg, info, entries, p)
    first = p.read_bytes()
    assert b"<lastBuildDate>" in first
    import time
    time.sleep(1.1)
    pp.build_rss_feed(cfg, info, entries, p)
    assert p.read_bytes() == first
    pp.build_rss_feed(cfg, info, entries + [{**entries[0], "video_id": "v2", "title": "Ep 2"}], p)
    assert p.read_bytes() != first and b"Ep 2" in p.read_bytes()


# -- feeds/ingest_state.json: no rewrite for a timestamp alone --------------

def test_watchdog_identical_run_leaves_the_file_untouched(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    wd = importlib.import_module("ci_ingest_watchdog")
    monkeypatch.setattr(wd, "PRE_RUN_COUNTS", tmp_path / "pre.json")
    (tmp_path / "feeds").mkdir()
    (tmp_path / "feeds" / "a.entries.json").write_text("[1, 2]")
    (tmp_path / "pre.json").write_text(json.dumps({"a": 2}))
    wd.main()
    first = (tmp_path / "feeds" / "ingest_state.json").read_bytes()
    wd.main()
    assert (tmp_path / "feeds" / "ingest_state.json").read_bytes() == first
    # an ingested episode changes the counters: rewritten
    (tmp_path / "feeds" / "a.entries.json").write_text("[1, 2, 3]")
    wd.main()
    state = json.loads((tmp_path / "feeds" / "ingest_state.json").read_text())
    assert state["last_added"] == 1 and "last_nonzero_run" in state
    # the next run ingests nothing: untouched (last_added stays the last change)
    after_ingest = (tmp_path / "feeds" / "ingest_state.json").read_bytes()
    (tmp_path / "pre.json").write_text(json.dumps({"a": 3}))
    wd.main()
    assert (tmp_path / "feeds" / "ingest_state.json").read_bytes() == after_ingest
    # a zero run while work is pending moves the streak: rewritten
    (tmp_path / "backfill_state.json").write_text(json.dumps({"a": {"todo": ["x", "y"]}}))
    wd.main()
    state = json.loads((tmp_path / "feeds" / "ingest_state.json").read_text())
    assert state["consecutive_zero_runs"] == 1 and state["last_pending"] >= 1


# -- feeds/rss_health.json: checked_at = "unchanged since" ------------------

def test_rss_health_record_kept_when_only_checked_at_moves():
    pytest.importorskip("requests")
    srs = importlib.import_module("sync_rss_channels")
    old = {"slug": "x", "checked_at": "2026-11-21T00:00:00+00:00", "sample": 5, "failures": [], "status": "ok"}
    new = {**old, "checked_at": "2026-11-22T00:00:00+00:00"}
    assert srs.same_health_kept(old, new) is old
    degraded = {**new, "failures": [{"url": "u", "status": 404}], "status": "degraded"}
    assert srs.same_health_kept(old, degraded) is degraded
    assert srs.same_health_kept(None, new) is new


# -- deploy-site: skip when nothing a build reads has moved -----------------

LIVE = {"state_generation": 10, "main_commit_time": 100, "main_sha": "m",
        "stats_digest": "3:abc", "built_at": "2026-11-22T04:04:45+00:00"}


def test_freshness_skips_an_identical_build_and_deploys_on_any_input_change():
    v = site_deploy.freshness_verdict_v2
    same = dict(stats_digest="3:abc", today="2026-11-22")
    ok, why = v(LIVE, "m", 100, 10, False, **same)
    assert ok is False and "already deployed" in why
    assert v(LIVE, "m", 100, 11, False, **same)[0] is True             # new generation (ingestion)
    assert v(LIVE, "n", 101, 10, False, **same)[0] is True             # new source commit
    assert v(LIVE, "m", 100, 10, True, **same)[0] is True              # dispatch with force
    assert v(LIVE, "m", 100, 10, False, stats_digest="3:def", today="2026-11-22")[0] is True
    assert v(LIVE, "m", 100, 10, False, stats_digest="unavailable (X)", today="2026-11-22")[0] is True
    assert v(LIVE, "m", 100, 10, False, stats_digest="3:abc", today="2026-11-23")[0] is True
    assert v({**LIVE, "stats_digest": None}, "m", 100, 10, False, **same)[0] is True  # old marker
    assert v(LIVE, "m", 100, 9, True, **same)[0] is False              # never older, even forced
