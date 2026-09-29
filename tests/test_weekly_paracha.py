"""Unit tests for the weekly "paracha de la semaine" (scripts/weekly_paracha.py
+ scripts/weekly_paracha_pages.py).

No network: Hebcal answers are the recorded /shabbat payloads of
tests/fixtures/hebcal_shabbat (same as test_social_post.py), transcripts and
the Anthropic client are fakes. Window: every day from 2026-10-01 to
2026-10-30 (Chol Hamoed Souccot -> Chemini Atseret / Vezot Habracha ->
Bereshit -> Noach -> Lech Lecha -> Vayera), plus the Souccot Shabbat itself.
"""
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
pytest.importorskip("requests")

import social_post as sp  # noqa: E402
import weekly_paracha as wp  # noqa: E402
import weekly_paracha_pages as pages  # noqa: E402

HEBCAL_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hebcal_shabbat"
FIXTURES = {}
for _f in HEBCAL_FIXTURES.glob("*.json"):
    FIXTURES[sp.upcoming_shabbat(dt.date.fromisoformat(_f.stem))] = json.loads(_f.read_text(encoding="utf-8"))


def fixture_fetch(day):
    return FIXTURES[day]


def _expected(day: dt.date):
    if day <= dt.date(2026, 10, 3):
        return ["vezot-habracha"], "holiday:shmini-atzeret"
    if day <= dt.date(2026, 10, 10):
        return ["bereshit"], "hebcal"
    if day <= dt.date(2026, 10, 17):
        return ["noach"], "hebcal"
    if day <= dt.date(2026, 10, 24):
        return ["lech-lecha"], "hebcal"
    return ["vayera"], "hebcal"


OCTOBER = [dt.date(2026, 10, 1) + dt.timedelta(days=i) for i in range(30)]


@pytest.mark.parametrize("day", OCTOBER, ids=lambda d: d.isoformat())
def test_every_day_of_october_resolves(day):
    week = wp.resolve_week(day, fixture_fetch)
    slugs, source = _expected(day)
    assert week["reading"]["slugs"] == slugs
    assert week["reading"]["source"] == source
    assert week["shabbat"] == sp.upcoming_shabbat(day)
    label_fr, label_he = wp.reading_labels(week["reading"])
    assert label_fr and label_he


def test_chemini_atseret_week_labels_and_sukkot_fillers():
    week = wp.resolve_week(dt.date(2026, 10, 1), fixture_fetch)   # Thursday, Chol Hamoed
    assert wp.reading_labels(week["reading"])[0].startswith("Chemini Atseret")
    assert week["festivals"] == ["sukkot"]                           # Hoshana Rabba on Friday
    # On Shabbat itself, Hoshana Rabba is past: no filler festival.
    assert wp.resolve_week(dt.date(2026, 10, 3), fixture_fetch)["festivals"] == []


def test_sukkot_shabbat_is_a_festival_week():
    week = wp.resolve_week(dt.date(2026, 9, 24), fixture_fetch)   # Thursday before Sukkot I
    assert week["reading"]["kind"] == "holiday" and week["reading"]["slugs"] == ["sukkot"]
    assert wp.reading_labels(week["reading"])[0] == "Souccot"
    assert week["festivals"] == []   # Sukkot is the reading itself, not a filler


def test_bereshit_week_is_a_plain_parasha():
    week = wp.resolve_week(dt.date(2026, 10, 8), fixture_fetch)
    assert wp.reading_labels(week["reading"]) == ("Paracha Bereshit", sp.PARASHIOT["bereshit"]["he"])
    assert week["festivals"] == []


def test_week_slug_and_collision(tmp_path):
    r = {"slugs": ["nitzavim", "vayelech"]}
    assert wp.week_slug(r, dt.date(2026, 9, 5), tmp_path) == "nitzavim-vayelech-2026"
    (tmp_path / "nitzavim-vayelech-2026.json").write_text(json.dumps({"shabbat": "2026-01-03"}))
    assert wp.week_slug(r, dt.date(2026, 9, 5), tmp_path) == "nitzavim-vayelech-2026-09"
    (tmp_path / "vayechi-2026.json").write_text(json.dumps({"shabbat": "2026-01-03"}))
    assert wp.week_slug({"slugs": ["vayechi"]}, dt.date(2026, 1, 3), tmp_path) == "vayechi-2026"


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
CHANNELS = [{"slug": f"rav-{c}", "podcast_author": f"Rav {c.upper()}", "podcast_language": "fr"}
            for c in "abcdefghij"]
SPEAKERS = [{"slug": "rav-invite", "name": "Rav Invité", "from_channels": ["rav-a"],
             "title_patterns": ["rav invité"]}]


def _ep(vid, title, published="2025-10-10T00:00:00+00:00", dur=1800, **kw):
    return {"video_id": vid, "title": title, "published": published, "duration_secs": dur,
            "audio_url": f"https://r2/{vid}.mp3", "thumbnail": f"https://i/{vid}.jpg",
            "description": kw.pop("description", "Un cours sur la paracha."), "tags": kw.pop("tags", [])}


def _entries():
    data = {ch["slug"]: [] for ch in CHANNELS}
    for i, ch in enumerate(CHANNELS):
        for j in range(3):
            data[ch["slug"]].append(_ep(f"{ch['slug']}-{j}", f"Paracha Bérechit n°{j} — {ch['podcast_author']}",
                                        published=f"202{5 - j}-10-1{i % 9}T00:00:00+00:00"))
    data["rav-a"] += [
        _ep("hitat", "HITAT DU JOUR - Bereshit"),                 # excluded: daily hitat
        _ep("clip", "Bereshit en 1 minute", dur=60),               # excluded: too short
        _ep("guest", "Bereshit avec le Rav Invité", published="2026-10-01T00:00:00+00:00"),
        _ep("offtopic", "TETSAVE (20) : les secrets de Béréchit", published="2026-10-02T00:00:00+00:00"),
    ]
    data["rav-c"].append(_ep("st1", "Simhat Torah : la joie de la Torah"))
    data["rav-d"].append(_ep("st2", "Vezot Habracha - la bénédiction de Moché"))
    data["rav-e"].append(_ep("su1", "Souccot : la joie du Hoshana Rabba"))
    return data


def _week(day=dt.date(2026, 10, 8)):
    return wp.resolve_week(day, fixture_fetch)


def test_collect_candidates_filters_and_folds_accents():
    data = _entries()
    cands = wp.collect_candidates(_week(), CHANNELS, SPEAKERS, dt.date(2026, 10, 8), data.get)
    ids = {c["ep"]["video_id"] for c in cands}
    assert "hitat" not in ids and "clip" not in ids
    assert "rav-b-0" in ids                   # "Bérechit" matched through accent folding
    guest = next(c for c in cands if c["ep"]["video_id"] == "guest")
    assert guest["rav"] == "Rav Invité" and guest["rav_slug"] == "rav-invite"
    off = next(c for c in cands if c["ep"]["video_id"] == "offtopic")
    fresh = next(c for c in cands if c["ep"]["video_id"] == "guest")
    assert off["weight"] == pytest.approx(wp.OFF_TOPIC_FACTOR) and off["score"] < fresh["score"]


def test_pick_is_diverse_and_bounded():
    data = _entries()
    cands = wp.collect_candidates(_week(), CHANNELS, SPEAKERS, dt.date(2026, 10, 8), data.get)
    wp.enrich_with_transcripts(cands, lambda vid: "")
    chosen = wp.pick(cands)
    assert wp.MIN_COURSES <= len(chosen) <= wp.MAX_COURSES
    ravs = [c["rav"] for c in chosen]
    # 11 distinct ravs available (10 channels + the guest): the first 11 picks are all different
    assert len(set(ravs[:11])) == 11
    assert max(ravs.count(r) for r in set(ravs)) <= wp.MAX_PER_RAV


def test_transcript_boosts_score():
    data = _entries()
    cands = wp.collect_candidates(_week(), CHANNELS, SPEAKERS, dt.date(2026, 10, 8), data.get)
    long_text = "Aujourd'hui nous étudions la création du monde et le sens du premier verset. " * 20
    wp.enrich_with_transcripts(cands, lambda vid: long_text if vid == "rav-j-2" else "")
    c = next(c for c in cands if c["ep"]["video_id"] == "rav-j-2")
    assert c["has_transcript"] and c["extract"]
    assert cands.index(c) < 5


def test_festival_without_class_falls_back_to_next_parasha(tmp_path):
    data = {ch["slug"]: [] for ch in CHANNELS}
    data["rav-a"] = [_ep("vz", "Vezot Habracha : la bénédiction de Moché")]
    week = wp.build_week(dt.date(2026, 9, 24), fetch=fixture_fetch, entries_loader=data.get,
                         fetch_tx=lambda v: "", api_key=None, data_dir=tmp_path,
                         catalogue=(CHANNELS, SPEAKERS))
    assert week["reading"]["slugs"] == ["vezot-habracha"]
    assert [c["video_id"] for c in week["courses"]] == ["vz"]


def test_no_class_at_all_raises(tmp_path):
    with pytest.raises(sp.ReadingError):
        wp.build_week(dt.date(2026, 10, 8), fetch=fixture_fetch, entries_loader=lambda s: [],
                      fetch_tx=lambda v: "", api_key=None, data_dir=tmp_path,
                      catalogue=(CHANNELS, SPEAKERS))


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
class _Block:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text
        self.thinking = "..." if type_ == "thinking" else None


class _Msg:
    def __init__(self, blocks):
        self.content = blocks


SUMMARY = ("Cette semaine, dix rabbins reviennent sur le récit de la création.\n"
           "Le Rav A insiste sur le premier verset, le Rav B sur le Chabbat.\n"
           "Plusieurs cours sont en hébreu, avec transcription.")


def test_first_text_skips_a_thinking_block():
    assert wp.first_text(_Msg([_Block("thinking"), _Block("text", SUMMARY)])) == SUMMARY
    assert wp.first_text(_Msg([_Block("thinking")])) == ""


def test_valid_summary_rules():
    assert wp.valid_summary(SUMMARY)
    assert not wp.valid_summary("Trop court.")
    assert not wp.valid_summary(SUMMARY + "\nhttps://spam")
    assert not wp.valid_summary("\n".join(["ligne assez longue pour compter"] * 8))


class _FakeAnthropic:
    calls = []
    raise_exc = None

    def __init__(self, api_key):
        self.messages = self

    def create(self, **kw):
        _FakeAnthropic.calls.append(kw)
        if _FakeAnthropic.raise_exc:
            raise _FakeAnthropic.raise_exc
        return _Msg([_Block("thinking"), _Block("text", "- " + SUMMARY.replace("\n", "\n- "))])


class _Ns:
    Anthropic = _FakeAnthropic


def _chosen():
    data = _entries()
    cands = wp.collect_candidates(_week(), CHANNELS, SPEAKERS, dt.date(2026, 10, 8), data.get)
    wp.enrich_with_transcripts(cands, lambda v: "")
    return wp.pick(cands)


def test_generate_summary_uses_haiku_and_grounded_prompt(monkeypatch):
    monkeypatch.setattr(wp, "_anthropic", _Ns)
    _FakeAnthropic.calls, _FakeAnthropic.raise_exc = [], None
    chosen = _chosen()
    text, source = wp.generate_summary("Paracha Bereshit", chosen, "key")
    assert source == "claude-haiku-4-5-20251001"
    assert text == SUMMARY                       # bullets stripped, thinking block skipped
    prompt = _FakeAnthropic.calls[0]["messages"][0]["content"]
    assert _FakeAnthropic.calls[0]["model"] == "claude-haiku-4-5-20251001"
    assert "UNIQUEMENT" in prompt and "n'invente" in prompt
    for c in chosen:
        assert c["ep"]["title"] in prompt


def test_generate_summary_falls_back_on_credit_error(monkeypatch, tmp_path):
    monkeypatch.setattr(wp, "_anthropic", _Ns)
    monkeypatch.setattr(sp, "CREDIT_ERROR_FLAG", tmp_path / "flag")

    class CreditError(Exception):
        status_code = 400
    _FakeAnthropic.calls, _FakeAnthropic.raise_exc = [], CreditError("Your credit balance is too low")
    text, source = wp.generate_summary("Paracha Bereshit", _chosen(), "key")
    assert source == "fallback" and "Paracha Bereshit" in text
    assert (tmp_path / "flag").exists()
    _FakeAnthropic.raise_exc = None


def test_fallback_summary_only_uses_data():
    chosen = _chosen()
    text = wp.fallback_summary("Paracha Bereshit", chosen)
    assert wp.valid_summary(text)
    assert chosen[0]["ep"]["title"].strip() in text


# ---------------------------------------------------------------------------
# End to end: generate, render, sitemap, feed, idempotence, Friday link
# ---------------------------------------------------------------------------
@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wp, "DATA_DIR", Path("paracha"))
    return tmp_path


def _build(day, api_key=None):
    data = _entries()
    return wp.build_week(day, fetch=fixture_fetch, entries_loader=data.get, fetch_tx=lambda v: "",
                         api_key=api_key, data_dir=Path("paracha"), catalogue=(CHANNELS, SPEAKERS))


def _inline_scripts(html):
    return re.findall(r'<script(?![^>]*\bsrc\b)(?![^>]*application/ld\+json)[^>]*>([\s\S]*?)</script>', html)


def test_generate_and_render(site, monkeypatch):
    monkeypatch.setattr(wp, "_anthropic", _Ns)
    _FakeAnthropic.calls, _FakeAnthropic.raise_exc = [], None
    week = _build(dt.date(2026, 10, 1), api_key="k")
    written = wp.write_week(week, Path("paracha"))
    assert "paracha/vezot-habracha-2026.json" in written
    week2 = _build(dt.date(2026, 10, 8), api_key="k")
    wp.write_week(week2, Path("paracha"))
    assert len(_FakeAnthropic.calls) == 2

    rolling = (site / pages.ROLLING_PAGE).read_text(encoding="utf-8")
    archive = (site / "paracha" / "vezot-habracha-2026.html").read_text(encoding="utf-8")
    assert "Paracha Bereshit" in rolling                      # latest week on the rolling page
    assert rolling.count('class="wk-course"') >= wp.MIN_COURSES
    assert 'href="/paracha/vezot-habracha-2026.html"' in rolling  # archive list
    assert '<link rel="canonical" href="https://thetorahpodcast.net/paracha-de-la-semaine.html">' in rolling
    assert '<link rel="canonical" href="https://thetorahpodcast.net/paracha/vezot-habracha-2026.html">' in archive
    for html in (rolling, archive):
        blocks = re.findall(r'<script type="application/ld\+json">([\s\S]*?)</script>', html)
        assert len(blocks) == 1
        ld = json.loads(blocks[0])
        assert ld["@type"] == "CollectionPage"
        assert ld["mainEntity"]["numberOfItems"] == len(ld["mainEntity"]["itemListElement"]) >= 1
        assert ld["breadcrumb"]["itemListElement"][0]["item"] == "https://thetorahpodcast.net/"
        assert SUMMARY.splitlines()[0] in html
        assert not re.search(r"\{\{\s*\w", html)            # no unrendered mustache
    node = shutil.which("node")
    if node:
        for js in _inline_scripts(rolling):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
                fh.write(js)
            proc = subprocess.run([node, "--check", fh.name], capture_output=True, text=True)
            Path(fh.name).unlink()
            assert proc.returncode == 0, proc.stderr

    feed = ET.fromstring((site / pages.FEED_FILE).read_bytes())
    items = feed.findall("./channel/item")
    assert [i.findtext("link") for i in items] == [
        "https://thetorahpodcast.net/paracha/bereshit-2026.html",
        "https://thetorahpodcast.net/paracha/vezot-habracha-2026.html",
    ]

    sm = pages.sitemap_entries("2026-10-08")
    assert sm.count("<url>") == 3 and "paracha-de-la-semaine.html" in sm
    assert "<lastmod>2026-10-08</lastmod>" in sm

    # Idempotence: same selection -> the summary is reused, no new API call.
    again = _build(dt.date(2026, 10, 8), api_key="k")
    assert again["summary"] == week2["summary"] and len(_FakeAnthropic.calls) == 2

    # Friday post links to the weekly page only for the matching reading.
    reading = sp.resolve_weekly_reading(dt.date(2026, 10, 9), fixture_fetch)
    assert pages.weekly_page_link(reading, dt.date(2026, 10, 9)) == \
        "https://thetorahpodcast.net/paracha-de-la-semaine.html"
    assert pages.weekly_page_link(reading, dt.date(2026, 10, 16)) is None
    assert wp.already_done(dt.date(2026, 10, 8)) == "bereshit-2026"
    assert wp.already_done(dt.date(2026, 10, 15)) is None


def test_sitemap_empty_without_week(site):
    assert pages.sitemap_entries("2026-10-08") == ""


def test_friday_post_uses_weekly_page(site, monkeypatch, capsys):
    week = _build(dt.date(2026, 10, 8))
    wp.write_week(week, Path("paracha"))
    monkeypatch.setattr(sp, "RUN_RESULTS", [])
    for name in ("FB_PAGE_ID", "FB_TOKEN", "IG_USER_ID", "MAKE_WEBHOOK_URL", "ANTHROPIC_KEY"):
        monkeypatch.setattr(sp, name, "")
    data = _entries()
    monkeypatch.setattr(sp, "load_entries", lambda slug: data.get(slug, []))
    sp.post_paracha(CHANNELS, {}, dry_run=True, ref_date=dt.date(2026, 10, 9), fetch=fixture_fetch)
    out = capsys.readouterr().out
    assert "Link: https://thetorahpodcast.net/paracha-de-la-semaine.html" in out


# ---------------------------------------------------------------------------
# Telegram (dormant without secrets)
# ---------------------------------------------------------------------------
def test_telegram_not_configured_is_a_green_noop(site, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHANNEL_ID", raising=False)
    assert wp.main(["telegram"]) == 0


def test_send_telegram(site):
    week = _build(dt.date(2026, 10, 8))
    sent = {}

    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "result": {"message_id": 42}}

    def post(url, json=None, timeout=None):  # noqa: A002
        sent.update(url=url, body=json)
        return R()
    ok, detail = wp.send_telegram(week, "TOKEN", "@ttp", post=post)
    assert ok and "42" in detail
    assert sent["url"].endswith("/botTOKEN/sendMessage")
    assert sent["body"]["parse_mode"] == "HTML" and len(sent["body"]["text"]) <= 4096
    assert "paracha/bereshit-2026.html" in sent["body"]["text"]


def test_normalize_summary_splits_a_single_paragraph():
    para = ("À l'approche de Chemini Atseret, dix rabbins commentent Vézot Habracha. "
            "Le Rav A lit l'Or Ha'haïm, le Rav B parle de la joie. "
            "Plusieurs cours portent sur Souccot et Hoshana Rabba.")
    text = wp.normalize_summary(para)
    assert text.count("\n") == 2 and wp.valid_summary(text)
    long = " ".join(f"Phrase numéro {i} assez longue pour remplir la limite du résumé." for i in range(40))
    assert len(wp.normalize_summary(long)) <= wp.SUMMARY_MAX_CHARS
