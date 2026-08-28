"""The generator publishes one derived feed per guest speaker.

Without it a speaker is reachable on the site (its page is generated) but not by
anything that resolves a rav by slug through feeds/<slug>.entries.json — which
is how the mobile app lists them, so the guests were simply missing there.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from feeds_util import channel_entry_files, speaker_slugs  # noqa: E402


def _feed(workdir, slug):
    return json.loads((workdir / "feeds" / f"{slug}.entries.json").read_text(encoding="utf-8"))


def test_speaker_feed_written_for_every_speaker(gen, fixture_workdir, speakers):
    gen.main()
    for sp in speakers:
        assert (fixture_workdir / "feeds" / f"{sp['slug']}.entries.json").exists(), sp["slug"]


def test_speaker_feed_holds_exactly_the_matched_episodes(gen, fixture_workdir,
                                                         entries_un, entries_deux):
    gen.main()
    feed = _feed(fixture_workdir, "rav-invite-test")

    expected = [
        ep for ep in entries_un + entries_deux
        if gen.speaker_matches(ep["title"], ["rav invite test", "invite test"])
    ]
    assert feed, "fixture speaker should match at least one episode"
    assert len(feed) == len(expected)
    assert {ep["video_id"] for ep in feed} == {ep["video_id"] for ep in expected}


def test_speaker_feed_entries_are_verbatim_channel_entries(gen, fixture_workdir, entries_un):
    """Same shape as a channel feed, so existing per-episode consumers work as-is."""
    gen.main()
    feed = _feed(fixture_workdir, "rav-invite-test")
    source = {ep["video_id"]: ep for ep in entries_un}
    shared = [ep for ep in feed if ep["video_id"] in source]
    assert shared
    for ep in shared:
        assert ep == source[ep["video_id"]]


def test_speaker_feed_is_sorted_newest_first(gen, fixture_workdir):
    gen.main()
    published = [ep["published"] for ep in _feed(fixture_workdir, "rav-invite-test")]
    assert published == sorted(published, reverse=True)


def test_speaker_without_matching_episode_gets_an_empty_feed(gen, fixture_workdir):
    gen.main()
    assert _feed(fixture_workdir, "rav-sans-episode") == []


def test_regeneration_is_byte_stable(gen, fixture_workdir):
    """CI regenerates hourly; an unstable feed would churn megabytes in git."""
    gen.main()
    path = fixture_workdir / "feeds" / "rav-invite-test.entries.json"
    first = path.read_bytes()
    gen.main()
    assert path.read_bytes() == first


def test_channel_entry_files_skips_derived_speaker_feeds(gen, fixture_workdir):
    """Per-episode jobs (tagging, R2 repair, durations) must not see the copies."""
    gen.main()
    assert speaker_slugs(fixture_workdir) == {"rav-invite-test", "rav-sans-episode"}

    slugs = {f.stem.replace(".entries", "")
             for f in channel_entry_files(fixture_workdir / "feeds", fixture_workdir)}
    assert slugs == {"rav-test-un", "rav-test-deux"}
