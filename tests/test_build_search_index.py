"""Tests for build_search_index — the full-catalog search payload."""
import json


def _run(gen, fixture_workdir, all_data):
    gen.build_search_index(all_data)
    return json.loads((fixture_workdir / "search-index.json").read_text(encoding="utf-8"))


def test_index_required_fields(gen, fixture_workdir, all_data):
    index = _run(gen, fixture_workdir, all_data)
    assert index, "search index should not be empty"
    for e in index:
        assert set(e) == {"t", "c", "u", "d", "l"}
        assert e["l"] in ("fr", "he")  # language of the CLASS, not of the UI
        assert e["t"], "title must be non-empty"
        assert e["u"], "url must be non-empty"
        assert e["u"].endswith(".html")
        assert len(e["d"]) == 10  # YYYY-MM-DD


def test_index_covers_whole_catalog_including_hitat(gen, fixture_workdir, all_data):
    # Every episode with both a title AND a published date is indexed — HITAT
    # included (unlike home.json which drops it from recents).
    expected = sum(
        1
        for _, entries in all_data
        for ep in entries
        if (ep.get("title") or "") and (ep.get("published") or "")
    )
    index = _run(gen, fixture_workdir, all_data)
    assert len(index) == expected
    assert any("HITAT DU JOUR" in e["t"].upper() for e in index), (
        "HITAT episodes must be searchable"
    )


def test_index_urls_match_ep_path(gen, fixture_workdir, all_data):
    index = _run(gen, fixture_workdir, all_data)
    by_slug = {ch["slug"]: (ch, entries) for ch, entries in all_data}
    # Reconstruct the expected URL for one known episode via the public helper.
    ch, entries = by_slug["rav-test-un"]
    ep = entries[0]
    expected_url = gen.ep_path(ch["slug"], ep)
    assert any(e["u"] == expected_url for e in index)


def test_index_skips_entries_without_title_or_date(gen, fixture_workdir, all_data):
    # Inject a malformed entry into a copy of the data; it must be skipped so the
    # generated URL never points to a page that main() would not have rendered.
    ch, entries = all_data[0]
    poisoned = list(entries) + [
        {"title": "", "published": "2026-06-01T00:00:00+00:00", "video_id": "x"},
        {"title": "Sans date", "published": "", "video_id": "y"},
    ]
    data = [(ch, poisoned), all_data[1]]
    index = _run(gen, fixture_workdir, data)
    assert not any(e["t"] == "" for e in index)
    assert not any(e["t"] == "Sans date" for e in index)
