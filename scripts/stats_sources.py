"""Espace rav — data sources (network). Each one is optional.

A source is ACTIVE as soon as its secret exists; otherwise it reports itself
inactive and the pipeline goes on with the others (never a silent zero: the
workflow summary lists which sources ran).

  * Cloudflare R2 downloads — GraphQL Analytics dataset
    `r2OperationsAdaptiveGroups` (GetObject requests per object, per day).
    Needs CF_API_TOKEN (permission "Account Analytics: Read"). The account id
    is taken from CF_ACCOUNT_ID or, failing that, from the host of
    R2_ENDPOINT_URL (<account>.r2.cloudflarestorage.com). Retention on
    Cloudflare's side is ~31 days: the daily job archives into the history.
  * GA4 site plays — Data API (runReport) on the property of the site tag
    G-7Z2QEN865Y. Needs GA4_SA_JSON (service account key with Viewer access
    on the property). GA4_PROPERTY_ID is optional: without it the property
    is discovered through the Admin API (data stream measurement id).

Nothing sensitive is printed: only row / day counts.
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from urllib.parse import urlsplit

import stats_core as core

CF_GRAPHQL = "https://api.cloudflare.com/client/v4/graphql"
GA4_MEASUREMENT_ID = "G-7Z2QEN865Y"
GA_DATA = "https://analyticsdata.googleapis.com/v1beta"
GA_ADMIN = "https://analyticsadmin.googleapis.com/v1beta"


class SourceUnavailable(Exception):
    """The source is not configured (missing secret) — not an error."""


# --------------------------------------------------------------------------
# Cloudflare R2
# --------------------------------------------------------------------------

def cf_account_id() -> str:
    acc = (os.environ.get("CF_ACCOUNT_ID") or "").strip()
    if acc:
        return acc
    host = urlsplit((os.environ.get("R2_ENDPOINT_URL") or "").strip()).netloc
    if host.endswith(".r2.cloudflarestorage.com"):
        return host.split(".", 1)[0]
    raise SourceUnavailable("CF_ACCOUNT_ID absent and not derivable from R2_ENDPOINT_URL")


R2_QUERY = """
query R2Downloads($account: string!, $day: Date!, $bucket: string!) {
  viewer {
    accounts(filter: {accountTag: $account}) {
      r2OperationsAdaptiveGroups(
        limit: 10000
        filter: {date: $day, bucketName: $bucket, actionType: "GetObject"}
      ) {
        sum { requests }
        dimensions { objectName }
      }
    }
  }
}
"""


def parse_r2_groups(payload: dict) -> dict[str, int]:
    """GraphQL response -> {video_id: requests} (non-MP3 objects ignored)."""
    if payload.get("errors"):
        msgs = "; ".join(str(e.get("message")) for e in payload["errors"])
        raise RuntimeError(f"Cloudflare GraphQL error: {msgs}")
    accounts = (((payload.get("data") or {}).get("viewer") or {}).get("accounts") or [])
    out: dict[str, int] = {}
    for acc in accounts:
        for g in acc.get("r2OperationsAdaptiveGroups") or []:
            vid = core.video_id_from_object((g.get("dimensions") or {}).get("objectName") or "")
            n = int(((g.get("sum") or {}).get("requests")) or 0)
            if vid and n > 0:
                out[vid] = out.get(vid, 0) + n
    return out


def fetch_r2_downloads(days: list[date], session=None) -> dict[str, dict[str, int]]:
    token = (os.environ.get("CF_API_TOKEN") or "").strip()
    if not token:
        raise SourceUnavailable("CF_API_TOKEN absent")
    bucket = (os.environ.get("R2_BUCKET_NAME") or "").strip()
    if not bucket:
        raise SourceUnavailable("R2_BUCKET_NAME absent")
    account = cf_account_id()
    import requests
    s = session or requests.Session()
    out = {}
    for d in days:
        r = s.post(CF_GRAPHQL, timeout=60,
                   headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                   json={"query": R2_QUERY,
                         "variables": {"account": account, "day": d.isoformat(), "bucket": bucket}})
        if r.status_code in (401, 403):
            raise RuntimeError(f"Cloudflare API refused the token (HTTP {r.status_code}): "
                               "it needs the 'Account Analytics: Read' permission")
        r.raise_for_status()
        out[d.isoformat()] = parse_r2_groups(r.json())
    return out


# --------------------------------------------------------------------------
# GA4
# --------------------------------------------------------------------------

def _ga_session():
    raw = (os.environ.get("GA4_SA_JSON") or "").strip()
    if not raw:
        raise SourceUnavailable("GA4_SA_JSON absent")
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    creds = service_account.Credentials.from_service_account_info(
        json.loads(raw), scopes=["https://www.googleapis.com/auth/analytics.readonly"])
    return AuthorizedSession(creds)


def ga4_property(session) -> str:
    pid = (os.environ.get("GA4_PROPERTY_ID") or "").strip()
    if pid:
        return pid if pid.startswith("properties/") else f"properties/{pid}"
    r = session.get(f"{GA_ADMIN}/accountSummaries", params={"pageSize": 200}, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"GA4 Admin API HTTP {r.status_code}: set GA4_PROPERTY_ID or enable "
                           "the Google Analytics Admin API on the service account's project")
    props = [p["property"] for a in r.json().get("accountSummaries", [])
             for p in a.get("propertySummaries", [])]
    for prop in props:
        rs = session.get(f"{GA_ADMIN}/{prop}/dataStreams", timeout=60)
        if rs.status_code >= 400:
            continue
        for st in rs.json().get("dataStreams", []):
            if (st.get("webStreamData") or {}).get("measurementId") == GA4_MEASUREMENT_ID:
                return prop
    raise RuntimeError(f"no GA4 property visible to the service account carries {GA4_MEASUREMENT_ID} "
                       f"({len(props)} properties visible)")


def _run_report(session, prop: str, start: date, end: date, dims: list[str]) -> list[dict]:
    body = {
        "dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
        "dimensions": [{"name": d} for d in dims],
        "metrics": [{"name": "eventCount"}],
        "dimensionFilter": {"filter": {"fieldName": "eventName",
                                       "inListFilter": {"values": list(core.GA4_EVENTS)}}},
        "limit": 100000,
    }
    r = session.post(f"{GA_DATA}/{prop}:runReport", json=body, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"GA4 runReport HTTP {r.status_code}: {r.text[:300]}")
    return r.json().get("rows") or []


def _ga_day(v: str) -> str:
    return f"{v[:4]}-{v[4:6]}-{v[6:8]}"


def parse_ga_page_rows(rows: list[dict]) -> dict[str, dict[str, int]]:
    """rows of (date, eventName, pagePath) -> {day: {'event|url_slug': n}}."""
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        d, ev, path = [x.get("value", "") for x in row["dimensionValues"]]
        page = core.page_slug(path)
        if not page:
            continue
        n = int(row["metricValues"][0]["value"])
        day = out.setdefault(_ga_day(d), {})
        key = f"{ev}|{page}"
        day[key] = day.get(key, 0) + n
    return out


def parse_ga_ep_rows(rows: list[dict]) -> dict[str, dict[str, int]]:
    """rows of (date, eventName, rav, ep_title) -> {day: {'event|rav|title': n}}."""
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        d, ev, rav, title = [x.get("value", "") for x in row["dimensionValues"]]
        if rav in ("", "(not set)"):
            continue
        n = int(row["metricValues"][0]["value"])
        day = out.setdefault(_ga_day(d), {})
        key = f"{ev}|{rav.replace('|', ' ')}|{title}"
        day[key] = day.get(key, 0) + n
    return out


def fetch_ga4(start: date, end: date) -> tuple[dict, dict | None, str]:
    """-> (site_page days, site_ep days or None, note).

    site_ep needs the event-scoped custom dimensions `rav` and `ep_title` to
    be declared in the GA4 admin; when they are not, only the per-page
    totals are returned (note says so).
    """
    s = _ga_session()
    prop = ga4_property(s)
    page = parse_ga_page_rows(_run_report(s, prop, start, end, ["date", "eventName", "pagePath"]))
    for day in _each_day(start, end):
        page.setdefault(day.isoformat(), {})
    try:
        ep = parse_ga_ep_rows(_run_report(s, prop, start, end,
                                          ["date", "eventName", "customEvent:rav", "customEvent:ep_title"]))
        for day in _each_day(start, end):
            ep.setdefault(day.isoformat(), {})
        note = "per-course dimensions OK"
    except RuntimeError as exc:
        ep, note = None, f"per-course dimensions unavailable ({str(exc)[:160]})"
    return page, ep, note


def _each_day(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)
