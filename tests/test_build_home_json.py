"""Tests for build_home_json — the pre-computed homepage payload."""
import json


def _run(gen, fixture_workdir, all_data, speakers, entries_cache):
    site_channels = len(all_data) + len(speakers)
    site_episodes = sum(len(e) for _, e in all_data)
    gen.build_home_json(all_data, speakers, entries_cache, site_channels, site_episodes)
    return json.loads((fixture_workdir / "home.json").read_text(encoding="utf-8"))


def test_home_json_structure(gen, fixture_workdir, all_data, speakers, entries_cache):
    home = _run(gen, fixture_workdir, all_data, speakers, entries_cache)
    assert set(home) >= {"generated_at", "stats", "channels", "speakers", "recents"}
    # `episodes_fr`/`episodes_he` are part of the contract: any UI that filters
    # on the course language needs a total it can actually display.
    assert set(home["stats"]) == {"channels", "episodes", "episodes_fr", "episodes_he"}
    assert home["stats"]["episodes_fr"] + home["stats"]["episodes_he"] == home["stats"]["episodes"]
    for r in home["recents"]:
        assert set(r) >= {"slug", "ch_name", "title", "published", "url"}


def test_home_recents_capped_at_home_recents_count(
    gen, fixture_workdir, all_data, speakers, entries_cache
):
    # Fixtures hold 27 non-HITAT episodes across the two channels (>20), so the
    # recents row must be capped at HOME_RECENTS_COUNT.
    total_non_hitat = sum(
        1
        for _, entries in all_data
        for ep in entries
        if "HITAT DU JOUR" not in (ep.get("title") or "").upper()
    )
    assert total_non_hitat > gen.HOME_RECENTS_COUNT  # guards the fixture itself
    home = _run(gen, fixture_workdir, all_data, speakers, entries_cache)
    assert len(home["recents"]) == gen.HOME_RECENTS_COUNT


def test_home_recents_sorted_desc(gen, fixture_workdir, all_data, speakers, entries_cache):
    home = _run(gen, fixture_workdir, all_data, speakers, entries_cache)
    dates = [r["published"] for r in home["recents"]]
    assert dates == sorted(dates, reverse=True)


def test_home_recents_excludes_hitat(gen, fixture_workdir, all_data, speakers, entries_cache):
    home = _run(gen, fixture_workdir, all_data, speakers, entries_cache)
    assert not any(
        "HITAT DU JOUR" in (r["title"] or "").upper() for r in home["recents"]
    )


def test_home_recents_urls_and_thumbs(gen, fixture_workdir, all_data, speakers, entries_cache):
    home = _run(gen, fixture_workdir, all_data, speakers, entries_cache)
    for r in home["recents"]:
        assert r["url"].endswith(".html")
        assert r["url"].startswith(r["slug"] + "/")


def test_home_speakers_only_when_matched(
    gen, fixture_workdir, all_data, speakers, entries_cache
):
    # "rav-invite-test" matches episodes in both channels; "rav-sans-episode"
    # matches none and must be dropped from the speakers list.
    home = _run(gen, fixture_workdir, all_data, speakers, entries_cache)
    slugs = {s["slug"] for s in home["speakers"]}
    assert "rav-invite-test" in slugs
    assert "rav-sans-episode" not in slugs
    for s in home["speakers"]:
        assert set(s) == {"slug", "name", "img", "count", "count_fr", "count_he"}
        # Same invariant as the channels: the per-language split is exhaustive,
        # so the homepage can show a language count without re-deriving it.
        assert s["count_fr"] + s["count_he"] == s["count"] > 0


def test_home_channels_counts_match(gen, fixture_workdir, all_data, speakers, entries_cache):
    home = _run(gen, fixture_workdir, all_data, speakers, entries_cache)
    counts = {c["slug"]: c["count"] for c in home["channels"]}
    for ch, entries in all_data:
        assert counts[ch["slug"]] == len(entries)


def test_home_stats_passthrough(gen, fixture_workdir, all_data, speakers, entries_cache):
    home = _run(gen, fixture_workdir, all_data, speakers, entries_cache)
    assert home["stats"]["channels"] == len(all_data) + len(speakers)
    assert home["stats"]["episodes"] == sum(len(e) for _, e in all_data)
