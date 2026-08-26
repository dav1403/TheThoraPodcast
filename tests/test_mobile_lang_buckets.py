"""Per-language filling of the mobile buckets.

The regression this guards against: buckets are capped (300 classes per theme,
250 per parasha, …) and filled newest-first. If a language filter were applied
*after* the cap, a theme whose N most recent classes are all Hebrew would come
out EMPTY for a French-speaking user. The cap is therefore enforced per
language, and the manifest carries `total_fr`/`total_he` so the counters the
user reads stay exact under a filter.
"""
import json

import pytest

import build_mobile_index as bmi

CHANNEL = {"slug": "rav-test", "podcast_author": "Rav Test", "podcast_language": "fr"}
PREFIX = "https://audio.example.com/rav-test/"


def _ep(n: int, title: str, tags):
    return {
        "video_id": f"vid{n:03d}",
        "title": title,
        # Newest first = highest n.
        "published": f"2026-01-{n:02d}T10:00:00+00:00",
        "audio_url": f"{PREFIX}{n:03d}.mp3",
        "duration_secs": 3600,
        "thumbnail": f"https://i.ytimg.com/vi/vid{n:03d}/maxresdefault.jpg",
        "tags": tags,
    }


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _build(entries):
    bmi.build_mobile_index([(CHANNEL, entries)])


def _read(workdir, name):
    return json.loads((workdir / "mobile" / name).read_text(encoding="utf-8"))


def test_hebrew_flood_does_not_starve_the_french_bucket(workdir, monkeypatch):
    monkeypatch.setattr(bmi, "CAP_THEME", 2)
    # 5 Hebrew classes are the most recent, 3 French ones are older: with a
    # global cap of 2 the bucket would be 100 % Hebrew.
    entries = [_ep(20 - i, "שיעור בהלכה", ["Halakha"]) for i in range(5)]
    entries += [_ep(10 - i, "Cours de Halakha", ["Halakha"]) for i in range(3)]
    _build(entries)

    bucket = _read(workdir, "theme-halakha.json")
    langs = [row[7] for row in bucket["episodes"]]
    assert langs.count("he") == 2
    assert langs.count("fr") == 2, "the French classes must survive the cap"
    # Still newest-first overall.
    dates = [row[3] for row in bucket["episodes"]]
    assert dates == sorted(dates, reverse=True)

    # Counters ignore the cap and always add up.
    assert bucket["total"] == 8
    assert bucket["total_he"] == 5
    assert bucket["total_fr"] == 3

    manifest = _read(workdir, "manifest.json")
    theme = next(t for t in manifest["themes"] if t["slug"] == "halakha")
    assert (theme["total"], theme["total_fr"], theme["total_he"]) == (8, 3, 5)
    assert manifest["langs"] == ["fr", "he"]


def test_row_carries_the_language_last(workdir):
    _build([_ep(1, "Cours de Torah", ["Moussar"]), _ep(2, "שיעור מוסר", ["Moussar"])])
    rows = _read(workdir, "theme-moussar.json")["episodes"]
    assert len(rows) == 2
    for row in rows:
        assert len(row) == 8, "lang is appended at index 7 (older clients ignore it)"
        assert row[7] in ("fr", "he")
    by_title = {row[2]: row[7] for row in rows}
    assert by_title["Cours de Torah"] == "fr"
    assert by_title["שיעור מוסר"] == "he"


def test_every_manifest_total_has_a_language_breakdown(workdir):
    _build([_ep(1, "Cours de Torah", ["Daf Hayomi"]), _ep(2, "שיעור בדף היומי", ["Daf Hayomi"])])
    manifest = _read(workdir, "manifest.json")
    groups = (
        [manifest["daf"]]
        + manifest["themes"] + manifest["parachiot"]
        + manifest["durations"] + manifest["hiloulot"]
    )
    for entry in groups:
        assert entry["total"] == entry["total_fr"] + entry["total_he"], entry
    assert manifest["daf"]["total_fr"] == 1
    assert manifest["daf"]["total_he"] == 1


def test_rewrite_is_a_no_op_when_only_the_timestamp_would_change(workdir):
    entries = [_ep(1, "Cours de Torah", ["Moussar"])]
    _build(entries)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in (workdir / "mobile").glob("*.json")}
    _build(entries)
    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
             for p in (workdir / "mobile").glob("*.json")}
    assert after == before, "an unchanged bucket must not churn git every hour"
