"""Tests for candidate_channels_report.py — contact extraction and parsing.

Network calls are never exercised here: api_get is monkeypatched to return
canned responses, matching the mocking approach requested for this workflow
(real API responses would burn quota and require a live key).
"""
import csv
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import candidate_channels_report as m


# ── extract_contacts ─────────────────────────────────────────────────────────

def test_extract_contacts_email():
    c = m.extract_contacts("Contactez-nous a contact@example.com pour plus d'infos.")
    assert c["emails"] == ["contact@example.com"]
    assert c["source"] == "description YouTube"


def test_extract_contacts_multiple_emails_sorted_deduped():
    desc = "b@example.com puis a@example.com puis b@example.com encore"
    c = m.extract_contacts(desc)
    assert c["emails"] == ["a@example.com", "b@example.com"]


def test_extract_contacts_whatsapp_link():
    c = m.extract_contacts("Ecrivez-nous sur https://wa.me/972537082212")
    assert c["whatsapp"] == ["972537082212"]
    assert c["phones"] == []  # not double-counted as a bare phone


def test_extract_contacts_whatsapp_api_form():
    c = m.extract_contacts("WhatsApp: https://api.whatsapp.com/send?phone=33612345678")
    assert c["whatsapp"] == ["33612345678"]


def test_extract_contacts_phone_french():
    c = m.extract_contacts("Appelez le 06 12 34 56 78 pour toute question.")
    assert c["phones"] == ["06 12 34 56 78"]


def test_extract_contacts_phone_international():
    c = m.extract_contacts("Tel: +972-54-522-8624")
    assert c["phones"] == ["+972-54-522-8624"]


def test_extract_contacts_website_found_and_youtube_excluded():
    desc = "Site: https://www.dafyomi.fr/ chaine https://www.youtube.com/@dafyomi"
    c = m.extract_contacts(desc)
    assert c["websites"] == ["https://www.dafyomi.fr/"]


def test_extract_contacts_wa_me_not_duplicated_as_website():
    c = m.extract_contacts("Contact: https://wa.me/972537082212")
    assert c["websites"] == []


def test_extract_contacts_empty_description():
    c = m.extract_contacts("")
    assert c == {"emails": [], "whatsapp": [], "phones": [], "websites": [], "source": ""}


def test_extract_contacts_none_description():
    c = m.extract_contacts(None)
    assert c["emails"] == []
    assert c["source"] == ""


def test_extract_contacts_ignores_short_digit_runs():
    # A video timestamp / year-like number should not become a "phone".
    c = m.extract_contacts("Episode 2026, chapitre 5, minute 12:34")
    assert c["phones"] == []


# ── is_hebrew ────────────────────────────────────────────────────────────────

def test_is_hebrew_true_for_hebrew_text():
    assert m.is_hebrew("שיעור תורה") is True


def test_is_hebrew_false_for_french_text():
    assert m.is_hebrew("cours de Torah") is False


def test_is_hebrew_false_for_empty():
    assert m.is_hebrew("") is False
    assert m.is_hebrew(None) is False


# ── resolve_handle (no network — direct URL/id parsing branches) ────────────

def test_resolve_handle_channel_url():
    cid, note = m.resolve_handle(
        "https://www.youtube.com/channel/UC0M3nu0QEa7ivwLHorzPlPg", api_key="x", quota=m.QuotaTracker()
    )
    assert cid == "UC0M3nu0QEa7ivwLHorzPlPg"
    assert "0 quota" in note


def test_resolve_handle_bare_channel_id():
    quota = m.QuotaTracker()
    cid, note = m.resolve_handle("UC0M3nu0QEa7ivwLHorzPlPg", api_key="x", quota=quota)
    assert cid == "UC0M3nu0QEa7ivwLHorzPlPg"
    assert quota.used == 0


def test_resolve_handle_uploads_playlist_url():
    cid, note = m.resolve_handle(
        "https://www.youtube.com/playlist?list=UU0M3nu0QEa7ivwLHorzPlPg",
        api_key="x",
        quota=m.QuotaTracker(),
    )
    assert cid == "UC0M3nu0QEa7ivwLHorzPlPg"


def test_resolve_handle_empty_line():
    cid, note = m.resolve_handle("", api_key="x", quota=m.QuotaTracker())
    assert cid is None
    assert "empty" in note


def test_resolve_handle_at_handle_via_api(monkeypatch):
    calls = []

    def fake_api_get(endpoint, params, api_key, timeout=15):
        calls.append((endpoint, params))
        assert endpoint == "channels"
        assert params["forHandle"] == "RavExample"
        return {"items": [{"id": "UCabcdefghij12345"}]}

    monkeypatch.setattr(m, "api_get", fake_api_get)
    quota = m.QuotaTracker()
    cid, note = m.resolve_handle("https://www.youtube.com/@RavExample", api_key="x", quota=quota)
    assert cid == "UCabcdefghij12345"
    assert quota.used == 1
    assert len(calls) == 1


def test_resolve_handle_at_handle_no_result(monkeypatch):
    monkeypatch.setattr(m, "api_get", lambda *a, **k: {"items": []})
    quota = m.QuotaTracker()
    cid, note = m.resolve_handle("@nobody", api_key="x", quota=quota)
    assert cid is None
    assert quota.used == 1


def test_resolve_handle_search_fallback(monkeypatch):
    def fake_api_get(endpoint, params, api_key, timeout=15):
        assert endpoint == "search"
        assert params["q"] == "Some Rav Name"
        return {"items": [{"snippet": {"channelId": "UCsearchfallback001"}}]}

    monkeypatch.setattr(m, "api_get", fake_api_get)
    quota = m.QuotaTracker()
    cid, note = m.resolve_handle("Some Rav Name", api_key="x", quota=quota)
    assert cid == "UCsearchfallback001"
    assert quota.used == 100


def test_resolve_handle_respects_quota_budget():
    quota = m.QuotaTracker(limit=0)
    cid, note = m.resolve_handle("@someone", api_key="x", quota=quota)
    assert cid is None
    assert "budget" in note


# ── QuotaTracker ─────────────────────────────────────────────────────────────

def test_quota_tracker_can_afford_and_spend():
    q = m.QuotaTracker(limit=150)
    assert q.can_afford(100) is True
    q.spend(100, "search.list test")
    assert q.used == 100
    assert q.can_afford(100) is False
    assert q.can_afford(50) is True


def test_quota_tracker_stops_before_exceeding_budget():
    q = m.QuotaTracker(limit=9000)
    q.used = 8950
    assert q.can_afford(100) is False
    assert q.can_afford(50) is True


# ── discover_channels ────────────────────────────────────────────────────────

def test_discover_channels_queries_fr_and_il(monkeypatch):
    calls = []

    def fake_api_get(endpoint, params, api_key, timeout=15):
        calls.append(params["regionCode"])
        return {"items": [{"snippet": {"channelId": f"UC_{params['regionCode']}", "title": "x"}}]}

    monkeypatch.setattr(m, "api_get", fake_api_get)
    quota = m.QuotaTracker()
    found = m.discover_channels("cours de Torah", "x", quota, max_results=25)
    assert calls == ["FR", "IL"]
    assert set(found) == {"UC_FR", "UC_IL"}
    assert quota.used == 200


def test_discover_channels_uses_hebrew_relevance_language(monkeypatch):
    seen_langs = []

    def fake_api_get(endpoint, params, api_key, timeout=15):
        seen_langs.append(params["relevanceLanguage"])
        return {"items": []}

    monkeypatch.setattr(m, "api_get", fake_api_get)
    m.discover_channels("שיעור תורה", "x", m.QuotaTracker(), max_results=25)
    assert seen_langs == ["he", "he"]


def test_discover_channels_stops_on_exhausted_budget(monkeypatch):
    calls = []
    monkeypatch.setattr(m, "api_get", lambda *a, **k: calls.append(1) or {"items": []})
    quota = m.QuotaTracker(limit=100)
    quota.used = 100  # nothing left
    found = m.discover_channels("cours de Torah", "x", quota, max_results=25)
    assert found == {}
    assert calls == []


# ── fetch_recent_activity ────────────────────────────────────────────────────

def test_fetch_recent_activity_counts_last_30_days(monkeypatch):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=5)).isoformat().replace("+00:00", "Z")
    old = (now - timedelta(days=200)).isoformat().replace("+00:00", "Z")

    def fake_api_get(endpoint, params, api_key, timeout=15):
        assert endpoint == "playlistItems"
        return {
            "items": [
                {"contentDetails": {"videoPublishedAt": recent}},
                {"contentDetails": {"videoPublishedAt": old}},
            ]
        }

    monkeypatch.setattr(m, "api_get", fake_api_get)
    last_published, count_30d, capped = m.fetch_recent_activity("UUsomeplaylist", "x", m.QuotaTracker())
    assert last_published == recent
    assert count_30d == 1
    assert capped is False


def test_fetch_recent_activity_no_playlist_id():
    last_published, count_30d, capped = m.fetch_recent_activity(None, "x", m.QuotaTracker())
    assert last_published is None
    assert count_30d == 0


def test_fetch_recent_activity_survives_404(monkeypatch):
    # Regression: a deleted/private uploads playlist 404s. This must degrade
    # to "no activity data" for that one channel, not crash the whole run
    # (real incident: run 36528648953 died here after spending 2710 quota
    # units on everything before it).
    import urllib.error

    def raising_api_get(endpoint, params, api_key, timeout=15):
        raise urllib.error.HTTPError(url="x", code=404, msg="Not Found", hdrs=None, fp=None)

    monkeypatch.setattr(m, "api_get", raising_api_get)
    last_published, count_30d, capped = m.fetch_recent_activity("UUdeleted", "x", m.QuotaTracker())
    assert last_published is None
    assert count_30d == 0
    assert capped is False


def test_fetch_channel_details_survives_404(monkeypatch, capsys):
    import urllib.error

    def raising_api_get(endpoint, params, api_key, timeout=15):
        raise urllib.error.HTTPError(url="x", code=404, msg="Not Found", hdrs=None, fp=None)

    monkeypatch.setattr(m, "api_get", raising_api_get)
    details = m.fetch_channel_details(["UC1", "UC2"], "x", m.QuotaTracker())
    assert details == {}
    assert "HTTP 404" in capsys.readouterr().out


def test_discover_channels_survives_network_error(monkeypatch):
    def raising_api_get(endpoint, params, api_key, timeout=15):
        raise urllib.error.URLError("temporary failure in name resolution")

    monkeypatch.setattr(m, "api_get", raising_api_get)
    found = m.discover_channels("cours de Torah", "x", m.QuotaTracker(), max_results=25)
    assert found == {}


def test_fetch_recent_activity_budget_exhausted():
    quota = m.QuotaTracker(limit=0)
    last_published, count_30d, capped = m.fetch_recent_activity("UUplaylist", "x", quota)
    assert "budget" in last_published


# ── channels.json exclusion ──────────────────────────────────────────────────

def test_load_existing_channel_ids(tmp_path):
    p = tmp_path / "channels.json"
    p.write_text(
        json.dumps([{"slug": "a", "youtube_channel_id": "UC1"}, {"slug": "b"}]),
        encoding="utf-8",
    )
    assert m.load_existing_channel_ids(p) == {"UC1"}


def test_load_existing_channel_ids_missing_file(tmp_path, capsys):
    ids = m.load_existing_channel_ids(tmp_path / "nope.json")
    assert ids == set()
    assert "not found" in capsys.readouterr().out


# ── CSV writing ──────────────────────────────────────────────────────────────

def test_write_csv_roundtrip(tmp_path):
    row = m.build_row(
        "UC123",
        {
            "snippet": {
                "title": "Rav Example",
                "customUrl": "@ravexample",
                "country": "FR",
                "description": "contact@example.com",
            },
            "statistics": {"subscriberCount": "1000", "videoCount": "50", "viewCount": "20000"},
        },
        ("2026-09-01T00:00:00Z", 3, False),
        {"query: cours de Torah"},
    )
    out = tmp_path / "out.csv"
    m.write_csv([row], out)
    with open(out, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["channel_id"] == "UC123"
    assert rows[0]["email"] == "contact@example.com"
    assert rows[0]["subscriber_count"] == "1000"


def test_build_row_hidden_subscriber_count():
    row = m.build_row(
        "UC456",
        {"snippet": {"title": "T"}, "statistics": {"hiddenSubscriberCount": True}},
        (None, 0, False),
        {"handle: x"},
    )
    assert row["subscriber_count"] == "masqué"


def test_build_row_capped_30d_count():
    row = m.build_row(
        "UC789",
        {"snippet": {"title": "T"}, "statistics": {}},
        ("2026-09-01T00:00:00Z", 50, True),
        {"handle: x"},
    )
    assert row["videos_last_30d"] == ">=50"


# ── parse_lines ──────────────────────────────────────────────────────────────

def test_parse_lines_strips_blanks():
    assert m.parse_lines("a\n\n b \n\nc") == ["a", "b", "c"]


def test_parse_lines_empty_input():
    assert m.parse_lines("") == []
    assert m.parse_lines(None) == []
