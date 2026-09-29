"""Espace rav (private per-rav listening stats) — core logic + page contract.

Pure tests: no network, no R2. The crypto tests need `cryptography`; the
interoperability test (Python seals, the page's own JS opens) needs node >= 20.
"""
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import stats_core as core  # noqa: E402
import stats_sources as sources  # noqa: E402

MASTER = core.load_master_key(core.new_master_key())
TODAY = date(2026, 9, 29)  # Tuesday -> last complete day Monday 28/09


def _catalogue():
    channels = [
        {"slug": "lev", "podcast_author": "Lev", "podcast_language": "fr", "enabled": True},
        {"slug": "Nahal-Haim", "podcast_author": "Nahal Haim", "podcast_language": "he", "enabled": True},
        {"slug": "old", "podcast_author": "Old", "enabled": False},
    ]
    speakers = [{"slug": "rav-elie-lemmel", "name": "Rav Elie Lemmel", "language": "fr",
                 "from_channels": ["lev"], "title_patterns": ["elie lemmel"]}]
    entries = {
        "lev": [
            {"video_id": "AAAAAAAAAAA", "title": "Cours 1 - Rav Elie Lemmel", "published": "2026-09-01T10:00:00+00:00"},
            {"video_id": "BBBBBBBBBBB", "title": "Cours 2", "published": "2026-09-10T10:00:00+00:00"},
        ],
        "Nahal-Haim": [{"video_id": "CCCCCCCCCCC", "title": "שיעור", "published": "2026-09-02T10:00:00+00:00"}],
        "rav-elie-lemmel": [
            {"video_id": "AAAAAAAAAAA", "title": "Cours 1 - Rav Elie Lemmel", "published": "2026-09-01T10:00:00+00:00"},
        ],
    }
    return core.build_catalogue(channels, speakers, entries)


# ---------------------------------------------------------------- keys


def test_token_is_stable_distinct_and_long_enough():
    t1 = core.rav_token(MASTER, "lev")
    assert t1 == core.rav_token(MASTER, "lev")
    assert t1 != core.rav_token(MASTER, "Nahal-Haim")
    assert re.fullmatch(r"[A-Za-z0-9_-]{22}", t1)
    other = core.load_master_key(core.new_master_key())
    assert core.rav_token(other, "lev") != t1


def test_blob_id_and_key_do_not_leak_the_token():
    tok = core.rav_token(MASTER, "lev")
    bid = core.blob_id(tok)
    assert re.fullmatch(r"[0-9a-f]{32}", bid)
    assert tok not in bid and bid not in core.blob_key(tok).hex()


def test_link_puts_the_token_in_the_fragment():
    tok = core.rav_token(MASTER, "lev")
    link = core.rav_link(tok)
    assert link == f"https://thetorahpodcast.net/espace-rav.html#k={tok}"


def test_master_key_validation():
    with pytest.raises(ValueError):
        core.load_master_key("")
    with pytest.raises(ValueError):
        core.load_master_key("c2hvcnQ")  # 5 bytes


# ---------------------------------------------------------------- crypto


def test_report_and_history_roundtrip():
    pytest.importorskip("cryptography")
    tok = core.rav_token(MASTER, "lev")
    blob = core.seal_report(tok, {"name": "Lev", "n": 3})
    assert b"Lev" not in blob
    assert core.open_report(tok, blob) == {"name": "Lev", "n": 3}
    with pytest.raises(Exception):
        core.open_report(core.rav_token(MASTER, "x"), blob)
    h = core.empty_history()
    core.merge_daily(h, "direct", {"2026-09-01": {"AAAAAAAAAAA": 4}})
    sealed = core.seal_history(MASTER, h)
    assert b"AAAAAAAAAAA" not in sealed
    assert core.open_history(MASTER, sealed) == h


# ---------------------------------------------------------------- history


def test_merge_daily_is_idempotent_per_day_and_keeps_old_days():
    h = core.empty_history()
    core.merge_daily(h, "direct", {"2026-08-01": {"A": 1}, "2026-08-02": {"A": 2}})
    core.merge_daily(h, "direct", {"2026-08-02": {"A": 5, "B": 0}})
    assert h["daily"]["direct"] == {"2026-08-01": {"A": 1}, "2026-08-02": {"A": 5}}
    assert h["sources"]["direct"]["first"] == "2026-08-01"
    with pytest.raises(ValueError):
        core.merge_daily(h, "nope", {})


def test_platform_rows_upsert_by_period():
    h = core.empty_history()
    row = {"slug": "lev", "platform": "Spotify", "start": "2026-09-01", "end": "2026-09-30", "plays": "10"}
    core.add_platform_rows(h, [row])
    core.add_platform_rows(h, [dict(row, plays=12)])
    assert len(h["platforms"]) == 1 and h["platforms"][0]["plays"] == 12
    assert h["platforms"][0]["platform"] == "spotify"


# ---------------------------------------------------------------- parsing


@pytest.mark.parametrize("name,vid", [
    ("thetorahpodcast/bSF6m5ZlESo_Être_Sensible_-_Rav_Elie_Lemmel.mp3", "bSF6m5ZlESo"),
    ("a-b_c-d_e-f_Titre.mp3", "a-b_c-d_e-f"),
    ("thetorahpodcast/xxxxxxxxxxx.mp3", "xxxxxxxxxxx"),
    ("_state/feeds/lev.xml", None),
    ("artwork/lev.png", None),
    ("short.mp3", None),
])
def test_video_id_from_object(name, vid):
    assert core.video_id_from_object(name) == vid


@pytest.mark.parametrize("path,slug", [
    ("/lev.html", "lev"), ("/Nahal-Haim.html", "nahal-haim"), ("/lev", "lev"),
    ("/lev/cours-2-2026-09-10.html", "lev"), ("/", None), ("/rav-elie-lemmel.html?x=1", "rav-elie-lemmel"),
])
def test_page_slug(path, slug):
    assert core.page_slug(path) == slug


def test_parse_r2_groups():
    payload = {"data": {"viewer": {"accounts": [{"r2OperationsAdaptiveGroups": [
        {"sum": {"requests": 3}, "dimensions": {"objectName": "thetorahpodcast/AAAAAAAAAAA_x.mp3"}},
        {"sum": {"requests": 2}, "dimensions": {"objectName": "AAAAAAAAAAA_y.mp3"}},
        {"sum": {"requests": 9}, "dimensions": {"objectName": "_state/home.json"}},
    ]}]}}}
    assert sources.parse_r2_groups(payload) == {"AAAAAAAAAAA": 5}
    with pytest.raises(RuntimeError):
        sources.parse_r2_groups({"errors": [{"message": "unknown field"}]})


def test_parse_ga_rows():
    rows = [{"dimensionValues": [{"value": "20260927"}, {"value": "audio_play"}, {"value": "/lev.html"}],
             "metricValues": [{"value": "4"}]},
            {"dimensionValues": [{"value": "20260927"}, {"value": "audio_play"}, {"value": "/lev"}],
             "metricValues": [{"value": "1"}]}]
    assert sources.parse_ga_page_rows(rows) == {"2026-09-27": {"audio_play|lev": 5}}
    rows = [{"dimensionValues": [{"value": "20260927"}, {"value": "audio_play"}, {"value": "Lev"}, {"value": "Cours 2"}],
             "metricValues": [{"value": "2"}]},
            {"dimensionValues": [{"value": "20260927"}, {"value": "audio_play"}, {"value": "(not set)"}, {"value": "x"}],
             "metricValues": [{"value": "7"}]}]
    assert sources.parse_ga_ep_rows(rows) == {"2026-09-27": {"audio_play|Lev|Cours 2": 2}}


def test_sources_are_inactive_without_secrets(monkeypatch):
    for k in ("CF_API_TOKEN", "GA4_SA_JSON"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(sources.SourceUnavailable):
        sources.fetch_r2_downloads([TODAY])
    with pytest.raises(sources.SourceUnavailable):
        sources.fetch_ga4(TODAY, TODAY)


def test_cf_account_from_endpoint(monkeypatch):
    monkeypatch.delenv("CF_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://abc123.r2.cloudflarestorage.com/thetorahpodcast")
    assert sources.cf_account_id() == "abc123"


# ---------------------------------------------------------------- report


def test_catalogue_skips_disabled_and_keeps_guests():
    cat = _catalogue()
    assert set(cat) == {"lev", "Nahal-Haim", "rav-elie-lemmel"}
    assert cat["rav-elie-lemmel"]["kind"] == "guest"
    assert set(cat["rav-elie-lemmel"]["episodes"]) == {"AAAAAAAAAAA"}


def test_report_direct_and_site_per_course_with_guest_credit():
    cat = _catalogue()
    h = core.empty_history()
    core.merge_daily(h, "direct", {"2026-09-27": {"AAAAAAAAAAA": 10, "BBBBBBBBBBB": 3, "CCCCCCCCCCC": 7},
                                   "2026-06-01": {"AAAAAAAAAAA": 100},
                                   "2026-09-29": {"AAAAAAAAAAA": 999}})  # today: incomplete, ignored
    core.merge_daily(h, "site_ep", {"2026-09-27": {
        "audio_play|Lev|Cours 1 - Rav Elie Lemmel": 4,      # played on the host page
        "audio_play|Rav Elie Lemmel|Cours 1 - Rav Elie Lemmel": 1,  # on the guest page
        "audio_complete|Lev|Cours 1 - Rav Elie Lemmel": 2,
        "audio_play|Lev|Titre inconnu": 5,                  # unresolved, own page
    }})
    core.merge_daily(h, "site_page", {"2026-09-27": {"audio_play|lev": 999}})  # superseded by site_ep

    lev = core.build_report(cat["lev"], cat, h, TODAY)
    assert lev["until"] == "2026-09-28"
    assert lev["totals"]["all"]["direct"] == 113
    assert lev["totals"]["d30"]["direct"] == 13
    assert lev["totals"]["all"]["site"] == 4 + 1 + 5
    assert lev["totals"]["all"]["site_unassigned"] == 5
    assert lev["totals"]["all"]["complete"] == 2
    top = lev["courses"][0]
    assert top["vid"] == "AAAAAAAAAAA" and top["site"] == 5 and top["direct"] == 110

    guest = core.build_report(cat["rav-elie-lemmel"], cat, h, TODAY)
    assert guest["totals"]["all"]["site"] == 5       # host + guest page plays of his course
    assert guest["totals"]["all"]["direct"] == 110
    assert [c["vid"] for c in guest["courses"]] == ["AAAAAAAAAAA"]

    nh = core.build_report(cat["Nahal-Haim"], cat, h, TODAY)
    assert nh["totals"]["all"]["direct"] == 7 and nh["totals"]["all"]["site"] == 0
    assert nh["lang"] == "he"


def test_report_weekly_series_and_page_fallback():
    cat = _catalogue()
    h = core.empty_history()
    core.merge_daily(h, "site_page", {"2026-09-21": {"audio_play|lev": 2, "audio_play|nahal-haim": 9},
                                      "2026-09-28": {"audio_play|lev": 3, "audio_complete|lev": 1}})
    core.merge_daily(h, "site_ep", {"2026-09-21": {}})   # empty day -> page fallback
    rep = core.build_report(cat["lev"], cat, h, TODAY, weeks=4)
    assert [w["start"] for w in rep["weekly"]] == ["2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]
    assert [w["site"] for w in rep["weekly"]] == [0, 0, 2, 3]
    assert rep["totals"]["all"]["site"] == 5 and rep["totals"]["all"]["complete"] == 1
    assert rep["courses"] == []                     # page totals carry no per-course split
    assert rep["sources"]["site"]["active"] is True
    assert rep["sources"]["direct"]["active"] is False


def test_empty_history_gives_a_valid_empty_report():
    cat = _catalogue()
    rep = core.build_report(cat["lev"], cat, core.empty_history(), TODAY)
    assert rep["totals"]["all"] == {"site": 0, "complete": 0, "direct": 0, "site_unassigned": 0}
    assert not rep["sources"]["site"]["active"] and not rep["sources"]["direct"]["active"]
    assert rep["episodes"] == 2 and len(rep["weekly"]) == 26
    json.dumps(rep)


# ---------------------------------------------------------------- page


PAGE = ROOT / "espace-rav.html"


def test_page_is_private_by_construction():
    html = PAGE.read_text(encoding="utf-8")
    assert '<meta name="robots" content="noindex, nofollow">' in html
    assert 'name="referrer" content="no-referrer"' in html
    assert "googletagmanager" not in html and "gtag(" not in html
    assert "location.hash" in html          # key read from the fragment only
    assert "'/espace-rav/d/'" in html and core.BLOB_DIR == "espace-rav/d"
    assert "ttp-espace-rav/v1/" in html and core._NS == b"ttp-espace-rav/v1/"


def test_page_not_in_sitemap_generator():
    gen = (ROOT / "scripts" / "generate_channel_pages.py").read_text(encoding="utf-8")
    assert "espace-rav" not in gen


def test_page_js_opens_a_python_sealed_report():
    """Interop: the page's own inline script decrypts what stats_core seals."""
    pytest.importorskip("cryptography")
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    html = PAGE.read_text(encoding="utf-8")
    script = re.search(r"<script>\n(.*?)</script>", html, re.S).group(1)
    tok = core.rav_token(MASTER, "Nahal-Haim")
    report = {"name": "נחל חיים", "totals": {"all": {"site": 3}}}
    blob = core.seal_report(tok, report)
    harness = (
        "globalThis.window = globalThis;"
        "globalThis.document = {readyState: 'loading', addEventListener: function () {}};"
        + script +
        f"\nconst tok = {json.dumps(tok)};"
        f"\nconst bytes = Buffer.from({json.dumps(blob.hex())}, 'hex');"
        "\nwindow.TTPEspaceRav.blobId(tok).then(id => window.TTPEspaceRav.openReport(tok, bytes)"
        ".then(r => console.log(JSON.stringify({id: id, r: r}))))"
        ".catch(e => { console.error(e); process.exit(3); });"
    )
    out = subprocess.run([node, "-e", harness], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)
    assert res["id"] == core.blob_id(tok)
    assert res["r"] == report


def test_private_commands_refuse_to_run_in_actions(monkeypatch):
    import stats_update
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("STATS_MASTER_KEY", core.new_master_key())
    for argv in (["links"], ["show", "--slug", "lev"], ["import-platforms", "--csv", "x.csv"]):
        with pytest.raises(SystemExit) as exc:
            stats_update.main(argv)
        assert "local use only" in str(exc.value)


class _FakeS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise Exception("An error occurred (NoSuchKey)")
        import io
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[Key] = Body


def test_collect_runs_without_any_source_and_keeps_history(monkeypatch, capsys):
    pytest.importorskip("cryptography")
    import stats_update
    for k in ("CF_API_TOKEN", "GA4_SA_JSON", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(k, raising=False)
    key = core.new_master_key()
    monkeypatch.setenv("STATS_MASTER_KEY", key)
    s3 = _FakeS3()
    monkeypatch.setattr(stats_update, "_r2", lambda: (s3, "bucket"))
    assert stats_update.main(["collect", "--days", "3", "--today", "2026-09-29"]) == 0
    assert s3.objects == {}                       # nothing written without a source
    assert "inactif" in capsys.readouterr().out


def test_collect_merges_an_active_source(monkeypatch):
    pytest.importorskip("cryptography")
    import stats_sources
    import stats_update
    monkeypatch.delenv("GA4_SA_JSON", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    key = core.new_master_key()
    monkeypatch.setenv("STATS_MASTER_KEY", key)
    s3 = _FakeS3()
    monkeypatch.setattr(stats_update, "_r2", lambda: (s3, "bucket"))
    monkeypatch.setattr(stats_sources, "fetch_r2_downloads",
                        lambda days: {d.isoformat(): {"AAAAAAAAAAA": 2} for d in days})
    assert stats_update.main(["collect", "--days", "2", "--today", "2026-09-29"]) == 0
    hist = core.open_history(core.load_master_key(key), s3.objects[stats_update.HISTORY_KEY])
    assert hist["daily"]["direct"] == {"2026-09-27": {"AAAAAAAAAAA": 2}, "2026-09-28": {"AAAAAAAAAAA": 2}}
