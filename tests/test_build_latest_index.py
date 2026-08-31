"""Tests for build_latest_index — the pre-computed index derniers-cours.html reads.

Before it existed the page downloaded the 22 `feeds/<slug>.entries.json` on
boot: 33 672 889 bytes raw / 4 573 683 gzipped (measured 31/08/2026) to paint
30 rows. The regression that matters here is silent: drop a field and the page
still renders, just without artwork, without a play button, or with a chip that
answers "no episode".
"""
import json

import pytest


def _run(gen, fixture_workdir, all_data, speakers, entries_cache):
    gen.build_latest_index(all_data, speakers, entries_cache)
    return json.loads((fixture_workdir / "latest.json").read_text(encoding="utf-8"))


@pytest.fixture
def latest(gen, fixture_workdir, all_data, speakers, entries_cache):
    return _run(gen, fixture_workdir, all_data, speakers, entries_cache)


def test_shape(latest):
    assert set(latest) >= {
        "generated_at", "total", "total_fr", "total_he",
        "channels", "speakers", "episodes",
    }
    assert latest["total_fr"] + latest["total_he"] == latest["total"]


def test_every_row_carries_what_the_list_item_needs(latest):
    # The page renders artwork, a date, a duration, a link and a play button
    # straight from these rows — one missing key is a silently degraded item.
    for row in latest["episodes"]:
        assert set(row) >= {
            "slug", "ch_name", "title", "published", "thumbnail", "audio_url",
            "video_id", "url", "description", "duration_secs", "lang",
        }
        assert row["url"].endswith(".html")
        assert row["lang"] in ("fr", "he")
        # The list shows description.slice(0, 200); anything beyond is payload
        # every visitor pays for and nobody sees.
        assert len(row["description"]) <= 200


def test_episodes_sorted_newest_first(latest):
    dates = [r["published"] for r in latest["episodes"]]
    assert dates == sorted(dates, reverse=True)


def test_hitat_is_excluded_everywhere(latest, all_data):
    # HITAT DU JOUR lives on hitat.html; the page has always dropped it, so the
    # index must too — including in the counts, or a chip/total would promise
    # classes the list never shows.
    assert any(
        "HITAT DU JOUR" in (ep.get("title") or "").upper()
        for _, entries in all_data for ep in entries
    ), "fixture no longer contains a HITAT episode"
    for row in latest["episodes"]:
        assert "HITAT DU JOUR" not in row["title"].upper()
    total_non_hitat = sum(
        1 for _, entries in all_data for ep in entries
        if "HITAT DU JOUR" not in (ep.get("title") or "").upper()
    )
    assert latest["total"] == total_non_hitat


def test_capped_at_latest_index_count(gen, fixture_workdir, all_data, speakers,
                                      entries_cache, monkeypatch):
    monkeypatch.setattr(gen, "LATEST_INDEX_COUNT", 5)
    latest = _run(gen, fixture_workdir, all_data, speakers, entries_cache)
    assert len(latest["episodes"]) == 5
    # The index is a slice; the announced catalogue size is not.
    assert latest["total"] > 5


def test_channel_chips_carry_per_language_counts(latest, all_data):
    slugs = {ch["slug"] for ch, _ in all_data}
    assert {c["slug"] for c in latest["channels"]} == slugs
    for c in latest["channels"]:
        assert set(c) >= {"slug", "name", "podcast_language",
                          "count", "count_fr", "count_he"}
        # Without these the page cannot tell whether a chip would be empty under
        # the visitor's course-language preference.
        assert c["count_fr"] + c["count_he"] == c["count"]


def test_speaker_chips_are_usable_on_their_own(latest):
    by_slug = {s["slug"]: s for s in latest["speakers"]}
    # A guest with no matching class gets no chip at all.
    assert "rav-sans-episode" not in by_slug
    sp = by_slug["rav-invite-test"]
    assert sp["count"] > 0
    assert sp["count_fr"] + sp["count_he"] == sp["count"]
    # `art_slug`: guests have no artwork of their own, the chip borrows the host
    # channel's. `from_channels` + `title_patterns`: the page lazy-loads those
    # feeds when the chip is picked and must tag the entries the same way.
    assert sp["art_slug"] in sp["from_channels"]
    assert sp["title_patterns"]


def test_matched_guests_are_precomputed_on_the_rows(latest):
    tagged = [r for r in latest["episodes"] if r.get("sp")]
    assert tagged, "no episode carries its matched guests — the `sp` field is gone"
    for row in tagged:
        assert "rav-invite-test" in row["sp"]
        assert "invite test" in row["title"].lower()
    # And only those: an untagged row must really not match.
    for row in latest["episodes"]:
        if "invite test" in row["title"].lower():
            assert row.get("sp")


def test_json_is_compact(fixture_workdir, latest):
    # separators=(",", ":"): the file is downloaded by every visitor of the
    # page, indentation would be pure weight.
    raw = (fixture_workdir / "latest.json").read_text(encoding="utf-8")
    assert '", "' not in raw and ": " not in raw.split("\n")[0][:200]
