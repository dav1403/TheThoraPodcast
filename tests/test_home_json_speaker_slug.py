"""home.json must let "Mon fil" find the classes of a followed GUEST speaker.

A guest teaches on the HOST rav's channel, so every homepage row carries the
host's `slug`. Matching a follow on `slug` alone therefore made a followed guest
invisible on the homepage. build_home_json now bakes `speakerSlug` on every row
and a bounded `speaker_recents` list (the guest classes never reach the top-20
`recents`), and index.html keeps a row on `slug` OR `speakerSlug`.

These checks run against the committed artifact, like test_artifacts_integrity.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "home.json"
SPEAKERS = ROOT / "speakers.json"

_ROW_KEYS = {"slug", "ch_name", "title", "published", "url", "speakerSlug"}


@pytest.fixture(scope="module")
def home():
    if not HOME.exists():
        pytest.skip("home.json not present")
    return json.loads(HOME.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def speaker_slugs():
    if not SPEAKERS.exists():
        pytest.skip("speakers.json not present")
    return {s["slug"] for s in json.loads(SPEAKERS.read_text(encoding="utf-8"))}


def test_recents_carry_speaker_slug(home, speaker_slugs):
    for r in home["recents"]:
        assert "speakerSlug" in r, f"recents row without speakerSlug: {r.get('url')}"
        assert r["speakerSlug"] is None or r["speakerSlug"] in speaker_slugs


def test_speaker_recents_shape(home, speaker_slugs):
    rows = home.get("speaker_recents")
    if rows is None:
        pytest.skip("speaker_recents not present (home.json from an older revision)")
    assert isinstance(rows, list) and rows
    for r in rows:
        assert _ROW_KEYS <= set(r), f"speaker_recents row missing keys: {sorted(_ROW_KEYS - set(r))}"
        assert r["speakerSlug"] in speaker_slugs, f"unknown speaker slug: {r['speakerSlug']}"
        assert r["url"].endswith(".html")


def test_speaker_recents_bounded_and_sorted(home):
    rows = home.get("speaker_recents")
    if rows is None:
        pytest.skip("speaker_recents not present (home.json from an older revision)")
    per_speaker = {}
    for r in rows:
        per_speaker.setdefault(r["speakerSlug"], []).append(r["published"])
    for slug, dates in per_speaker.items():
        # Bounded: a homepage payload must stay small (HOME_SPEAKER_RECENTS_COUNT).
        assert len(dates) <= 5, f"{slug} has {len(dates)} rows in speaker_recents"
        assert dates == sorted(dates, reverse=True), f"{slug} rows are not newest-first"


def test_every_speaker_with_classes_is_represented(home):
    rows = home.get("speaker_recents")
    if rows is None:
        pytest.skip("speaker_recents not present (home.json from an older revision)")
    # home["speakers"] only lists speakers with >=1 matched episode, so each of
    # them must be reachable from "Mon fil".
    listed = {s["slug"] for s in home.get("speakers", [])}
    covered = {r["speakerSlug"] for r in rows}
    assert listed <= covered, f"speakers absent from speaker_recents: {sorted(listed - covered)}"
