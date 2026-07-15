"""Tests for sync_rss_channels.merge_entries.

Guards the re-sync merge: the hourly RSS sync must NOT drop fields that other
scripts add locally to feeds/<slug>.entries.json (notably `tags` from
tag_episodes.py). Regression test for the bug where the fresh RSS entry
replaced the stored one wholesale, wiping local enrichment every run.

sync_rss_channels imports `requests` at module load, which is not installed in
the CI test env (it only installs pytest). We stub it before import — the merge
function itself is pure stdlib.
"""
import sys
import types

# scripts/ is on sys.path via tests/conftest.py.
sys.modules.setdefault("requests", types.ModuleType("requests"))

import importlib

sync = importlib.import_module("sync_rss_channels")


def _entry(vid, **extra):
    e = {"video_id": vid, "title": f"t{vid}", "published": "2026-01-01T00:00:00+00:00"}
    e.update(extra)
    return e


def test_local_tags_preserved_on_resync():
    # The stored entry carries locally-added tags; the fresh RSS entry does not.
    existing = {"a": _entry("a", tags=["breslev", "emouna"])}
    new_entries = [_entry("a")]  # same episode, straight from RSS, no tags

    merged = sync.merge_entries(new_entries, existing)

    assert merged["a"]["tags"] == ["breslev", "emouna"], "local tags must survive re-sync"


def test_rss_is_authoritative_for_provided_fields():
    # When the RSS entry provides a field, it wins over the stored value.
    existing = {"a": _entry("a", title="old title", tags=["x"])}
    new_entries = [_entry("a", title="new title")]

    merged = sync.merge_entries(new_entries, existing)

    assert merged["a"]["title"] == "new title"   # RSS wins for fields it provides
    assert merged["a"]["tags"] == ["x"]           # local-only field preserved


def test_missing_from_rss_kept_as_is():
    # An episode still in the store but absent from the fresh feed is retained.
    existing = {"gone": _entry("gone", tags=["keep"])}
    new_entries = []

    merged = sync.merge_entries(new_entries, existing)

    assert merged["gone"]["tags"] == ["keep"]


def test_new_episode_added():
    existing = {"a": _entry("a")}
    new_entries = [_entry("a"), _entry("b")]

    merged = sync.merge_entries(new_entries, existing)

    assert set(merged) == {"a", "b"}
