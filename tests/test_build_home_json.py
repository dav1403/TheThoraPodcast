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
        assert set(s) == {
            "slug", "name", "img", "count", "count_fr", "count_he",
            # Last class of the guest — same contract as the channels, so the app
            # can show/sort it without pulling the feeds. The fixture speakers are
            # mono-language, hence no _fr/_he variants here (see _last_class_block).
            "last_published", "last_title", "last_video_id",
            "last_audio_url", "last_duration_secs",
        }
        # Same invariant as the channels: the per-language split is exhaustive,
        # so the homepage can show a language count without re-deriving it.
        assert s["count_fr"] + s["count_he"] == s["count"] > 0


def test_home_channels_counts_match(gen, fixture_workdir, all_data, speakers, entries_cache):
    home = _run(gen, fixture_workdir, all_data, speakers, entries_cache)
    counts = {c["slug"]: c["count"] for c in home["channels"]}
    for ch, entries in all_data:
        assert counts[ch["slug"]] == len(entries)


def test_home_last_class_matches_newest_episode(
    gen, fixture_workdir, all_data, speakers, entries_cache
):
    """Each channel carries its most recent episode verbatim.

    The app draws the rav panel (date + title) and the "Dernier cours" sort from
    these fields alone — computing them client-side would mean pulling the 22
    entries.json feeds (~20 MB).
    """
    home = _run(gen, fixture_workdir, all_data, speakers, entries_cache)
    by_slug = {c["slug"]: c for c in home["channels"]}
    for ch, entries in all_data:
        newest = max(entries, key=lambda e: e.get("published") or "")
        out = by_slug[ch["slug"]]
        assert out["last_published"] == newest["published"]
        # Titles are copied as-is: never translated, never truncated.
        assert out["last_title"] == newest["title"]
        assert out["last_video_id"] == newest["video_id"]
        assert out["last_audio_url"] == newest["audio_url"]
        assert out["last_duration_secs"] == newest["duration_secs"]


def test_home_last_class_null_when_no_episode(gen):
    """A rav with no episode yields nulls — never an invented date or title."""
    assert gen._last_class_block([]) == {
        "last_published": None, "last_title": None, "last_video_id": None,
        "last_audio_url": None, "last_duration_secs": None,
    }


def test_home_last_class_per_language_only_when_bilingual(gen):
    """`last_*_fr` / `last_*_he` exist only for a rav teaching in both languages.

    For a mono-language rav the suffixed block would duplicate the main one byte
    for byte, and home.json is loaded at startup by the app AND the homepage.
    """
    fr = {"published": "2026-06-10T00:00:00+00:00", "title": "Cours FR",
          "video_id": "fr1", "audio_url": "u_fr", "duration_secs": 10}
    he = {"published": "2026-06-01T00:00:00+00:00", "title": "שיעור",
          "video_id": "he1", "audio_url": "u_he", "duration_secs": 20}

    mono = gen._last_class_block([(fr, "fr")])
    assert not any(k.endswith(("_fr", "_he")) for k in mono)
    assert mono["last_title"] == "Cours FR"

    bil = gen._last_class_block([(fr, "fr"), (he, "he")])
    assert bil["last_title"] == "Cours FR"          # newest, all languages
    assert bil["last_title_fr"] == "Cours FR"
    assert bil["last_title_he"] == "שיעור"          # newest Hebrew class
    assert bil["last_published_he"] == he["published"]
    assert bil["last_audio_url_he"] == "u_he"


def test_home_last_class_keeps_hitat(gen):
    """Unlike `recents`, the last class does NOT drop HITAT DU JOUR.

    These fields feed the "Dernier cours" sort, whose reference implementation
    (latestEpisode() in rabbins.html) has no such filter — dropping it here would
    make the app disagree with the site.
    """
    hitat = {"published": "2026-06-20T00:00:00+00:00", "title": "HITAT DU JOUR — 20/06",
             "video_id": "h1", "audio_url": "u_h", "duration_secs": 5}
    other = {"published": "2026-06-19T00:00:00+00:00", "title": "Cours ordinaire",
             "video_id": "o1", "audio_url": "u_o", "duration_secs": 6}
    assert gen._last_class_block([(hitat, "fr"), (other, "fr")])["last_video_id"] == "h1"


def test_home_stats_passthrough(gen, fixture_workdir, all_data, speakers, entries_cache):
    home = _run(gen, fixture_workdir, all_data, speakers, entries_cache)
    assert home["stats"]["channels"] == len(all_data) + len(speakers)
    assert home["stats"]["episodes"] == sum(len(e) for _, e in all_data)
