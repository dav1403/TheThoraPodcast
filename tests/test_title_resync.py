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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
tr = importlib.import_module("title_resync")
gen = importlib.import_module("generate_channel_pages")

SLUG = "Nahal-Haim"


def _entry(vid, title, published, **extra):
    e = {"video_id": vid, "title": title, "description": "", "published": published,
         "audio_url": f"https://cdn.example.com/{vid}.mp3", "thumbnail": f"https://img.example.com/{vid}.jpg",
         "duration_secs": 1800, "file_size": 1}
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
    "ZpU-OWjKa2A": {"id": "ZpU-OWjKa2A", "title": NEW_TITLE, "description": "Cours du Rav"},
    "D8FVHVi1Qgc": {"id": "D8FVHVi1Qgc", "title": "23 août 2026", "description": ""},  # still a date
    "realTitle01": {"id": "realTitle01", "title": "Something else", "description": "x"},
}


def test_rename_updates_title_description_and_records_the_old_page():
    entries = _entries()
    before = copy.deepcopy(entries)
    changes = tr.resync_entries(entries, SLUG, YT)

    assert [c["video_id"] for c in changes] == ["ZpU-OWjKa2A"]
    renamed = entries[0]
    assert renamed["title"] == NEW_TITLE
    assert renamed["description"] == "Cours du Rav"
    assert renamed["page_aliases"] == ["24-aout-2026-2026-08-24.html"]
    assert changes[0]["old_page"] == "24-aout-2026-2026-08-24.html"
    # Nothing else of the entry moves (published drives the page date + feed order).
    for key in ("published", "audio_url", "thumbnail", "video_id", "duration_secs"):
        assert renamed[key] == before[0][key]
    # Still a date on YouTube / absent (private, deleted) / not a date title: untouched.
    assert entries[1:] == before[1:]


def test_empty_youtube_description_keeps_the_stored_one():
    entries = [_entry("v1", "24 août 2026", "2026-08-24T18:00:00+00:00", description="keep me")]
    tr.resync_entries(entries, SLUG, {"v1": {"id": "v1", "title": "Vrai titre", "description": ""}})
    assert entries[0]["title"] == "Vrai titre"
    assert entries[0]["description"] == "keep me"


def test_second_pass_is_a_no_op_and_a_second_rename_adds_an_alias():
    entries = _entries()
    tr.resync_entries(entries, SLUG, YT)
    snapshot = copy.deepcopy(entries)
    assert tr.resync_entries(entries, SLUG, YT) == []  # no longer a date title
    assert entries == snapshot

    # A date title again (rav reverted) then a new subject: both old names kept.
    e = entries[0]
    tr.apply_rename(e, "25 août 2026", "", SLUG)
    tr.resync_entries(entries, SLUG, {"ZpU-OWjKa2A": {"id": "ZpU-OWjKa2A", "title": "Sujet final"}})
    assert e["title"] == "Sujet final"
    assert e["page_aliases"] == [
        "24-aout-2026-2026-08-24.html",
        gen.ep_filename(dict(e, title=NEW_TITLE), SLUG),
        "25-aout-2026-2026-08-24.html",
    ]
    assert gen.ep_filename(e, SLUG) not in e["page_aliases"]


def test_run_asks_youtube_only_for_date_titles_and_saves_changed_channels(tmp_path):
    feeds = tmp_path / "feeds"
    feeds.mkdir()
    (feeds / f"{SLUG}.entries.json").write_text(json.dumps(_entries(), ensure_ascii=False), encoding="utf-8")
    quiet = [_entry("q1", "Un vrai titre", "2026-01-01T00:00:00+00:00")]
    (feeds / "quiet.entries.json").write_text(json.dumps(quiet), encoding="utf-8")
    rss_only = [_entry("r1", "24 août 2026", "2026-08-24T00:00:00+00:00")]
    (feeds / "rss-chan.entries.json").write_text(json.dumps(rss_only), encoding="utf-8")
    off = [_entry("o1", "24 août 2026", "2026-08-24T00:00:00+00:00")]
    (feeds / "off.entries.json").write_text(json.dumps(off), encoding="utf-8")
    channels = [
        {"slug": SLUG, "youtube_channel_id": "UC1", "enabled": True},
        {"slug": "quiet", "youtube_channel_id": "UC2", "enabled": True},
        {"slug": "rss-chan", "source": "rss", "enabled": True},
        {"slug": "off", "youtube_channel_id": "UC3", "enabled": False},
        {"slug": "no-feed-yet", "youtube_channel_id": "UC4", "enabled": True},
    ]
    asked, rebuilt = [], []

    def fake_fetch(ids):
        asked.append(list(ids))
        return [YT[i] for i in ids if i in YT]

    summary = tr.run(channels, feeds, fake_fetch, lambda ch, entries: rebuilt.append((ch["slug"], entries)))

    assert asked == [["ZpU-OWjKa2A", "D8FVHVi1Qgc", "gonePrivate"]]
    assert summary["checked"] == 3 and summary["renamed"] == 1
    assert summary["by_channel"] == {SLUG: 1}
    saved = json.loads((feeds / f"{SLUG}.entries.json").read_text(encoding="utf-8"))
    assert saved[0]["title"] == NEW_TITLE and saved[0]["page_aliases"] == ["24-aout-2026-2026-08-24.html"]
    assert len(saved) == 4  # never loses an episode
    # RSS rebuilt once, for the changed channel only, with the saved entries.
    assert [s for s, _ in rebuilt] == [SLUG]
    assert rebuilt[0][1][0]["title"] == NEW_TITLE
    # Untouched channels are not rewritten.
    assert json.loads((feeds / "off.entries.json").read_text(encoding="utf-8")) == off
    assert json.loads((feeds / "rss-chan.entries.json").read_text(encoding="utf-8")) == rss_only


def test_run_without_date_titles_does_not_call_youtube(tmp_path):
    feeds = tmp_path / "feeds"
    feeds.mkdir()
    (feeds / "a.entries.json").write_text(json.dumps([_entry("q", "Titre", "2026-01-01T00:00:00+00:00")]),
                                          encoding="utf-8")

    def boom(ids):
        raise AssertionError("YouTube must not be called")

    s = tr.run([{"slug": "a", "enabled": True}], feeds, boom, lambda *a: None)
    assert s["checked"] == 0 and s["renamed"] == 0


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
