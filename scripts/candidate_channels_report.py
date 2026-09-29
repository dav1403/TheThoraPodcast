#!/usr/bin/env python3
"""
candidate_channels_report.py
-----------------------------
Manual workflow_dispatch tool: builds a CSV of candidate YouTube channels for
TheThoraPodcast onboarding, using verified YouTube Data API v3 numbers instead
of the "non vérifié (pas de clé API)" placeholders that filled the previous
manual pass (candidats-rabbins-2026-09-29.csv).

Two independent sources feed the same candidate pool:
  - HANDLES  : known channels (URL, @handle, channel ID, or /c//user/ name),
               resolved directly.
  - QUERIES  : free-text discovery terms, searched via search.list(type=channel)
               once per region (FR, IL) — the two markets where a francophone
               or Hebrew Torah-study channel is most likely to be registered.

Every candidate that resolves to a channel_id already present in
channels.json (field youtube_channel_id) is dropped from the CSV and only
logged — those are already onboarded, re-listing them would just be noise for
David's review.

Quota (2026 default: 10 000 units/day):
  channels.list      = 1 unit  (even batched up to 50 ids)
  playlistItems.list = 1 unit  (one page, up to 50 items)
  search.list        = 100 units (by far the expensive call)
A running QuotaTracker stops issuing further calls once the *projected* total
would cross QUOTA_BUDGET_LIMIT (9 000, leaving headroom below the 10k/day
ceiling for whatever else uses the same key that day). Channels already
resolved keep going through the cheaper detail/upload steps; only the
search.list discovery loop and the tail of the detail-fetch loop are cut off,
and every skip is logged so the CSV never *silently* looks complete.

Output: a CSV (UTF-8 BOM, opens correctly in Excel) written to the path given
by --output, uploaded as a build artifact by the workflow — never committed
(the repo is public, contact info found in descriptions must not land in git
history).
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API_BASE = "https://www.googleapis.com/youtube/v3/"
QUOTA_BUDGET_LIMIT = 9000
ACTIVE_WINDOW_DAYS = 60  # informational column only; filtering to "active" is
                         # left to whoever consumes the CSV (see module docstring
                         # of the caller / session report), not hardcoded here.

HEBREW_RE = re.compile(r"[֐-׿]")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# wa.me/<digits> or api.whatsapp.com/send?phone=<digits>, with or without a
# leading '+', optional scheme, optional surrounding punctuation.
WHATSAPP_RE = re.compile(
    r"(?:https?://)?(?:wa\.me/|api\.whatsapp\.com/send\?phone=)\+?(\d[\d\s\-]{6,}\d)",
    re.IGNORECASE,
)
# Generic phone number: a run of 8-15 digits allowing spaces/dots/dashes and an
# optional leading '+'. Deliberately loose (French, Israeli, and generic intl
# formats all match) — false positives from stray long numbers are possible,
# hence the "source = description YouTube" label on every extracted contact
# rather than a claim of certainty.
PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d .\-]{6,13}\d)(?!\d)")
URL_RE = re.compile(r"https?://[^\s<>\)\]\"']+")

SELF_REFERENTIAL_DOMAINS = ("youtube.com", "youtu.be", "wa.me", "whatsapp.com")


class QuotaTracker:
    """Tracks cumulative YouTube Data API quota units and refuses calls that
    would push the running total past the budget. Refusing *before* the call
    (rather than after) is what makes the stop clean: no half-issued request,
    no partially-consumed response to unwind."""

    def __init__(self, limit=QUOTA_BUDGET_LIMIT):
        self.limit = limit
        self.used = 0

    def can_afford(self, cost):
        return self.used + cost <= self.limit

    def spend(self, cost, label):
        self.used += cost
        print(f"  [quota] +{cost} ({label}) -> total {self.used}/{self.limit}")


def api_get(endpoint, params, api_key, timeout=15):
    params = dict(params)
    params["key"] = api_key
    url = API_BASE + endpoint + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "ttp-candidate-report/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def safe_api_get(endpoint, params, api_key, timeout=15):
    """api_get wrapped against HTTP/network errors. A single bad id (a
    private/deleted playlist returning 404, a transient 5xx, a timeout on one
    of hundreds of calls) must degrade that one candidate, not crash a run
    that already spent thousands of quota units on everything before it.
    Returns {} on failure, exactly like an API response with no items — every
    call site here already treats an empty/absent "items" list as "nothing
    found", so this reuses that path instead of adding a second branch."""
    try:
        return api_get(endpoint, params, api_key, timeout=timeout)
    except urllib.error.HTTPError as e:
        print(f"  [warn] {endpoint} {params.get('id') or params.get('playlistId') or params.get('q') or ''}: HTTP {e.code} {e.reason}")
        return {}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  [warn] {endpoint} {params.get('id') or params.get('playlistId') or params.get('q') or ''}: {e}")
        return {}


def is_hebrew(text):
    return bool(HEBREW_RE.search(text or ""))


# ── contact extraction ──────────────────────────────────────────────────────

def extract_contacts(description):
    """Pulls emails / WhatsApp links / phone numbers / websites out of a
    YouTube channel description. Every hit is tagged with its source so a
    human reviewer can tell "found in the description" from a guess."""
    description = description or ""
    source = "description YouTube"

    emails = sorted(set(EMAIL_RE.findall(description)))

    whatsapp_numbers = sorted({m.replace(" ", "").replace("-", "") for m in WHATSAPP_RE.findall(description)})

    # Phone numbers: reuse the WhatsApp digits so the same number isn't also
    # reported as a bare phone number, then scan the rest for standalone runs.
    wa_spans = {m.span(1) for m in WHATSAPP_RE.finditer(description)}
    phones = []
    for m in PHONE_RE.finditer(description):
        if m.span(1) in wa_spans:
            continue
        digits = re.sub(r"[^\d]", "", m.group(1))
        if 8 <= len(digits) <= 15:
            phones.append(m.group(1).strip())
    phones = sorted(set(phones))

    websites = []
    for m in URL_RE.finditer(description):
        url = m.group(0).rstrip(".,;:!?")
        host = urllib.parse.urlparse(url).netloc.lower()
        if any(d in host for d in SELF_REFERENTIAL_DOMAINS):
            continue
        websites.append(url)
    websites = sorted(set(websites))

    return {
        "emails": emails,
        "whatsapp": whatsapp_numbers,
        "phones": phones,
        "websites": websites,
        "source": source if (emails or whatsapp_numbers or phones or websites) else "",
    }


# ── handle / URL resolution ─────────────────────────────────────────────────

def resolve_handle(line, api_key, quota):
    """Resolves one HANDLES line to a channel_id. Returns (channel_id, note)
    — channel_id is None on failure, note explains why (also used to decide
    whether the search.list fallback fires)."""
    line = line.strip()
    if not line:
        return None, "empty line"

    m = re.search(r"[?&]list=(UU[A-Za-z0-9_-]+)", line)
    if m:
        return "UC" + m.group(1)[2:], "resolved from uploads-playlist id (0 quota)"

    m = re.search(r"/channel/(UC[A-Za-z0-9_-]{10,})", line)
    if m:
        return m.group(1), "resolved from URL (0 quota)"

    if re.fullmatch(r"UC[A-Za-z0-9_-]{10,}", line):
        return line, "already a channel id (0 quota)"

    m = re.search(r"/@([A-Za-z0-9_.\-]+)", line) or re.fullmatch(r"@([A-Za-z0-9_.\-]+)", line)
    if m:
        handle = m.group(1)
        if not quota.can_afford(1):
            return None, "quota budget exhausted before forHandle lookup"
        data = safe_api_get("channels", {"part": "id", "forHandle": handle}, api_key)
        quota.spend(1, f"channels.list forHandle @{handle}")
        items = data.get("items", [])
        if items:
            return items[0]["id"], f"resolved @{handle} via forHandle"
        return None, f"forHandle @{handle}: no result"

    m = re.search(r"youtube\.com/(?:c/|user/)([^/?&#]+)", line)
    if m:
        name = m.group(1)
        if not quota.can_afford(1):
            return None, "quota budget exhausted before forUsername lookup"
        data = safe_api_get("channels", {"part": "id", "forUsername": name}, api_key)
        quota.spend(1, f"channels.list forUsername {name}")
        items = data.get("items", [])
        if items:
            return items[0]["id"], f"resolved {name} via forUsername"
        # fall through to search — /c/ names are often display names, not
        # legacy usernames, and forUsername frequently returns nothing for them.

    if not re.match(r"https?://", line):
        query = line
    elif m:
        query = m.group(1)
    else:
        query = line

    if not quota.can_afford(100):
        return None, "quota budget exhausted before search.list fallback"
    data = safe_api_get(
        "search",
        {"part": "snippet", "type": "channel", "q": query, "maxResults": 1},
        api_key,
    )
    quota.spend(100, f"search.list fallback '{query}'")
    items = data.get("items", [])
    if items:
        return items[0]["snippet"]["channelId"], f"resolved '{query}' via search.list fallback"
    return None, f"'{query}': no match even via search.list fallback"


def discover_channels(query, api_key, quota, max_results):
    """search.list(type=channel) for one discovery query, run once for
    regionCode=FR and once for regionCode=IL, both with the query's own
    script driving relevanceLanguage (fr, or he for a Hebrew query)."""
    lang = "he" if is_hebrew(query) else "fr"
    found = {}  # channel_id -> title
    for region in ("FR", "IL"):
        if not quota.can_afford(100):
            print(f"  [quota] budget exhausted, skipping search.list '{query}' regionCode={region}")
            continue
        data = safe_api_get(
            "search",
            {
                "part": "snippet",
                "type": "channel",
                "q": query,
                "regionCode": region,
                "relevanceLanguage": lang,
                "maxResults": max_results,
            },
            api_key,
        )
        quota.spend(100, f"search.list '{query}' regionCode={region} lang={lang}")
        for item in data.get("items", []):
            cid = item["snippet"]["channelId"]
            found[cid] = item["snippet"].get("title", "")
    return found


# ── channel detail + activity ───────────────────────────────────────────────

def fetch_channel_details(channel_ids, api_key, quota):
    """channels.list in batches of 50 — 1 quota unit per batch regardless of
    how many ids are in it, so this is the cheap part of the pipeline."""
    details = {}
    ids = list(channel_ids)
    for i in range(0, len(ids), 50):
        batch = ids[i : i + 50]
        if not quota.can_afford(1):
            print(f"  [quota] budget exhausted, skipping channels.list batch of {len(batch)}")
            break
        data = safe_api_get(
            "channels",
            {"part": "snippet,statistics,contentDetails", "id": ",".join(batch)},
            api_key,
        )
        quota.spend(1, f"channels.list batch of {len(batch)}")
        for item in data.get("items", []):
            details[item["id"]] = item
    return details


def fetch_recent_activity(uploads_playlist_id, api_key, quota):
    """One page (<=50 items) of the channel's uploads playlist. Returns
    (last_published_iso, videos_last_30d, capped) — capped=True means all 50
    returned items were within the 30-day window, so the real count may be
    higher than what a single page can show."""
    if not uploads_playlist_id:
        return None, 0, False
    if not quota.can_afford(1):
        return "non vérifié (budget quota atteint)", 0, False
    data = safe_api_get(
        "playlistItems",
        {"part": "contentDetails", "playlistId": uploads_playlist_id, "maxResults": 50},
        api_key,
    )
    quota.spend(1, f"playlistItems.list {uploads_playlist_id}")
    items = data.get("items", [])
    if not items:
        return None, 0, False
    dates = [it["contentDetails"]["videoPublishedAt"] for it in items if it.get("contentDetails", {}).get("videoPublishedAt")]
    if not dates:
        return None, 0, False
    dates.sort(reverse=True)
    last_published = dates[0]
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    count_30d = sum(1 for d in dates if datetime.fromisoformat(d.replace("Z", "+00:00")) >= cutoff)
    capped = count_30d == len(dates) == 50
    return last_published, count_30d, capped


def days_since(iso_date):
    if not iso_date or not isinstance(iso_date, str) or "T" not in iso_date:
        return None
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - dt).days


# ── channels.json exclusion ──────────────────────────────────────────────────

def load_existing_channel_ids(channels_json_path):
    path = Path(channels_json_path)
    if not path.exists():
        print(f"WARNING: {channels_json_path} not found, cannot exclude already-integrated channels")
        return set()
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return {ch["youtube_channel_id"] for ch in data if ch.get("youtube_channel_id")}


# ── CSV output ───────────────────────────────────────────────────────────────

CSV_FIELDS = [
    "channel_id",
    "title",
    "custom_url",
    "country",
    "subscriber_count",
    "video_count",
    "view_count",
    "last_video_published",
    "videos_last_30d",
    "description",
    "email",
    "whatsapp",
    "phone",
    "website",
    "contact_source",
    "discovered_via",
]


def build_row(channel_id, detail, activity, discovered_via):
    last_published, videos_30d, capped = activity
    snippet = detail.get("snippet", {})
    stats = detail.get("statistics", {})
    contacts = extract_contacts(snippet.get("description", ""))

    subs = "masqué" if stats.get("hiddenSubscriberCount") else stats.get("subscriberCount", "")
    videos_30d_str = f">={videos_30d}" if capped else str(videos_30d)

    return {
        "channel_id": channel_id,
        "title": snippet.get("title", ""),
        "custom_url": snippet.get("customUrl", ""),
        "country": snippet.get("country", ""),
        "subscriber_count": subs,
        "video_count": stats.get("videoCount", ""),
        "view_count": stats.get("viewCount", ""),
        "last_video_published": last_published or "",
        "videos_last_30d": videos_30d_str,
        "description": snippet.get("description", ""),
        "email": "; ".join(contacts["emails"]),
        "whatsapp": "; ".join(contacts["whatsapp"]),
        "phone": "; ".join(contacts["phones"]),
        "website": "; ".join(contacts["websites"]),
        "contact_source": contacts["source"],
        "discovered_via": "; ".join(sorted(discovered_via)),
    }


def write_csv(rows, output_path):
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# ── main ─────────────────────────────────────────────────────────────────────

def parse_lines(text):
    return [l.strip() for l in (text or "").splitlines() if l.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="candidate_channels.csv")
    parser.add_argument("--channels-json", default="channels.json")
    parser.add_argument("--max-results", type=int, default=25)
    parser.add_argument("--quota-limit", type=int, default=QUOTA_BUDGET_LIMIT)
    args = parser.parse_args(argv)

    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        print("ERROR: YOUTUBE_API_KEY not set"); return 1

    handles = parse_lines(os.environ.get("CANDIDATE_HANDLES", ""))
    queries = parse_lines(os.environ.get("CANDIDATE_QUERIES", ""))

    quota = QuotaTracker(limit=args.quota_limit)
    existing_ids = load_existing_channel_ids(args.channels_json)
    print(f"{len(existing_ids)} channel(s) already in {args.channels_json} (will be excluded).")

    discovered_via = {}  # channel_id -> set of sources

    print(f"\n--- Resolving {len(handles)} handle(s) ---")
    for line in handles:
        cid, note = resolve_handle(line, api_key, quota)
        if cid:
            discovered_via.setdefault(cid, set()).add(f"handle: {line}")
            print(f"  OK  {line} -> {cid} ({note})")
        else:
            print(f"  FAIL {line}: {note}")

    print(f"\n--- Discovery over {len(queries)} quer{'y' if len(queries)==1 else 'ies'} ---")
    for q in queries:
        found = discover_channels(q, api_key, quota, args.max_results)
        for cid, title in found.items():
            discovered_via.setdefault(cid, set()).add(f"query: {q}")
        print(f"  '{q}': {len(found)} channel(s) found")

    total_found = len(discovered_via)
    already_integrated = [cid for cid in discovered_via if cid in existing_ids]
    for cid in already_integrated:
        print(f"  SKIP (already in channels.json): {cid} via {sorted(discovered_via[cid])}")
    new_ids = [cid for cid in discovered_via if cid not in existing_ids]
    print(
        f"\n{total_found} unique channel(s) discovered, "
        f"{len(already_integrated)} already integrated (excluded), "
        f"{len(new_ids)} new candidate(s) to detail."
    )

    print("\n--- Fetching channel details ---")
    details = fetch_channel_details(new_ids, api_key, quota)

    print("\n--- Fetching recent-activity signal (uploads playlist) ---")
    rows = []
    for cid in new_ids:
        detail = details.get(cid)
        if not detail:
            print(f"  SKIP {cid}: no details fetched (quota exhausted earlier?)")
            continue
        uploads_id = (
            detail.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        )
        activity = fetch_recent_activity(uploads_id, api_key, quota)
        rows.append(build_row(cid, detail, activity, discovered_via[cid]))

    write_csv(rows, args.output)
    print(f"\nWrote {len(rows)} row(s) to {args.output}")
    print(f"Total quota consumed: {quota.used}/{quota.limit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
