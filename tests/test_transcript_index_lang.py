"""The full-text index doc rows must carry the language of the class.

`search-fts/docs/<n>.json` is what the search UI renders; without a language on
each doc it cannot honour a "French only / Hebrew only" filter without
downloading something else. The field is appended LAST so an older client that
reads five fields keeps working.
"""
import json

import pytest

import build_transcript_index as bti

CHANNELS = [
    {"slug": "rav-fr", "podcast_author": "Rav FR", "podcast_language": "fr", "enabled": True},
    {"slug": "rav-he", "podcast_author": "Rav HE", "podcast_language": "he", "enabled": True},
]
ENTRIES = {
    "rav-fr": [{"video_id": "aaa", "title": "Les lois du Chabbat",
                "published": "2026-01-02T10:00:00+00:00"}],
    "rav-he": [{"video_id": "bbb", "title": "שיעור בהלכות שבת",
                "published": "2026-01-03T10:00:00+00:00"},
               # Latin title on a Hebrew channel → a French class.
               {"video_id": "ccc", "title": "Cours en francais du rav",
                "published": "2026-01-04T10:00:00+00:00"}],
}
TRANSCRIPTS = {
    "aaa": "bonjour voici le cours sur les lois du chabbat allumage bougies " * 5,
    "bbb": "שלום זהו השיעור בהלכות שבת הדלקת נרות קידוש " * 5,
    "ccc": "voici un autre cours en francais sur la priere du matin " * 5,
}


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    (tmp_path / "channels.json").write_text(json.dumps(CHANNELS), encoding="utf-8")
    feeds = tmp_path / "feeds"
    (feeds / "transcripts").mkdir(parents=True)
    for slug, entries in ENTRIES.items():
        (feeds / f"{slug}.entries.json").write_text(json.dumps(entries), encoding="utf-8")
    for vid, text in TRANSCRIPTS.items():
        (feeds / "transcripts" / f"{vid}.txt").write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bti.sys, "argv", ["build_transcript_index.py", "--force"])
    return tmp_path


def test_doc_rows_carry_the_class_language(corpus):
    bti.main()
    docs = json.loads((corpus / "search-fts" / "docs" / "0.json").read_text(encoding="utf-8"))
    by_vid = {d[0]: d for d in docs if d}
    assert len(by_vid) == 3
    for row in by_vid.values():
        assert len(row) == 6, "lang is appended after [vid, title, channel, url, date]"
    assert by_vid["aaa"][5] == "fr"
    assert by_vid["bbb"][5] == "he"
    assert by_vid["ccc"][5] == "fr", "a latin title on a hebrew channel is a french class"


def test_manifest_reports_the_language_split(corpus):
    bti.main()
    manifest = json.loads(
        (corpus / "search-fts" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["n_docs_by_lang"] == {"fr": 2, "he": 1}
    assert sum(manifest["n_docs_by_lang"].values()) == manifest["n_docs"]


def test_doc_ids_survive_the_index_version_bump(corpus):
    """A format bump must NOT renumber documents: renumbering rewrites all
    ~17 000 term shards in git for nothing."""
    bti.main()
    state_path = corpus / "search-fts" / ".build-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    ids = dict(state["doc_ids"])
    state["index_version"] = bti.INDEX_VERSION - 1  # pretend the previous build was older
    state_path.write_text(json.dumps(state), encoding="utf-8")

    bti.main()
    assert dict(json.loads(state_path.read_text(encoding="utf-8"))["doc_ids"]) == ids
