"""Phase 4 deploy guards of scripts/site_deploy.py (no git reference): pure logic."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import site_deploy  # noqa: E402


def test_freshness_v2_orders_by_generation():
    live = {"state_generation": 10, "main_commit_time": 100, "main_sha": "m"}
    v = site_deploy.freshness_verdict_v2
    assert v(None, "m", 1, 1, False)[0] is True
    assert v(live, "m", 100, 9, True)[0] is False           # older state, even forced
    assert v(live, "x", 99, 11, True)[0] is False           # older source
    assert v(live, "m", 100, 10, False)[0] is False         # identical
    assert v(live, "m", 100, 10, True)[0] is True           # forced redeploy
    assert v(live, "m", 100, 11, False)[0] is True          # same source, newer state
    assert v({"main_sha": "m", "main_commit_time": 5}, "m", 5, 1, False)[0] is True  # phase-3 marker


def test_episode_pages_only_slug_dirs():
    paths = ["rav-a/x.html", "rav-a/sub/y.html", "lev/z.html", "index.html", "feeds/a.xml", "other/w.html"]
    assert site_deploy.episode_pages(paths, ["rav-a", "lev"]) == ["lev/z.html", "rav-a/x.html"]


def test_guard_problems():
    prev = {"xml_feeds": 3, "entries": {"a.entries.json": 10, "b.entries.json": 5}, "files": 1000}
    ok = {"xml_feeds": 3, "entries": {"a.entries.json": 11, "b.entries.json": 5}, "files": 1001}
    g = site_deploy.guard_problems
    assert g(None, ok) == [] and g(prev, ok) == []
    assert g(prev, {**ok, "xml_feeds": 2})
    assert g(prev, {**ok, "entries": {"a.entries.json": 9, "b.entries.json": 5}})
    assert g(prev, {**ok, "entries": {"a.entries.json": 10}})
    assert g(prev, {**ok, "files": 900})
    # a deliberate removal (channel disabled) passes with allow_shrink, a recul never does
    assert g(prev, {**ok, "xml_feeds": 2, "entries": {"a.entries.json": 10}, "files": 900}, True) == []
    assert g(prev, {**ok, "entries": {"a.entries.json": 9, "b.entries.json": 5}}, True)


def test_build_summary(tmp_path):
    (tmp_path / "feeds").mkdir()
    (tmp_path / "feeds" / "a.entries.json").write_text(json.dumps([1, 2, 3]))
    (tmp_path / "feeds" / "a.xml").write_text("<rss/>")
    (tmp_path / "channels.json").write_text(json.dumps([{"slug": "a"}]))
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "ep.html").write_text("x")
    paths = site_deploy._site_paths(tmp_path)
    s = site_deploy.build_summary(tmp_path, paths)
    assert s["files"] == 4 and s["xml_feeds"] == 1 and s["entries"] == {"a.entries.json": 3}
    assert s["slugs"] == ["a"] and set(s["feeds_md5"]) == {"a.xml"}
    assert site_deploy.episode_pages(paths, s["slugs"]) == ["a/ep.html"]
