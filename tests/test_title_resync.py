"""The title resync pass (scripts/title_resync.py) and the redirect stubs it
leads the generator to write (generate_channel_pages.write_episode_alias_stubs).

The YouTube API is replaced by a dict / a fake fetch function: stdlib only,
like the rest of the CI suite (process_podcasts.py is never imported here).
"""
import copy
import importlib
import json
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
tr = importlib.import_module("title_resync")
gen = importlib.import_module("generate_channel_pages")

SLUG = "Nahal-Haim"
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def _entry(vid, title, published, **extra):
    e = {"video_id": vid, "title": title, "description": "", "published": published,
         "audio_url": f"https://cdn.example.com/{vid}.mp3", "thumbnail": f"https://img.example.com/{vid}.jpg",
         "duration_secs": 1800, "file_size": 1, "tags": ["emouna"]}
    e.update(extra)
    return e


def _entries():
    return [
        _entry("ZpU-OWjKa2A", "24 août 2026", "2026-08-24T18:00:00+00:00"),
        _entry("D8FVHVi1Qgc", "23 août 2026", "2026-08-23T18:00:00+00:00"),
        _entry("gonePrivate", "17 août 2026", "2026-08-17T18:00:00+00:00"),
        _entry("realTitle01", "Likoutei Moharan 12", "2026-08-10T18:00:00+00:00"),
    ]


NEW_TITLE = 'רב יואל משי זהב שליט"א - סיפורי מעשיות א\' חלק ו\' - כולל נחל חיים'
YT = {
    "ZpU-OWjKa2A": {"title": NEW_TITLE, "description": "Cours du Rav"},
    "D8FVHVi1Qgc": {"title": "23 août 2026", "description": ""},  # still the same date
    "realTitle01": {"title": "Likoutei Moharan 12", "description": "edited on YouTube"},
}


# --- Comparison: never a false rename ----------------------------------------

@pytest.mark.parametrize("stored, youtube", [
    ("Pirké Avot  - Chapitre 1 ", "Pirké Avot - Chapitre 1"),            # double / edge spaces
    ("Torah &amp; Mitsvot", "Torah & Mitsvot"),                          # HTML entity
    ("Pirké Avot", "Pirké Avot"),                             # NFD vs NFC
    ("‏שיעור בגמרא‏", "שיעור בגמרא"),                          # RLM marks
    ("Cours du soir", "Cours du soir"),                             # NBSP
    ("Line\nbreak", "Line break"),
])
def test_normalisation_only_differences_are_not_renames(stored, youtube):
    assert tr.title_key(stored) == tr.title_key(youtube)
    entries = [_entry("v1", stored, "2026-01-01T00:00:00+00:00")]
    assert tr.plan_resync(entries, {"v1": {"title": youtube, "description": "x"}}) == []


def test_real_differences_are_renames_and_stored_as_ingested():
    entries = [_entry("v1", "Cours 1", "2026-01-01T00:00:00+00:00"),
               _entry("v2", "pirké avot", "2026-01-01T00:00:00+00:00")]
    yt = {"v1": {"title": "Cours 1 : la Emouna  ", "description": ""},
          "v2": {"title": "Pirké Avot", "description": ""}}
    plan = tr.plan_resync(entries, yt)
    assert [(e["video_id"], t) for e, t, _ in plan] == [("v1", "Cours 1 : la Emouna  "), ("v2", "Pirké Avot")]
    # Stored exactly like ingestion would (html.unescape only, spaces kept):
    # the next pass compares equal and is a no-op.
    tr.resync_entries(entries, SLUG, yt)
    assert entries[0]["title"] == "Cours 1 : la Emouna  "
    assert tr.plan_resync(entries, yt) == []
    assert tr.change_kind("pirké avot", "Pirké Avot") == "case"


def test_as_ingested_matches_process_podcasts():
    src = (ROOT / "scripts" / "process_podcasts.py").read_text(encoding="utf-8")
    # Every ingestion path stores html.unescape(snippet title) - the resync must too.
    assert src.count('html.unescape(snippet.get("title", ""))') >= 2
    assert tr.as_ingested("A &amp; B &quot;C&quot;") == 'A & B "C"'


def test_never_renamed_to_a_date_nor_when_absent_or_empty():
    entries = [_entry("v1", "Vrai sujet", "2026-01-01T00:00:00+00:00"),
               _entry("v2", "Autre sujet", "2026-01-01T00:00:00+00:00"),
               _entry("v3", "Encore", "2026-01-01T00:00:00+00:00")]
    yt = {"v1": {"title": "24 août 2026"}, "v2": {"title": "   "}}
    assert tr.plan_resync(entries, yt) == []


def test_title_locked_entry_is_never_touched():
    entries = [_entry("v1", "Titre réécrit par nous", "2026-01-01T00:00:00+00:00", title_locked=True)]
    before = copy.deepcopy(entries)
    assert tr.resync_entries(entries, SLUG, {"v1": {"title": "Titre YouTube", "description": "d"}}) == []
    assert entries == before


# --- Applying a rename ---------------------------------------------------------

def test_rename_updates_title_description_and_records_the_old_page():
    entries = _entries()
    before = copy.deepcopy(entries)
    changes = tr.resync_entries(entries, SLUG, YT)

    assert [c["video_id"] for c in changes] == ["ZpU-OWjKa2A"]
    renamed = entries[0]
    assert renamed["title"] == NEW_TITLE
    assert renamed["description"] == "Cours du Rav"
    assert "tags" not in renamed  # re-tagged from the new title by tag_episodes.py
    assert renamed["page_aliases"] == ["24-aout-2026-2026-08-24.html"]
    assert changes[0]["old_page"] == "24-aout-2026-2026-08-24.html"
    for key in ("published", "audio_url", "thumbnail", "video_id", "duration_secs"):
        assert renamed[key] == before[0][key]
    # Same title / absent (private, deleted) / description-only edit: untouched.
    assert entries[1:] == before[1:]


def test_description_is_kept_when_youtube_has_none_and_never_updated_alone():
    entries = [_entry("v1", "Ancien", "2026-08-24T18:00:00+00:00", description="notre description"),
               _entry("v2", "Même titre", "2026-08-24T18:00:00+00:00", description="notre description")]
    tr.resync_entries(entries, SLUG, {"v1": {"title": "Nouveau", "description": "  "},
                                      "v2": {"title": "Même titre", "description": "autre"}})
    assert entries[0]["title"] == "Nouveau"
    assert entries[0]["description"] == "notre description"
    assert entries[1]["description"] == "notre description" and entries[1]["tags"] == ["emouna"]


def test_rename_that_keeps_the_slug_adds_no_alias():
    entries = [_entry("v1", "pirke avot", "2026-01-01T00:00:00+00:00")]
    changes = tr.resync_entries(entries, SLUG, {"v1": {"title": "Pirké Avot"}})
    assert changes[0]["old_page"] is None
    assert "page_aliases" not in entries[0]


def test_second_pass_is_a_no_op_and_a_second_rename_adds_an_alias():
    entries = _entries()
    tr.resync_entries(entries, SLUG, YT)
    snapshot = copy.deepcopy(entries)
    assert tr.resync_entries(entries, SLUG, YT) == []
    assert entries == snapshot

    e = entries[0]
    tr.apply_rename(e, "25 août 2026", "", SLUG)
    tr.resync_entries(entries, SLUG, {"ZpU-OWjKa2A": {"title": "Sujet final"}})
    assert e["title"] == "Sujet final"
    assert e["page_aliases"] == [
        "24-aout-2026-2026-08-24.html",
        gen.ep_filename(dict(e, title=NEW_TITLE), SLUG),
        "25-aout-2026-2026-08-24.html",
    ]
    assert gen.ep_filename(e, SLUG) not in e["page_aliases"]


# --- Pools and rotating cursor ---------------------------------------------------

def test_split_pools_recent_and_date_titles_are_hot():
    entries = [_entry("new", "Cours", "2026-10-01T00:00:00+00:00"),
               _entry("old", "Cours", "2025-01-01T00:00:00+00:00"),
               _entry("olddate", "12 Janv 2025", "2025-01-12T00:00:00+00:00")]
    hot, cold = tr.split_pools(entries, NOW, 60)
    assert hot == ["new", "olddate"] and cold == ["old"]


def test_select_slice_rotates_wraps_and_ignores_insertions():
    pool = [f"id{i:02d}" for i in range(10)]
    ids, cur, wrapped = tr.select_slice(pool, None, 4)
    assert ids == ["id00", "id01", "id02", "id03"] and cur == "id03" and not wrapped
    ids, cur, wrapped = tr.select_slice(pool + ["id025"], cur, 4)  # a new id lands before the cursor
    assert ids == ["id04", "id05", "id06", "id07"] and not wrapped
    ids, cur, wrapped = tr.select_slice(pool, cur, 4)
    assert ids == ["id08", "id09", "id00", "id01"] and cur == "id01" and wrapped
    # Pool smaller than the slice: each id once.
    assert tr.select_slice(["b", "a"], None, 10)[0] == ["a", "b"]
    # Cursor past the end (its id left the pool): restart from the beginning.
    assert tr.select_slice(pool, "zzz", 2)[0] == ["id00", "id01"]


def _write_feeds(tmp_path, data):
    feeds = tmp_path / "feeds"
    feeds.mkdir()
    for slug, entries in data.items():
        (feeds / f"{slug}.entries.json").write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return feeds


def _fake_pipeline(monkeypatch, rebuilt=None):
    """process_podcasts needs feedgen/yt-dlp/boto3 + secrets: a stand-in."""
    import types
    fake = types.ModuleType("process_podcasts")
    fake.get_channel_info = lambda cid: {"title": cid, "description": "", "thumbnail": ""}
    fake.build_rss_feed = lambda ch, info, entries, path: (rebuilt if rebuilt is not None else []).append(ch["slug"])
    monkeypatch.setitem(sys.modules, "process_podcasts", fake)
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")


def _fake_fetch(yt, asked):
    def fetch(ids):
        asked.append(list(ids))
        return {i: yt[i] for i in ids if i in yt}
    return fetch


def test_run_all_mode_checks_hot_plus_a_rotating_cold_slice(tmp_path):
    cold = [_entry(f"c{i}", f"Ancien {i}", "2024-01-01T00:00:00+00:00") for i in range(5)]
    hot = [_entry("h1", "Récent", "2026-10-20T00:00:00+00:00")]
    feeds = _write_feeds(tmp_path, {"a": cold + hot})
    channels = [{"slug": "a", "youtube_channel_id": "UC1"}]
    yt = {"c1": {"title": "Ancien 1 renommé", "description": ""}, "h1": {"title": "Récent", "description": ""}}
    asked = []
    s1 = tr.run(channels, feeds, _fake_fetch(yt, asked), lambda *a: None,
                mode="all", state={}, now=NOW, slice_size=2)
    assert asked[-1] == ["h1", "c0", "c1"]
    assert s1["renamed"] == 1 and s1["state"]["cursor"] == "c1"
    assert s1["units"] == 1 and not s1["tour_completed"]
    s2 = tr.run(channels, feeds, _fake_fetch(yt, asked), lambda *a: None,
                mode="all", state=s1["state"], now=NOW, slice_size=2)
    assert asked[-1] == ["h1", "c2", "c3"] and s2["renamed"] == 0
    s3 = tr.run(channels, feeds, _fake_fetch(yt, asked), lambda *a: None,
                mode="all", state=s2["state"], now=NOW, slice_size=2)
    assert asked[-1] == ["h1", "c4", "c0"] and s3["tour_completed"]
    assert s3["state"]["tours_completed"] == 1
    saved = json.loads((feeds / "a.entries.json").read_text(encoding="utf-8"))
    assert saved[1]["title"] == "Ancien 1 renommé" and len(saved) == 6


def test_fetch_error_applies_nothing_and_keeps_the_cursor(tmp_path, monkeypatch, capsys):
    feeds = _write_feeds(tmp_path, {"a": [_entry("c1", "Ancien", "2024-01-01T00:00:00+00:00")]})
    (feeds / tr.STATE_NAME).write_text(json.dumps({"cursor": "c0"}), encoding="utf-8")
    chans = tmp_path / "channels.json"
    chans.write_text(json.dumps([{"slug": "a", "youtube_channel_id": "UC1"}]), encoding="utf-8")
    _fake_pipeline(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("quotaExceeded")
    monkeypatch.setattr(tr, "fetch_api", boom)
    before = (feeds / "a.entries.json").read_text(encoding="utf-8")
    assert tr.main(["--mode", "all", "--feeds-dir", str(feeds), "--channels", str(chans)]) == 0
    assert "::warning title=Title resync skipped::RuntimeError: quotaExceeded" in capsys.readouterr().out
    assert (feeds / "a.entries.json").read_text(encoding="utf-8") == before
    assert json.loads((feeds / tr.STATE_NAME).read_text(encoding="utf-8")) == {"cursor": "c0"}


def test_dates_mode_asks_youtube_only_for_date_titles(tmp_path):
    feeds = _write_feeds(tmp_path, {
        SLUG: _entries(),
        "quiet": [_entry("q1", "Un vrai titre", "2026-01-01T00:00:00+00:00")],
        "rss-chan": [_entry("r1", "24 août 2026", "2026-08-24T00:00:00+00:00")],
        "off": [_entry("o1", "24 août 2026", "2026-08-24T00:00:00+00:00")],
    })
    channels = [
        {"slug": SLUG, "youtube_channel_id": "UC1", "enabled": True},
        {"slug": "quiet", "youtube_channel_id": "UC2", "enabled": True},
        {"slug": "rss-chan", "source": "rss", "enabled": True},
        {"slug": "off", "youtube_channel_id": "UC3", "enabled": False},
        {"slug": "no-feed-yet", "youtube_channel_id": "UC4", "enabled": True},
    ]
    asked, rebuilt = [], []
    summary = tr.run(channels, feeds, _fake_fetch(YT, asked),
                     lambda ch, entries: rebuilt.append((ch["slug"], entries)), mode="dates")

    assert asked == [["ZpU-OWjKa2A", "D8FVHVi1Qgc", "gonePrivate"]]
    assert summary["checked"] == 3 and summary["renamed"] == 1
    assert summary["by_channel"] == {SLUG: 1}
    saved = json.loads((feeds / f"{SLUG}.entries.json").read_text(encoding="utf-8"))
    assert saved[0]["title"] == NEW_TITLE and saved[0]["page_aliases"] == ["24-aout-2026-2026-08-24.html"]
    assert len(saved) == 4
    assert [s for s, _ in rebuilt] == [SLUG]
    assert json.loads((feeds / "off.entries.json").read_text(encoding="utf-8"))[0]["title"] == "24 août 2026"
    assert json.loads((feeds / "rss-chan.entries.json").read_text(encoding="utf-8"))[0]["title"] == "24 août 2026"


def test_run_without_candidates_does_not_call_youtube(tmp_path):
    feeds = _write_feeds(tmp_path, {"a": [_entry("q", "Titre", "2026-01-01T00:00:00+00:00")]})

    def boom(ids):
        raise AssertionError("YouTube must not be called")

    s = tr.run([{"slug": "a", "enabled": True}], feeds, boom, lambda *a: None, mode="dates")
    assert s["checked"] == 0 and s["renamed"] == 0


# --- Anti-mass guard --------------------------------------------------------------

def test_guard_applies_nothing_above_the_limit(tmp_path, monkeypatch, capsys):
    entries = [_entry(f"v{i:02d}", f"Titre {i}", "2026-10-20T00:00:00+00:00") for i in range(6)]
    feeds = _write_feeds(tmp_path, {"a": entries})
    channels = [{"slug": "a", "youtube_channel_id": "UC1"}]
    yt = {f"v{i:02d}": {"title": f"Nouveau {i}", "description": ""} for i in range(6)}
    rebuilt = []
    s = tr.run(channels, feeds, _fake_fetch(yt, []), lambda *a: rebuilt.append(a),
               mode="all", state={"cursor": "x"}, now=NOW, max_renames=5)
    assert s["guard_tripped"] and s["renamed"] == 6
    assert s["state"] == {"cursor": "x"}  # cursor not advanced
    assert rebuilt == []
    saved = json.loads((feeds / "a.entries.json").read_text(encoding="utf-8"))
    assert saved == entries  # nothing written

    # At the limit: applied.
    s = tr.run(channels, feeds, _fake_fetch(dict(list(yt.items())[:5]), []), lambda *a: rebuilt.append(a),
               mode="all", state={}, now=NOW, max_renames=5)
    assert not s["guard_tripped"] and s["renamed"] == 5 and len(rebuilt) == 1


def test_guard_through_main_warns_and_writes_the_job_summary(tmp_path, monkeypatch, capsys):
    entries = [_entry(f"v{i:02d}", f"Titre {i}", "2026-10-20T00:00:00+00:00") for i in range(12)]
    feeds = _write_feeds(tmp_path, {"a": entries})
    chans = tmp_path / "channels.json"
    chans.write_text(json.dumps([{"slug": "a", "youtube_channel_id": "UC1"}]), encoding="utf-8")
    step_summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    rebuilt = []
    _fake_pipeline(monkeypatch, rebuilt)
    monkeypatch.setenv("TITLE_RESYNC_MAX_RENAMES", "3")
    monkeypatch.setattr(tr, "fetch_api", lambda ids, key: {i: {"title": "Neuf " + i, "description": ""} for i in ids})
    assert tr.main(["--mode", "all", "--feeds-dir", str(feeds), "--channels", str(chans)]) == 0
    out = capsys.readouterr().out
    assert "::warning title=Title resync blocked::" in out and "12 renames detected (> 3)" in out
    assert out.count("-> 'Neuf v") == 10  # 10 examples
    assert "Title resync blocked" in step_summary.read_text(encoding="utf-8")
    assert json.loads((feeds / "a.entries.json").read_text(encoding="utf-8")) == entries
    assert not (feeds / tr.STATE_NAME).exists() and rebuilt == []

    # Limit raised for one run: applied, RSS rebuilt, cursor state written.
    monkeypatch.setenv("TITLE_RESYNC_MAX_RENAMES", "50")
    assert tr.main(["--mode", "all", "--feeds-dir", str(feeds), "--channels", str(chans)]) == 0
    saved = json.loads((feeds / "a.entries.json").read_text(encoding="utf-8"))
    assert all(e["title"].startswith("Neuf ") for e in saved) and rebuilt == ["a"]
    state = json.loads((feeds / tr.STATE_NAME).read_text(encoding="utf-8"))
    assert state["last_run"]["renamed"] == 12


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    entries = [_entry("v1", "Titre", "2020-01-01T00:00:00+00:00")]
    feeds = _write_feeds(tmp_path, {"a": entries})
    chans = tmp_path / "channels.json"
    chans.write_text(json.dumps([{"slug": "a", "youtube_channel_id": "UC1"}]), encoding="utf-8")
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    monkeypatch.setattr(tr, "fetch_api", lambda ids, key: {"v1": {"title": "Nouveau", "description": ""}})
    report = tmp_path / "r.json"
    tr.main(["--dry-run", "--all", "--feeds-dir", str(feeds), "--channels", str(chans), "--report", str(report)])
    assert json.loads((feeds / "a.entries.json").read_text(encoding="utf-8")) == entries
    assert not (feeds / tr.STATE_NAME).exists()
    rep = json.loads(report.read_text(encoding="utf-8"))
    assert rep["renamed"] == 1 and rep["examples"][0]["new_title"] == "Nouveau"


def test_fetch_api_parses_like_ingestion_and_skips_live():
    calls = []

    def fake_get(url):
        calls.append(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query))
        ids = calls[-1]["id"][0].split(",")
        return {"items": [{"id": i, "snippet": {"title": "A &amp; B", "description": "d &quot;x&quot;",
                                                "liveBroadcastContent": "live" if i == "live1" else "none"}}
                          for i in ids if i != "gone"]}

    ids = [f"v{i}" for i in range(60)] + ["live1", "gone"]
    out = tr.fetch_api(ids, "k", get_json=fake_get)
    assert len(calls) == 2 and calls[0]["part"] == ["snippet"]
    assert out["v0"] == {"title": "A & B", "description": 'd "x"'}
    assert "live1" not in out and "gone" not in out and len(out) == 60


# --- Generator: old page name -> redirect stub ------------------------------

def _renamed_fixture(fixture_workdir):
    path = fixture_workdir / "feeds" / "rav-test-un.entries.json"
    entries = json.loads(path.read_text(encoding="utf-8"))
    ep = entries[0]
    old_name = "24-aout-2026-" + ep["published"][:10] + ".html"
    ep["page_aliases"] = [old_name]
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return ep, old_name


def test_alias_becomes_a_redirect_stub_to_the_new_page(gen, fixture_workdir):
    ep, old_name = _renamed_fixture(fixture_workdir)
    gen.main()

    new_name = gen.ep_filename(ep, "rav-test-un")
    new_page = fixture_workdir / "rav-test-un" / new_name
    stub = fixture_workdir / "rav-test-un" / old_name
    assert new_page.exists()
    assert stub.exists()
    text = stub.read_text(encoding="utf-8")
    target = f"{gen.BASE_URL}/rav-test-un/{new_name}"
    assert f'<link rel="canonical" href="{target}">' in text
    assert f'content="0; url={target}"' in text
    assert f'location.replace("{target}")' in text
    assert 'name="robots" content="noindex, follow"' in text
    # The stub is not a page of the site: out of the sitemap, the new page in it.
    sitemap = (fixture_workdir / "sitemap.xml").read_text(encoding="utf-8")
    assert old_name not in sitemap
    assert new_name in sitemap


def test_alias_never_overwrites_a_real_page(gen, fixture_workdir):
    path = fixture_workdir / "feeds" / "rav-test-un.entries.json"
    entries = json.loads(path.read_text(encoding="utf-8"))
    # Alias pointing at another episode's real page (identical date titles collide).
    other = gen.ep_filename(entries[1], "rav-test-un")
    entries[0]["page_aliases"] = [other, "../escape.html", "x/y.html", gen.ep_filename(entries[0], "rav-test-un")]
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    gen.main()

    page = (fixture_workdir / "rav-test-un" / other).read_text(encoding="utf-8")
    assert 'http-equiv="refresh"' not in page
    own = (fixture_workdir / "rav-test-un" / gen.ep_filename(entries[0], "rav-test-un")).read_text(encoding="utf-8")
    assert 'http-equiv="refresh"' not in own
    assert not (fixture_workdir / "escape.html").exists()


def test_write_episode_alias_stubs_counts(tmp_path):
    entries = [_entry("v1", "Vrai titre", "2026-08-24T18:00:00+00:00",
                      page_aliases=["24-aout-2026-2026-08-24.html"]),
               _entry("v2", "Autre", "2026-08-25T18:00:00+00:00")]
    assert gen.write_episode_alias_stubs(tmp_path, SLUG, entries) == 1
    text = (tmp_path / "24-aout-2026-2026-08-24.html").read_text(encoding="utf-8")
    assert re.search(r'href="https://thetorahpodcast\.net/Nahal-Haim/vrai-titre-2026-08-24\.html"', text)
