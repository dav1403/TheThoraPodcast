"""Unit tests for scripts/social_post.py (weekly reading + fail-loud posting).

No network: Hebcal answers come from tests/fixtures/hebcal_shabbat/<friday>.json
(real /shabbat payloads recorded on 2026-09-28 for geonameid 293397), and every
Meta / Make.com call goes through a fake requests.post.

Regression covered: from 2026-09-04 the Friday post logged "Could not determine
current parasha — skipping" and the run stayed green (double parasha, festival
Shabbatot, typographic apostrophes, renamed Hebcal spellings).
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
pytest.importorskip("requests")

import social_post as sp  # noqa: E402

HEBCAL_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hebcal_shabbat"


def _fixture_by_shabbat():
    out = {}
    for f in HEBCAL_FIXTURES.glob("*.json"):
        friday = dt.date.fromisoformat(f.stem)
        out[sp.upcoming_shabbat(friday)] = json.loads(f.read_text(encoding="utf-8"))
    return out


FIXTURES = _fixture_by_shabbat()


def fixture_fetch(day):
    return FIXTURES[day]


class FakeResponse:
    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else json.dumps(body or {})

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise sp.requests.HTTPError(f"HTTP {self.status_code}")


IG_SESSION_INVALIDATED = {
    "error": {
        "message": "Error validating access token: The session has been invalidated because "
                   "the user changed their password or Facebook has changed the session for "
                   "security reasons.",
        "type": "OAuthException", "code": 190, "error_subcode": 460,
    }
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Fresh result list and no Meta/Make config unless a test sets one."""
    monkeypatch.setattr(sp, "RUN_RESULTS", [])
    for name in ("FB_PAGE_ID", "FB_TOKEN", "IG_USER_ID", "MAKE_WEBHOOK_URL", "ANTHROPIC_KEY"):
        monkeypatch.setattr(sp, name, "")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


@pytest.fixture
def fake_post(monkeypatch):
    """Route requests.post by URL substring -> FakeResponse; records calls."""
    routes = {}
    calls = []

    def _post(url, data=None, json=None, timeout=None):  # noqa: A002 - mirrors requests
        calls.append({"url": url, "data": data, "json": json})
        for key, resp in routes.items():
            if key in url:
                return resp
        raise AssertionError(f"unexpected POST {url}")

    monkeypatch.setattr(sp.requests, "post", _post)
    return routes, calls


# ---------------------------------------------------------------------------
# Weekly reading resolution — every Friday from 2026-09-04 to 2026-10-30
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("friday, kind, slugs", [
    ("2026-09-04", "parasha", ["nitzavim", "vayelech"]),   # double parasha
    ("2026-09-11", "holiday", ["rosh-hashana"]),           # Rosh Hashana on Shabbat
    ("2026-09-18", "parasha", ["haazinu"]),                # Ha’azinu (curly apostrophe)
    ("2026-09-25", "holiday", ["sukkot"]),                 # Sukkot I on Shabbat
    ("2026-10-02", "parasha", ["vezot-habracha"]),         # Shmini Atzeret / Simchat Torah
    ("2026-10-09", "parasha", ["bereshit"]),
    ("2026-10-16", "parasha", ["noach"]),
    ("2026-10-23", "parasha", ["lech-lecha"]),             # hyphen inside a single name
    ("2026-10-30", "parasha", ["vayera"]),                 # Hebcal now spells "Vayera"
])
def test_every_friday_has_a_reading(friday, kind, slugs):
    reading = sp.resolve_weekly_reading(dt.date.fromisoformat(friday), fixture_fetch)
    assert reading["kind"] == kind
    assert reading["slugs"] == slugs
    assert reading["kw"], "a reading without keywords can never match an episode"
    assert reading["link"].startswith(sp.SITE_URL)


def test_fixtures_cover_the_whole_window():
    fridays = sorted(dt.date.fromisoformat(f.stem) for f in HEBCAL_FIXTURES.glob("*.json"))
    assert fridays[0] == dt.date(2026, 9, 4) and fridays[-1] == dt.date(2026, 10, 30)
    assert all((b - a).days == 7 for a, b in zip(fridays, fridays[1:]))


# All 'parashat' titles Hebcal returned for 2026 and 2027 (Israel), verbatim.
HEBCAL_PARASHA_TITLES = [
    "Achrei Mot", "Achrei Mot-Kedoshim", "Balak", "Bamidbar", "Bechukotai", "Behar",
    "Behar-Bechukotai", "Beha’alotcha", "Bereshit", "Beshalach", "Bo", "Chayei Sara",
    "Chukat", "Devarim", "Eikev", "Emor", "Ha’azinu", "Kedoshim", "Ki Tavo", "Ki Teitzei",
    "Ki Tisa", "Korach", "Lech-Lecha", "Matot-Masei", "Metzora", "Miketz", "Mishpatim",
    "Nasso", "Nitzavim-Vayeilech", "Noach", "Pekudei", "Pinchas", "Re’eh", "Shemot",
    "Shmini", "Shoftim", "Sh’lach", "Tazria", "Tazria-Metzora", "Terumah", "Tetzaveh",
    "Toldot", "Tzav", "Vaera", "Vaetchanan", "Vayakhel", "Vayakhel-Pekudei", "Vayechi",
    "Vayera", "Vayeshev", "Vayetzei", "Vayigash", "Vayikra", "Vayishlach", "Yitro",
]


@pytest.mark.parametrize("name", HEBCAL_PARASHA_TITLES)
def test_every_hebcal_parasha_title_resolves(name):
    slugs = sp.parasha_slugs_from_title(f"Parashat {name}")
    assert slugs, name
    assert len(slugs) == (2 if name in {
        "Achrei Mot-Kedoshim", "Behar-Bechukotai", "Matot-Masei", "Nitzavim-Vayeilech",
        "Tazria-Metzora", "Vayakhel-Pekudei"} else 1)
    assert all(s in sp.PARASHIOT for s in slugs)


def test_unknown_parasha_name_raises():
    data = {"items": [{"category": "parashat", "title": "Parashat Nonexistent"}]}
    with pytest.raises(sp.ReadingError):
        sp.reading_from_hebcal(data, dt.date(2026, 9, 5))


def test_unmapped_festival_looks_ahead_to_next_parasha():
    empty = {"items": [{"category": "holiday", "title": "Some Unknown Feast", "date": "2026-09-12"}]}
    fetch = lambda d: empty if d == dt.date(2026, 9, 12) else fixture_fetch(d)  # noqa: E731
    reading = sp.resolve_weekly_reading(dt.date(2026, 9, 11), fetch)
    assert reading["slugs"] == ["haazinu"]
    assert reading["source"] == "next-parasha:2026-09-19"


def test_no_reading_at_all_raises():
    with pytest.raises(sp.ReadingError):
        sp.resolve_weekly_reading(dt.date(2026, 9, 11), lambda d: {"items": []})


# ---------------------------------------------------------------------------
# post_paracha — never a silent skip
# ---------------------------------------------------------------------------
CHANNELS = [
    {"slug": "rav-a", "podcast_author": "Rav A"},
    {"slug": "rav-b", "podcast_author": "Rav B"},
]
ENTRIES = {
    "rav-a": [
        {"title": "Roch Hachana : le jugement", "published": "2026-09-01", "tags": []},
        {"title": "Paracha Haazinu - le chant", "published": "2025-09-20", "tags": []},
        {"title": "Nitsavim : nous sommes tous là", "published": "2025-09-10", "tags": []},
    ],
    "rav-b": [
        {"title": "Cours du Rav", "published": "2026-09-02", "tags": ["Roch Hachana & Yom Kippour"]},
        {"title": "Vayelech: Moshé s'en va", "published": "2025-09-12", "tags": []},
    ],
}


@pytest.fixture
def entries(monkeypatch):
    data = {k: list(v) for k, v in ENTRIES.items()}
    monkeypatch.setattr(sp, "load_entries", lambda slug: data.get(slug, []))
    return data


def _statuses(post=None):
    return {(r["post"], r["channel"]): r["status"] for r in sp.RUN_RESULTS
            if post is None or r["post"] == post}


def _captured_messages(capsys):
    return capsys.readouterr().out


def test_paracha_double_parasha_dry_run(entries, capsys):
    sp.post_paracha(CHANNELS, {}, dry_run=True, ref_date=dt.date(2026, 9, 4), fetch=fixture_fetch)
    out = _captured_messages(capsys)
    assert _statuses() == {("paracha", "facebook"): sp.STATUS_DRY_RUN,
                           ("paracha", "instagram"): sp.STATUS_DRY_RUN}
    assert "Nitsavim-Vayelech" in out
    assert "Rav A — 1 cours" in out and "Rav B — 1 cours" in out
    assert sp.run_verdict(sp.RUN_RESULTS, dry_run=True)[0] == 0


def test_paracha_rosh_hashana_posts_the_festival(entries, capsys):
    sp.post_paracha(CHANNELS, {}, dry_run=True, ref_date=dt.date(2026, 9, 11), fetch=fixture_fetch)
    out = _captured_messages(capsys)
    assert "Roch Hachana" in out
    # title keyword wins: only rav-a (rav-b is merely theme-tagged)
    assert "Rav A — 1 cours" in out and "Rav B —" not in out
    assert "themes.html#Roch%20Hachana%20%26%20Yom%20Kippour" in out
    assert "Could not determine" not in out


def test_festival_theme_tag_is_a_fallback_only(entries, capsys):
    entries["rav-a"] = [e for e in entries["rav-a"] if "Roch" not in e["title"]]
    sp.post_paracha(CHANNELS, {}, dry_run=True, ref_date=dt.date(2026, 9, 11), fetch=fixture_fetch)
    out = _captured_messages(capsys)
    assert "Rav B — 1 cours" in out and "Rav A —" not in out


def test_paracha_festival_without_episode_falls_back_to_next_parasha(entries, capsys):
    # Sukkot (26/09): no episode matches -> next parasha = Vezot Habracha (03/10)
    entries["rav-a"].append({"title": "Simhat Torah : la joie", "published": "2025-10-14", "tags": []})
    sp.post_paracha(CHANNELS, {}, dry_run=True, ref_date=dt.date(2026, 9, 25), fetch=fixture_fetch)
    out = _captured_messages(capsys)
    assert "falling back to the next parasha" in out
    assert "parasha.html#vezot-habracha" in out
    assert _statuses()[("paracha", "facebook")] == sp.STATUS_DRY_RUN


@pytest.mark.parametrize("friday", sorted(f.stem for f in HEBCAL_FIXTURES.glob("*.json")))
def test_paracha_never_silently_skips(friday, monkeypatch, capsys):
    """Whatever the week, the run either posts or records a FAILURE."""
    monkeypatch.setattr(sp, "load_entries", lambda slug: [])  # worst case: no episode at all
    sp.post_paracha(CHANNELS, {}, dry_run=True, ref_date=dt.date.fromisoformat(friday), fetch=fixture_fetch)
    code, reasons = sp.run_verdict(sp.RUN_RESULTS, dry_run=True)
    assert code == 1 and reasons
    assert ("paracha", "episodes") in _statuses()


def test_paracha_hebcal_down_fails_loud(entries):
    def boom(day):
        raise sp.requests.ConnectionError("hebcal down")
    sp.post_paracha(CHANNELS, {}, dry_run=True, ref_date=dt.date(2026, 9, 4), fetch=boom)
    assert _statuses() == {("paracha", "reading"): sp.STATUS_FAILED}
    assert sp.run_verdict(sp.RUN_RESULTS)[0] == 1


def test_fetch_hebcal_passes_the_date(monkeypatch):
    seen = {}

    def _get(url, params=None, timeout=None):
        seen.update(params)
        return FakeResponse(200, {"items": []})

    monkeypatch.setattr(sp.requests, "get", _get)
    sp.fetch_hebcal_shabbat(dt.date(2026, 9, 12))
    assert (seen["gy"], seen["gm"], seen["gd"]) == (2026, 9, 12)
    assert seen["geonameid"] == sp.HEBCAL_GEONAME_ID


# ---------------------------------------------------------------------------
# Facebook: Graph API first, Make.com as unconfirmed fallback
# ---------------------------------------------------------------------------
def test_facebook_graph_ok_is_confirmed_and_skips_make(monkeypatch, fake_post):
    routes, calls = fake_post
    monkeypatch.setattr(sp, "FB_PAGE_ID", "123")
    monkeypatch.setattr(sp, "FB_TOKEN", "tok")
    monkeypatch.setattr(sp, "MAKE_WEBHOOK_URL", "https://hook.make.com/x")
    routes["/123/feed"] = FakeResponse(200, {"id": "123_456"})
    assert sp.post_facebook("hello", link="https://x", post="rabbi") is True
    assert [c["url"] for c in calls] == [f"{sp.GRAPH_URL}/123/feed"]
    assert calls[0]["data"]["link"] == "https://x"
    assert sp.RUN_RESULTS[-1]["status"] == sp.STATUS_OK
    assert "123_456" in sp.RUN_RESULTS[-1]["detail"]


def test_facebook_graph_200_without_id_is_a_failure(monkeypatch, fake_post):
    routes, _ = fake_post
    monkeypatch.setattr(sp, "FB_PAGE_ID", "123")
    monkeypatch.setattr(sp, "FB_TOKEN", "tok")
    routes["/123/feed"] = FakeResponse(200, {"success": True})
    assert sp.post_facebook("hello") is False
    assert sp.RUN_RESULTS[-1]["status"] == sp.STATUS_FAILED


def test_facebook_graph_error_with_make_fallback_still_fails(monkeypatch, fake_post):
    routes, calls = fake_post
    monkeypatch.setattr(sp, "FB_PAGE_ID", "123")
    monkeypatch.setattr(sp, "FB_TOKEN", "tok")
    monkeypatch.setattr(sp, "MAKE_WEBHOOK_URL", "https://hook.make.com/x")
    routes["/123/feed"] = FakeResponse(400, IG_SESSION_INVALIDATED)
    routes["hook.make.com"] = FakeResponse(200, None, text="Accepted")
    sp.post_facebook("hello")
    assert len(calls) == 2 and "hook.make.com" in calls[1]["url"]
    r = sp.RUN_RESULTS[-1]
    assert r["status"] == sp.STATUS_FAILED
    assert "code 190/460" in r["detail"] and "NOT confirmed" in r["detail"]
    assert sp.run_verdict(sp.RUN_RESULTS)[0] == 1


def test_facebook_make_only_is_unconfirmed_not_fatal(monkeypatch, fake_post):
    routes, _ = fake_post
    monkeypatch.setattr(sp, "MAKE_WEBHOOK_URL", "https://hook.make.com/x")
    routes["hook.make.com"] = FakeResponse(200, None, text="Accepted")
    assert sp.post_facebook("hello") is True
    assert sp.RUN_RESULTS[-1]["status"] == sp.STATUS_UNCONFIRMED
    summary = sp.render_summary("rabbi", sp.RUN_RESULTS)
    assert "NON CONFIRMÉ" in summary and "vérifier la page Facebook" in summary


def test_facebook_make_error_is_a_failure(monkeypatch, fake_post):
    routes, _ = fake_post
    monkeypatch.setattr(sp, "MAKE_WEBHOOK_URL", "https://hook.make.com/x")
    routes["hook.make.com"] = FakeResponse(500, None, text="Scenario error")
    assert sp.post_facebook("hello") is False
    assert sp.RUN_RESULTS[-1]["status"] == sp.STATUS_FAILED


# ---------------------------------------------------------------------------
# Instagram
# ---------------------------------------------------------------------------
def test_instagram_ok(monkeypatch, fake_post):
    routes, calls = fake_post
    monkeypatch.setattr(sp, "IG_USER_ID", "ig1")
    monkeypatch.setattr(sp, "FB_TOKEN", "tok")
    routes["/ig1/media_publish"] = FakeResponse(200, {"id": "media-9"})
    routes["/ig1/media"] = FakeResponse(200, {"id": "container-1"})
    assert sp.post_instagram("cap", "https://img") is True
    assert calls[1]["data"]["creation_id"] == "container-1"
    assert sp.RUN_RESULTS[-1]["status"] == sp.STATUS_OK


def test_instagram_session_invalidated_fails_the_run(monkeypatch, fake_post, tmp_path, capsys):
    routes, _ = fake_post
    monkeypatch.setattr(sp, "IG_USER_ID", "ig1")
    monkeypatch.setattr(sp, "FB_TOKEN", "SECRET-TOKEN-VALUE")
    routes["/ig1/media"] = FakeResponse(400, IG_SESSION_INVALIDATED)
    assert sp.post_instagram("cap", "https://img", post="paracha") is False

    summary_file = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    code = sp.emit_summary("paracha", sp.RUN_RESULTS)
    assert code == 1
    text = summary_file.read_text(encoding="utf-8")
    assert "ÉCHEC" in text and "session has been invalidated" in text and "code 190/460" in text
    assert "SECRET-TOKEN-VALUE" not in text
    assert "::error title=Social post paracha::" in capsys.readouterr().out


def test_instagram_not_configured_is_not_fatal_when_facebook_ok(monkeypatch, fake_post):
    routes, _ = fake_post
    monkeypatch.setattr(sp, "FB_PAGE_ID", "123")
    monkeypatch.setattr(sp, "FB_TOKEN", "tok")
    routes["/123/feed"] = FakeResponse(200, {"id": "123_1"})
    sp.post_facebook("m")
    monkeypatch.setattr(sp, "FB_TOKEN", "")
    sp.post_instagram("m", "https://img")
    assert sp.RUN_RESULTS[-1]["status"] == sp.STATUS_NOT_CONFIGURED
    assert sp.run_verdict(sp.RUN_RESULTS)[0] == 0


def test_nothing_configured_fails_a_real_run():
    sp.post_facebook("m")
    sp.post_instagram("m", "https://img")
    code, reasons = sp.run_verdict(sp.RUN_RESULTS, dry_run=False)
    assert code == 1 and "no social channel configured" in reasons[0]


# ---------------------------------------------------------------------------
# main(): exit code + state still saved on failure
# ---------------------------------------------------------------------------
def test_main_exits_1_and_saves_state_when_a_channel_fails(monkeypatch, tmp_path, fake_post):
    routes, _ = fake_post
    monkeypatch.chdir(tmp_path)
    (tmp_path / "channels.json").write_text(json.dumps(CHANNELS), encoding="utf-8")
    (tmp_path / "social_state.json").write_text(json.dumps(
        {"rabbi_index": 0, "theme_index": 0, "last_posted": {}, "announced_rabbis": ["rav-a", "rav-b"]}),
        encoding="utf-8")
    monkeypatch.setattr(sp, "load_entries", lambda slug: ENTRIES.get(slug, []))
    monkeypatch.setattr(sp, "FB_PAGE_ID", "123")
    monkeypatch.setattr(sp, "FB_TOKEN", "tok")
    monkeypatch.setattr(sp, "IG_USER_ID", "ig1")
    routes["/123/feed"] = FakeResponse(200, {"id": "123_1"})
    routes["/ig1/media"] = FakeResponse(400, IG_SESSION_INVALIDATED)
    summary_file = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
    monkeypatch.setattr(sys, "argv", ["social_post.py", "--type", "rabbi"])

    assert sp.main() == 1
    state = json.loads((tmp_path / "social_state.json").read_text(encoding="utf-8"))
    assert state["rabbi_index"] == 1 and "rabbi" in state["last_posted"]
    text = summary_file.read_text(encoding="utf-8")
    assert "facebook" in text and "OK (publication confirmée)" in text and "ÉCHEC" in text


def test_main_unexpected_exception_is_reported(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "channels.json").write_text(json.dumps(CHANNELS), encoding="utf-8")
    (tmp_path / "social_state.json").write_text(json.dumps(
        {"rabbi_index": 0, "theme_index": 0, "last_posted": {}, "announced_rabbis": ["rav-a", "rav-b"]}),
        encoding="utf-8")

    def boom(*a, **k):
        raise KeyError("podcast_author")

    monkeypatch.setattr(sp, "post_theme", boom)
    monkeypatch.setattr(sys, "argv", ["social_post.py", "--type", "theme", "--dry-run"])
    assert sp.main() == 1
    assert sp.RUN_RESULTS[-1]["channel"] == "script"
