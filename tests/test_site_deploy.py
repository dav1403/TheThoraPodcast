"""Pure-logic tests for scripts/site_deploy.py and scripts/site_legacy.py
(Pages deployment in Actions mode). No network, no boto3 needed."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import site_deploy  # noqa: E402
import site_legacy  # noqa: E402


def test_freshness_first_deploy():
    ok, why = site_deploy.freshness_verdict(None, "a" * 40, 100, "b" * 40, 90, False)
    assert ok and "first" in why


def test_freshness_never_older():
    live = {"main_sha": "m2", "main_commit_time": 200, "state_commit": "s2", "state_commit_time": 190}
    assert site_deploy.freshness_verdict(live, "m1", 150, "s2", 190, True)[0] is False  # older main
    assert site_deploy.freshness_verdict(live, "m3", 250, "s1", 100, True)[0] is False  # older state
    assert site_deploy.freshness_verdict(live, "m3", 250, "s3", 240, False)[0] is True


def test_freshness_same_build():
    live = {"main_sha": "m", "main_commit_time": 200, "state_commit": "s", "state_commit_time": 190}
    assert site_deploy.freshness_verdict(live, "m", 200, "s", 190, False)[0] is False
    assert site_deploy.freshness_verdict(live, "m", 200, "s", 190, True)[0] is True  # manual force


def test_count_xml_feeds():
    names = ["a.xml", "a.entries.json", "b.xml", "transcripts", "x.channel_info.json"]
    assert site_deploy.count_xml_feeds(names) == 2
    assert site_deploy.count_xml_feeds(["sub/c.xml"]) == 0


def test_entries_regressions():
    ref = {"a.entries.json": 10, "b.entries.json": 5}
    assert site_deploy.entries_regressions({"a.entries.json": 10, "b.entries.json": 6}, ref) == []
    probs = site_deploy.entries_regressions({"a.entries.json": 9}, ref)
    assert len(probs) == 2
    assert any("missing" in p for p in probs) and any("9 episodes < 10" in p for p in probs)


def test_parse_legacy_list():
    text = "# comment\n\nrav-x/old-page-2022-01-01.html\n  rav-y/p.html  \n"
    assert site_legacy.parse_list(text) == ["rav-x/old-page-2022-01-01.html", "rav-y/p.html"]
    for bad in ("/abs.html", "a/../b.html", "feeds/a.xml"):
        with pytest.raises(ValueError):
            site_legacy.parse_list(bad)
    with pytest.raises(ValueError):
        site_legacy.parse_list("a/b.html\na/b.html\n")


def test_plan_legacy_sync():
    have = {"a/1.html": {"md5": "x"}}
    assert site_legacy.plan_sync(["a/1.html", "a/2.html", "a/2.html"], have) == ["a/2.html"]


def test_versioned_legacy_list():
    """The versioned list parses, holds the 383 pages of the phase-3 cut-over
    and never names a path the generator owns at the top level."""
    paths = site_legacy.parse_list(site_legacy.DEFAULT_LIST.read_text(encoding="utf-8"))
    assert len(paths) >= 383
    assert all("/" in p for p in paths)
    assert len({p.lower() for p in paths}) == len(paths)
