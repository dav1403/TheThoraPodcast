"""Espace rav — core logic of the private per-rav listening statistics.

Everything here is pure (no network): key derivation, encryption, history
merge and per-rav report building. Network fetchers live in stats_sources.py,
the CLI / R2 plumbing in stats_update.py.

Confidentiality model (the repository AND the site are public):
  * one secret, STATS_MASTER_KEY (32 random bytes, base64), lives only in the
    GitHub secret of the same name and in David's local .secrets folder;
  * each rav gets a token = HMAC(master, slug), truncated to 132 bits. The
    private link is  https://thetorahpodcast.net/espace-rav.html#k=<token>
    — the token sits in the URL FRAGMENT, which browsers never send to the
    server nor put in the Referer header;
  * the rav's report is AES-256-GCM encrypted with a key derived from the
    token and published as  espace-rav/d/<id>.bin  where <id> is a hash of
    the token: the file name reveals neither the rav nor the key, and the
    ciphertext is useless without the link;
  * the raw collected history (per day, per episode) is also encrypted,
    with a key derived from the master key, before it is stored in R2 (the
    bucket is publicly readable through r2.dev).
Nothing in clear — no token, no count — is ever committed or printed in the
(public) Actions logs.

Only the `cryptography` package is needed on top of the stdlib, imported
lazily so the pure aggregation helpers stay importable without it.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import json
import os
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

FORMAT_VERSION = 1
SITE_URL = "https://thetorahpodcast.net"
PAGE_PATH = "espace-rav.html"
BLOB_DIR = "espace-rav/d"          # path of the encrypted reports on the site
TOKEN_CHARS = 22                   # 22 base64url chars = 132 bits

_NS = b"ttp-espace-rav/v1/"

# GA4 events sent by the site player (generate_channel_pages.py, render_page).
GA4_EVENTS = ("audio_play", "audio_complete")


# --------------------------------------------------------------------------
# Keys and encryption
# --------------------------------------------------------------------------

def load_master_key(value: str | None) -> bytes:
    """Decode STATS_MASTER_KEY (base64 / base64url of >= 32 bytes)."""
    if not value or not value.strip():
        raise ValueError("STATS_MASTER_KEY is empty")
    v = value.strip()
    raw = base64.urlsafe_b64decode(v.replace("+", "-").replace("/", "_") + "=" * (-len(v) % 4))
    if len(raw) < 32:
        raise ValueError("STATS_MASTER_KEY must decode to at least 32 bytes")
    return raw


def new_master_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


def rav_token(master: bytes, slug: str) -> str:
    """Stable private token of a rav (the secret part of his link)."""
    mac = hmac.new(master, _NS + b"token/" + slug.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")[:TOKEN_CHARS]


def blob_id(token: str) -> str:
    """Public file name of the rav's encrypted report (derived from the token)."""
    return hashlib.sha256(_NS + b"id/" + token.encode("ascii")).hexdigest()[:32]


def blob_key(token: str) -> bytes:
    """AES-256 key of the rav's report (derived from the token)."""
    return hashlib.sha256(_NS + b"key/" + token.encode("ascii")).digest()


def history_key(master: bytes) -> bytes:
    return hmac.new(master, _NS + b"history", hashlib.sha256).digest()


def rav_link(token: str) -> str:
    return f"{SITE_URL}/{PAGE_PATH}#k={token}"


def encrypt(key: bytes, plaintext: bytes) -> bytes:
    """nonce(12) || AES-GCM(ciphertext + tag). Same layout as espace-rav.html."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, None)


def decrypt(key: bytes, blob: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(blob) < 12 + 16:
        raise ValueError("encrypted blob too short")
    return AESGCM(key).decrypt(blob[:12], blob[12:], None)


def seal_report(token: str, report: dict) -> bytes:
    data = json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return encrypt(blob_key(token), data)


def open_report(token: str, blob: bytes) -> dict:
    return json.loads(decrypt(blob_key(token), blob).decode("utf-8"))


def seal_history(master: bytes, history: dict) -> bytes:
    data = gzip.compress(json.dumps(history, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8"), mtime=0)
    return encrypt(history_key(master), data)


def open_history(master: bytes, blob: bytes) -> dict:
    return json.loads(gzip.decompress(decrypt(history_key(master), blob)).decode("utf-8"))


# --------------------------------------------------------------------------
# History (raw collected counts)
# --------------------------------------------------------------------------
#
# {
#   "v": 1,
#   "daily": {
#     "direct":   {"YYYY-MM-DD": {"<video_id>": requests}},        # Cloudflare R2
#     "site_page":{"YYYY-MM-DD": {"<event>|<rav url slug>": n}},  # GA4, pagePath
#     "site_ep":  {"YYYY-MM-DD": {"<event>|<rav name>|<title>": n}}  # GA4 custom dims
#   },
#   "platforms": [{"slug","platform","start","end","plays","noted"}],  # manual
#   "sources": {"<source>": {"first": day, "last": day, "updated": iso}}
# }

DAILY_SOURCES = ("direct", "site_page", "site_ep")


def empty_history() -> dict:
    return {"v": FORMAT_VERSION, "daily": {s: {} for s in DAILY_SOURCES},
            "platforms": [], "sources": {}}


def merge_daily(history: dict, source: str, days: dict[str, dict[str, int]],
                now: datetime | None = None) -> int:
    """Replace the given days of one source (re-fetching a day is idempotent).

    Returns the number of days written. A day absent from `days` is left as
    is, so a source with a short retention (R2: 31 days) accumulates beyond it.
    """
    if source not in DAILY_SOURCES:
        raise ValueError(f"unknown source {source!r}")
    store = history.setdefault("daily", {}).setdefault(source, {})
    for day, counts in days.items():
        date.fromisoformat(day)  # validates
        store[day] = {k: int(v) for k, v in counts.items() if int(v) > 0}
    if days:
        meta = history.setdefault("sources", {}).setdefault(source, {})
        all_days = sorted(store)
        meta["first"] = all_days[0]
        meta["last"] = all_days[-1]
        meta["updated"] = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return len(days)


def add_platform_rows(history: dict, rows: list[dict]) -> int:
    """Upsert manual platform figures (Spotify / Apple / Deezer consoles).

    Key = (slug, platform, start, end); a re-import of the same period
    replaces the previous figure instead of adding to it.
    """
    table = history.setdefault("platforms", [])
    index = {(r["slug"], r["platform"], r["start"], r["end"]): i for i, r in enumerate(table)}
    n = 0
    for r in rows:
        row = {
            "slug": str(r["slug"]).strip(),
            "platform": str(r["platform"]).strip().lower(),
            "start": date.fromisoformat(str(r["start"]).strip()).isoformat(),
            "end": date.fromisoformat(str(r["end"]).strip()).isoformat(),
            "plays": int(r["plays"]),
            "noted": str(r.get("noted") or date.today().isoformat()),
        }
        key = (row["slug"], row["platform"], row["start"], row["end"])
        if key in index:
            table[index[key]] = row
        else:
            index[key] = len(table)
            table.append(row)
        n += 1
    return n


# --------------------------------------------------------------------------
# Catalogue: who is a rav, which episodes are his
# --------------------------------------------------------------------------

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def video_id_from_object(name: str) -> str | None:
    """R2 object key -> YouTube video id (keys are '<video_id>_<title>.mp3')."""
    base = name.rsplit("/", 1)[-1]
    if not base.lower().endswith(".mp3") or len(base) < 12:
        return None
    vid = base[:11]
    if not VIDEO_ID_RE.match(vid) or (len(base) > 15 and base[11] not in "_."):
        return None
    return vid


def norm_title(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").replace("’", "'")
    return re.sub(r"\s+", " ", s).strip().lower()


def page_slug(path: str) -> str | None:
    """GA4 pagePath -> lowercase url slug of the rav page ('/lev.html' -> 'lev')."""
    p = (path or "").split("?", 1)[0].split("#", 1)[0].strip("/")
    if not p:
        return None
    first = p.split("/", 1)[0]
    if first.endswith(".html"):
        first = first[:-5]
    return first.lower() or None


def build_catalogue(channels: list[dict], speakers: list[dict],
                    entries: dict[str, list[dict]]) -> dict:
    """ravs: slug -> {name, lang, kind, episodes: {video_id: ep}}.

    `entries` maps every slug (channels AND guests) to its entries.json list;
    a guest's entries.json already holds the host episodes matched to him.
    """
    ravs: dict[str, dict] = {}
    for ch in channels:
        if not ch.get("enabled", True):
            continue
        slug = ch["slug"]
        ravs[slug] = {"slug": slug, "name": ch.get("podcast_author") or slug,
                      "lang": ch.get("podcast_language", "fr"), "kind": "channel",
                      "external": bool(ch.get("external_feed") or ch.get("rss_url"))}
    for sp in speakers:
        ravs[sp["slug"]] = {"slug": sp["slug"], "name": sp.get("name") or sp["slug"],
                            "lang": sp.get("language", "fr"), "kind": "guest",
                            "external": False}
    for slug, r in ravs.items():
        eps = {}
        for ep in entries.get(slug) or []:
            vid = ep.get("video_id")
            if vid and ep.get("title"):
                eps[vid] = {"title": ep["title"], "published": (ep.get("published") or "")[:10]}
        r["episodes"] = eps
    return ravs


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def _iso_week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _days_between(history: dict, source: str, start: date | None, end: date) -> dict:
    out = {}
    for day, counts in (history.get("daily", {}).get(source) or {}).items():
        d = date.fromisoformat(day)
        if d <= end and (start is None or d >= start):
            out[day] = counts
    return out


def build_report(rav: dict, catalogue: dict, history: dict, today: date,
                 weeks: int = 26, top: int = 50) -> dict:
    """Per-rav report (the JSON encrypted into espace-rav/d/<id>.bin).

    Site plays per course come from the GA4 custom dimensions (rav name +
    episode title); for days where those are not available the rav total
    falls back on the plays counted on his page (pagePath), without a per
    course split. Direct downloads = R2 GetObject requests on the episode
    files (includes Apple Podcasts and podcast apps AND the site player
    itself: never add them to the site plays).
    """
    slug = rav["slug"]
    my_eps: dict = rav["episodes"]
    end = today - timedelta(days=1)                       # last complete day
    first_week = _iso_week_start(end) - timedelta(weeks=weeks - 1)
    d30 = end - timedelta(days=29)

    name_to_slugs: dict[str, list[str]] = {}
    for r in catalogue.values():
        name_to_slugs.setdefault(norm_title(r["name"]), []).append(r["slug"])
    # title -> video ids, per rav, to resolve (rav name, title) GA4 rows
    title_index: dict[str, dict[str, str]] = {
        s: {norm_title(e["title"]): v for v, e in r["episodes"].items()}
        for s, r in catalogue.items()
    }
    my_url_slug = slug.lower()

    per_ep = {v: {"site": 0, "complete": 0, "direct": 0} for v in my_eps}
    weekly: dict[str, dict[str, int]] = {}
    totals = {"all": {"site": 0, "complete": 0, "direct": 0, "site_unassigned": 0},
              "d30": {"site": 0, "complete": 0, "direct": 0, "site_unassigned": 0}}

    def bump(day: str, field: str, n: int):
        d = date.fromisoformat(day)
        buckets = ["all"] + (["d30"] if d >= d30 else [])
        for b in buckets:
            totals[b][field] += n
        if d >= first_week:
            wk = _iso_week_start(d).isoformat()
            w = weekly.setdefault(wk, {"site": 0, "direct": 0})
            if field in w:
                w[field] += n

    # Direct downloads (R2)
    for day, counts in _days_between(history, "direct", None, end).items():
        for vid, n in counts.items():
            if vid in per_ep:
                per_ep[vid]["direct"] += n
                bump(day, "direct", n)

    # Site plays: custom dimensions when present that day, else page path
    # A day without any custom-dimension row (dimensions declared later in
    # GA4 only apply from then on) falls back on the page totals.
    ep_days = {d: c for d, c in _days_between(history, "site_ep", None, end).items() if c}
    for day, counts in ep_days.items():
        for key, n in counts.items():
            event, rav_name, title = (key.split("|", 2) + ["", ""])[:3]
            field = "site" if event == "audio_play" else "complete" if event == "audio_complete" else None
            if not field:
                continue
            host_slugs = name_to_slugs.get(norm_title(rav_name), [])
            vid = None
            for hs in host_slugs:
                vid = title_index.get(hs, {}).get(norm_title(title))
                if vid:
                    break
            if vid and vid in per_ep:
                per_ep[vid][field] += n
                bump(day, field, n)
            elif not vid and slug in host_slugs:
                # played on his own page but the title did not resolve
                if field == "site":
                    bump(day, "site", n)
                    for b in ("all", "d30") if date.fromisoformat(day) >= d30 else ("all",):
                        totals[b]["site_unassigned"] += n
                else:
                    bump(day, "complete", n)
    for day, counts in _days_between(history, "site_page", None, end).items():
        if day in ep_days:
            continue
        for key, n in counts.items():
            event, page = (key.split("|", 1) + [""])[:2]
            if page != my_url_slug:
                continue
            if event == "audio_play":
                bump(day, "site", n)
                for b in ("all", "d30") if date.fromisoformat(day) >= d30 else ("all",):
                    totals[b]["site_unassigned"] += n
            elif event == "audio_complete":
                bump(day, "complete", n)

    weeks_out = []
    wk = first_week
    while wk <= end:
        w = weekly.get(wk.isoformat(), {"site": 0, "direct": 0})
        weeks_out.append({"start": wk.isoformat(), "site": w["site"], "direct": w["direct"]})
        wk += timedelta(weeks=1)

    courses = []
    for vid, c in per_ep.items():
        tot = c["site"] + c["direct"]
        if tot <= 0:
            continue
        ep = my_eps[vid]
        courses.append({"title": ep["title"], "published": ep["published"], "vid": vid,
                        "site": c["site"], "complete": c["complete"], "direct": c["direct"]})
    courses.sort(key=lambda c: (-(c["site"] + c["direct"]), c["title"]))

    platforms = [
        {k: p[k] for k in ("platform", "start", "end", "plays", "noted")}
        for p in history.get("platforms", []) if p.get("slug") == slug
    ]
    platforms.sort(key=lambda p: (p["end"], p["platform"]), reverse=True)

    src = history.get("sources", {})

    def src_meta(key):
        m = src.get(key) or {}
        return {"active": bool(m), "since": m.get("first"), "last": m.get("last")}

    site_meta = src_meta("site_ep") if src.get("site_ep") else src_meta("site_page")
    if src.get("site_ep") and src.get("site_page"):
        site_meta["since"] = min(src["site_ep"]["first"], src["site_page"]["first"])
    return {
        "v": FORMAT_VERSION,
        "slug": slug,
        "name": rav["name"],
        "lang": rav.get("lang", "fr"),
        "kind": rav.get("kind", "channel"),
        "generated": today.isoformat(),
        "until": end.isoformat(),
        "episodes": len(my_eps),
        "sources": {"site": site_meta, "direct": src_meta("direct"),
                    "platforms": {"active": bool(platforms)}},
        "totals": totals,
        "weekly": weeks_out,
        "courses": courses[:top],
        "courses_count": len(courses),
        "platforms": platforms,
    }


# --------------------------------------------------------------------------
# Dashboard (David's private global view: audience of every rav + pipeline)
# --------------------------------------------------------------------------
#
# Same confidentiality model as the rav reports: one token derived from the
# master key (in a namespace no rav slug can reach), the report sealed with
# it and published next to the rav reports (espace-rav/d/<id>.bin), opened
# by tableau-de-bord.html with the token carried in the link's fragment.

DASHBOARD_PAGE = "tableau-de-bord.html"


def dashboard_token(master: bytes) -> str:
    mac = hmac.new(master, _NS + b"dashboard", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")[:TOKEN_CHARS]


def dashboard_link(token: str) -> str:
    return f"{SITE_URL}/{DASHBOARD_PAGE}#k={token}"


def _site_events(counts: dict) -> tuple[int, int]:
    """site_page day -> (plays, completes), every page of the site."""
    plays = completes = 0
    for key, n in counts.items():
        event = key.split("|", 1)[0]
        if event == "audio_play":
            plays += n
        elif event == "audio_complete":
            completes += n
    return plays, completes


def build_dashboard(catalogue: dict, history: dict, reports: dict[str, dict], today: date,
                    health: dict | None = None, days: int = 90, top: int = 25,
                    stale_days: int = 30) -> dict:
    """Global report: every rav at once, plus the pipeline health snapshot.

    Global totals come from the history itself, never from adding up the
    per-rav reports: a guest's course is also his host's course, so the sum
    of the reports counts it twice. Site plays = every audio_play of the
    site (all pages, GA4 pagePath); direct = every R2 GetObject on an mp3.
    `reports` are the per-rav reports built in the same run (build_report).
    """
    end = today - timedelta(days=1)
    first = end - timedelta(days=days - 1)
    known = {v for r in catalogue.values() for v in r["episodes"]}
    zero = {"site": 0, "complete": 0, "direct": 0}

    per_day: dict[str, dict[str, int]] = {}
    unmatched = 0
    for day, counts in _days_between(history, "direct", None, end).items():
        row = per_day.setdefault(day, dict(zero))
        for vid, n in counts.items():
            row["direct"] += n
            if vid not in known:
                unmatched += n
    for day, counts in _days_between(history, "site_page", None, end).items():
        plays, completes = _site_events(counts)
        row = per_day.setdefault(day, dict(zero))
        row["site"] += plays
        row["complete"] += completes

    def window(start: date | None, stop: date) -> dict:
        out = dict(zero)
        for day, row in per_day.items():
            d = date.fromisoformat(day)
            if d <= stop and (start is None or d >= start):
                for k in out:
                    out[k] += row[k]
        return out

    totals = {
        "d7": window(end - timedelta(days=6), end),
        "d30": window(end - timedelta(days=29), end),
        "prev30": window(end - timedelta(days=59), end - timedelta(days=30)),
        "all": window(None, end),
    }
    series = []
    d = first
    while d <= end:
        series.append({"day": d.isoformat(), **per_day.get(d.isoformat(), zero)})
        d += timedelta(days=1)

    # Per rav: taken from the reports, which credit guests correctly
    ravs = []
    for slug, rav in catalogue.items():
        tot = (reports.get(slug) or {}).get("totals") or {}
        last = max((e["published"] for e in rav["episodes"].values() if e.get("published")), default="")
        ravs.append({
            "slug": slug, "name": rav["name"], "kind": rav.get("kind", "channel"),
            "lang": rav.get("lang", "fr"), "episodes": len(rav["episodes"]), "last": last,
            "d30": {k: (tot.get("d30") or {}).get(k, 0) for k in ("site", "direct")},
            "all": {k: (tot.get("all") or {}).get(k, 0) for k in ("site", "direct")},
        })
    ravs.sort(key=lambda r: (-(r["d30"]["site"] + r["d30"]["direct"]),
                             -(r["all"]["site"] + r["all"]["direct"]), r["name"]))

    # By language: channels only (a guest's courses already sit in a channel)
    by_lang: dict[str, dict[str, int]] = {}
    for r in ravs:
        if r["kind"] != "channel":
            continue
        b = by_lang.setdefault(r["lang"], {"site": 0, "direct": 0, "ravs": 0, "episodes": 0})
        b["site"] += r["d30"]["site"]
        b["direct"] += r["d30"]["direct"]
        b["ravs"] += 1
        b["episodes"] += r["episodes"]

    # Top courses: the global top-k always lies within the union of the
    # per-rav top-k (same ranking key), so the reports' lists are enough.
    courses: dict[str, dict] = {}
    for slug, rep in reports.items():
        rav = catalogue.get(slug) or {}
        for c in rep.get("courses") or []:
            cur = courses.get(c["vid"])
            if cur is None or (cur["_kind"] == "guest" and rav.get("kind") == "channel"):
                courses[c["vid"]] = {**c, "rav": rav.get("name", slug), "_kind": rav.get("kind")}
    top_courses = sorted(courses.values(), key=lambda c: (-(c["site"] + c["direct"]), c["title"]))[:top]
    top_courses = [{k: v for k, v in c.items() if k != "_kind"} for c in top_courses]

    # Catalogue: unique episodes, new ones per ISO week, quiet channels
    published = {v: e["published"] for r in catalogue.values()
                 for v, e in r["episodes"].items() if e.get("published")}
    wk0 = _iso_week_start(end) - timedelta(weeks=11)
    new_weekly = {(wk0 + timedelta(weeks=i)).isoformat(): 0 for i in range(12)}
    pub_days = []
    for p in published.values():
        try:
            pub_days.append(date.fromisoformat(p))
        except ValueError:
            continue
    for pd in pub_days:
        if wk0 <= pd <= end:
            new_weekly[_iso_week_start(pd).isoformat()] += 1
    stale_limit = (end - timedelta(days=stale_days)).isoformat()
    quiet = sorted(({"slug": r["slug"], "name": r["name"], "last": r["last"]}
                    for r in ravs if r["kind"] == "channel" and r["last"] and r["last"] < stale_limit),
                   key=lambda r: r["last"])

    platforms = history.get("platforms", [])
    src = history.get("sources", {})
    return {
        "v": FORMAT_VERSION,
        "kind": "dashboard",
        "generated": today.isoformat(),
        "until": end.isoformat(),
        "sources": {k: {"active": bool(src.get(k)), "since": (src.get(k) or {}).get("first"),
                        "last": (src.get(k) or {}).get("last")} for k in DAILY_SOURCES},
        "totals": totals,
        "direct_unmatched": unmatched,
        "series": series,
        "ravs": ravs,
        "by_lang": by_lang,
        "courses": top_courses,
        "catalogue": {
            "episodes": len(known),
            "channels": sum(1 for r in catalogue.values() if r.get("kind") == "channel"),
            "guests": sum(1 for r in catalogue.values() if r.get("kind") == "guest"),
            "new_weekly": [{"start": k, "n": v} for k, v in new_weekly.items()],
            "new_d7": sum(1 for pd in pub_days if end - timedelta(days=6) <= pd <= end),
            "new_d30": sum(1 for pd in pub_days if end - timedelta(days=29) <= pd <= end),
            "quiet": quiet,
        },
        "platforms": {"rows": len(platforms), "plays": sum(int(p.get("plays", 0)) for p in platforms),
                      "last_noted": max((p.get("noted", "") for p in platforms), default="") or None},
        "health": health or {"available": False},
    }
