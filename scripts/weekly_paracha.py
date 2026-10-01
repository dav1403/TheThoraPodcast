"""
weekly_paracha.py
-----------------
"Rendez-vous hebdo paracha": every Thursday, a selection of the best classes of
the catalogue on the reading of the coming Shabbat (weekly parasha, double
parasha, or festival), with a short AI summary, published as source pages:

  paracha/<week>.json          data of the week (selection + summary)
  paracha/<week>.html          archive page of the week
  paracha-de-la-semaine.html   rolling page (latest week + archive list)
  paracha-de-la-semaine.xml    RSS feed of the weekly selections

The reading resolution (Hebcal, double parashiot, festival Shabbatot, Vezot
Habracha, typographic apostrophes) is NOT re-implemented here: it is the one of
social_post.py (commit 35bee1bc6c), imported as is.

Usage:
  python scripts/weekly_paracha.py generate [--date YYYY-MM-DD] [--force] [--no-ai]
  python scripts/weekly_paracha.py render          # re-render pages from the JSON files
  python scripts/weekly_paracha.py telegram [--wait-live]   # no-op without secrets

Exit codes: 0 ok / skipped (already generated this week), 1 failure (reading
unresolvable, no class at all, write error). Never a silent skip.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).parent))

import requests  # noqa: E402

import social_post as sp  # noqa: E402  (reading resolution + Hebcal + matching)
import weekly_paracha_pages as pages  # noqa: E402
from generate_channel_pages import ep_path, transcript_extract, url_slug  # noqa: E402
from lang_detect import episode_lang  # noqa: E402
from llm_util import first_text  # noqa: E402  (content[0] may be a ThinkingBlock)

try:
    import anthropic as _anthropic
except ImportError:  # pragma: no cover - CI installs it
    _anthropic = None

SITE_URL = sp.SITE_URL
FEEDS_DIR = Path("feeds")
DATA_DIR = pages.DATA_DIR
CHANNELS_FILE = Path("channels.json")
SPEAKERS_FILE = Path("speakers.json")

SUMMARY_MODEL = "claude-haiku-4-5-20251001"
MIN_COURSES = 8
MAX_COURSES = 12
MAX_PER_RAV = 2
TRANSCRIPT_PROBE = 60          # candidates whose transcript is looked up
MIN_TRANSCRIPT_BYTES = 400     # same threshold as the sitemap (generate_channel_pages)
MIN_DURATION_SECS = 240        # shorter = clip / jingle, not a class
ISRAEL_TZ = timezone(timedelta(hours=3))  # only used to pick "today" in CI
_HITAT_RE = re.compile(r"HITAT DU JOUR", re.IGNORECASE)
_PARACHA_WORD_RE = re.compile(r"\b(paracha|parasha|parachat|parashat|paracha:)\b|פרשת", re.IGNORECASE)

# Labels of festival readings that social_post maps onto a parasha.
READING_LABELS = {
    "holiday:shmini-atzeret": ("Chemini Atseret – Simha Torah : Vézot Habracha",
                               "שמיני עצרת – שמחת תורה · וזאת הברכה"),
}

# Classes of the festivals falling in the week (Hoshana Rabba, Chol Hamoed...)
# complete the selection, below the reading itself.
FESTIVAL_WEIGHT = 0.6


# --------------------------------------------------------------------------
# Reading of the week
# --------------------------------------------------------------------------

def week_festivals(payload: dict, start: date, shabbat: date, reading: dict) -> list[str]:
    """HOLIDAYS keys of the festivals falling between start and Shabbat, other
    than the reading itself (e.g. Hoshana Rabba -> 'sukkot' the week of
    Shmini Atzeret)."""
    keys: list[str] = []
    own = {s.split(":", 1)[1] for s in [reading.get("source", "")] if s.startswith("holiday:")}
    own |= set(reading.get("slugs", []))
    for item in payload.get("items", []):
        if item.get("category") != "holiday":
            continue
        day = str(item.get("date", ""))[:10]
        if not (start.isoformat() <= day <= shabbat.isoformat()):
            continue
        key = sp.holiday_key_from_title(item.get("title", ""))
        if not key or key in own or key in keys:
            continue
        if sp.HOLIDAYS[key].get("parasha"):
            continue  # Shmini Atzeret is a reading, not an extra festival
        keys.append(key)
    return keys


def resolve_week(ref: date, fetch: Callable | None = None) -> dict:
    """The reading + festivals of the week of `ref` (a Thursday in production).

    Raises sp.ReadingError (or requests errors) — the caller fails loudly."""
    fetch = fetch or sp.fetch_hebcal_shabbat
    shabbat = sp.upcoming_shabbat(ref)
    reading = sp.resolve_weekly_reading(ref, fetch)
    payload = fetch(shabbat)
    festivals = week_festivals(payload, ref, shabbat, reading)
    return {"shabbat": shabbat, "reading": reading, "festivals": festivals}


def reading_labels(reading: dict) -> tuple[str, str]:
    if reading.get("source") in READING_LABELS:
        return READING_LABELS[reading["source"]]
    if reading["kind"] == "parasha":
        return f"Paracha {reading['fr']}", reading["he"]
    return reading["fr"], reading["he"]


def week_slug(reading: dict, shabbat: date, data_dir: Path = DATA_DIR) -> str:
    """`<slugs>-<year>`, e.g. 'nitzavim-vayelech-2026'. If that name is already
    taken by ANOTHER Shabbat (a parasha can come back twice in one civil year),
    the month is appended."""
    base = f"{'-'.join(reading['slugs'])}-{shabbat.year}"
    f = data_dir / f"{base}.json"
    if f.exists():
        try:
            other = json.loads(f.read_text(encoding="utf-8")).get("shabbat")
        except (OSError, ValueError):
            other = None
        if other and other != shabbat.isoformat():
            return f"{base}-{shabbat.month:02d}"
    return base


# --------------------------------------------------------------------------
# Title matching (accent-folded on top of social_post.match_parasha)
# --------------------------------------------------------------------------

def fold(text: str) -> str:
    """Lowercase, accents and typographic apostrophes removed, Hebrew niqqud
    dropped: 'Bérechit' and 'berechit' match the same keyword."""
    nfd = unicodedata.normalize("NFD", text or "")
    out = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
    return out.replace("’", "'").replace("`", "'").lower()


def title_matches(title: str, keywords: list[str]) -> bool:
    return sp.match_parasha(fold(title), [fold(k) for k in keywords])


def _other_parasha_kws(slugs: list[str]) -> list[str]:
    """Keywords of every OTHER parasha, minus the ambiguous very short ones
    ('bo', 'נח'...) that would flag ordinary words."""
    own = {fold(k) for s in slugs for k in sp.PARASHIOT[s]["kw"]}
    kws = []
    for slug, p in sp.PARASHIOT.items():
        if slug in slugs:
            continue
        kws += [k for k in p["kw"] if len(fold(k)) >= 4 and fold(k) not in own]
    return kws


OFF_TOPIC_FACTOR = 0.3  # the title is mainly about another parasha


# --------------------------------------------------------------------------
# Catalogue + selection
# --------------------------------------------------------------------------

def load_catalogue() -> tuple[list[dict], list[dict]]:
    channels = [c for c in json.loads(CHANNELS_FILE.read_text(encoding="utf-8-sig"))
                if c.get("enabled", True)]
    speakers = (json.loads(SPEAKERS_FILE.read_text(encoding="utf-8-sig"))
                if SPEAKERS_FILE.exists() else [])
    return channels, speakers


def load_entries(slug: str) -> list[dict]:
    f = FEEDS_DIR / f"{slug}.entries.json"
    if not f.exists():
        return []
    return json.loads(f.read_text(encoding="utf-8-sig"))


def rav_of(ep: dict, ch: dict, speakers: list[dict]) -> tuple[str, str]:
    """(display name, page slug): a guest speaker when the title names him on
    one of his host channels, else the channel's rav."""
    t = (ep.get("title") or "").lower()
    for s in speakers:
        if ch["slug"] in s.get("from_channels", []) and any(p.lower() in t for p in s.get("title_patterns", [])):
            return s["name"], s["slug"]
    return ch["podcast_author"], ch["slug"]


def _age_days(published: str, ref: date) -> float:
    try:
        d = datetime.fromisoformat(published[:10]).date()
    except ValueError:
        return 3650.0
    return max(0.0, (ref - d).days)


def base_score(ep: dict, ref: date, relevance: float) -> float:
    """Relevance x (recency + a few quality hints). Transcript is added later."""
    age_years = _age_days(ep.get("published", ""), ref) / 365.0
    recency = max(0.0, 1.0 - age_years / 6.0)            # 1.0 today -> 0 after 6 years
    dur = int(ep.get("duration_secs") or 0)
    duration = 1.0 if 600 <= dur <= 4800 else (0.4 if dur else 0.2)
    explicit = 0.5 if _PARACHA_WORD_RE.search(ep.get("title", "")) else 0.0
    return relevance * (3.0 * recency + duration + explicit)


def collect_candidates(week: dict, channels: list[dict], speakers: list[dict], ref: date,
                       entries_loader: Callable[[str], list[dict]] = load_entries) -> list[dict]:
    """Every class whose TITLE matches the reading (weight 1) or a festival of
    the week (weight FESTIVAL_WEIGHT). For a festival reading without any title
    hit, the AI theme tag is the fallback (same rule as social_post)."""
    reading = week["reading"]
    kw_sets = [(reading["kw"], 1.0, "reading")]
    for key in week["festivals"]:
        kw_sets.append((sp.HOLIDAYS[key]["kw"], FESTIVAL_WEIGHT, f"festival:{key}"))

    others = _other_parasha_kws(reading["slugs"]) if reading["kind"] == "parasha" else []
    out: dict[str, dict] = {}
    by_channel = {ch["slug"]: entries_loader(ch["slug"]) for ch in channels}

    def _add(ch, ep, weight, why):
        vid = ep.get("video_id")
        if not vid or not ep.get("title") or not ep.get("published"):
            return
        if _HITAT_RE.search(ep["title"]):
            return
        dur = int(ep.get("duration_secs") or 0)
        if 0 < dur < MIN_DURATION_SECS:
            return
        if others and why == "reading" and title_matches(ep["title"], others):
            weight *= OFF_TOPIC_FACTOR
        if vid in out and out[vid]["weight"] >= weight:
            return
        rav, rav_slug = rav_of(ep, ch, speakers)
        out[vid] = {"ep": ep, "ch": ch, "rav": rav, "rav_slug": rav_slug, "weight": weight,
                    "match": why, "score": base_score(ep, ref, weight)}

    for kws, weight, why in kw_sets:
        for ch in channels:
            for ep in by_channel[ch["slug"]]:
                if title_matches(ep.get("title", ""), kws):
                    _add(ch, ep, weight, why)
    theme = reading.get("theme")
    if theme and not any(c["match"] == "reading" for c in out.values()):
        for ch in channels:
            for ep in by_channel[ch["slug"]]:
                if theme in (ep.get("tags") or []):
                    _add(ch, ep, 0.8, f"theme:{theme}")
    return sorted(out.values(), key=lambda c: (-c["score"], c["ep"]["video_id"]))


def pick(candidates: list[dict], n_max: int = MAX_COURSES, per_rav: int = MAX_PER_RAV) -> list[dict]:
    """Diversity first: the best class of each rav, rav after rav by score;
    then second classes, never more than `per_rav` per rav. Readings come
    before festival fillers within each round."""
    ordered = sorted(candidates, key=lambda c: (c["match"] != "reading", -c["score"], c["ep"]["video_id"]))
    chosen: list[dict] = []
    for round_ in range(per_rav):
        seen: dict[str, int] = {}
        for c in chosen:
            seen[c["rav"]] = seen.get(c["rav"], 0) + 1
        for c in ordered:
            if len(chosen) >= n_max:
                break
            if c in chosen or seen.get(c["rav"], 0) > round_:
                continue
            chosen.append(c)
            seen[c["rav"]] = seen.get(c["rav"], 0) + 1
    return chosen


# --------------------------------------------------------------------------
# Transcripts (repo checkout first, then the live site)
# --------------------------------------------------------------------------

def fetch_transcript(video_id: str, session: requests.Session | None = None) -> str:
    """Transcript text, '' when none. The weekly workflow checks out entries
    only (34 000 transcripts = 600 MB), so it reads the few it needs from the
    published site, where feeds/transcripts/ is served from the R2 state."""
    local = FEEDS_DIR / "transcripts" / f"{video_id}.txt"
    if local.is_file():
        return local.read_text(encoding="utf-8", errors="replace")
    try:
        r = (session or requests).get(f"{SITE_URL}/feeds/transcripts/{video_id}.txt", timeout=15)
    except requests.RequestException:
        return ""
    if r.status_code != 200:
        return ""
    r.encoding = "utf-8"
    return r.text


def enrich_with_transcripts(candidates: list[dict], fetch_tx: Callable[[str], str],
                            probe: int = TRANSCRIPT_PROBE) -> None:
    for c in candidates[:probe]:
        text = fetch_tx(c["ep"]["video_id"]) or ""
        has = len(text.encode("utf-8")) >= MIN_TRANSCRIPT_BYTES
        c["has_transcript"] = has
        c["extract"] = transcript_extract(text, 110) if has else ""
        if has:
            c["score"] += 1.5
    for c in candidates[probe:]:
        c.setdefault("has_transcript", False)
        c.setdefault("extract", "")
    candidates.sort(key=lambda c: (-c["score"], c["ep"]["video_id"]))


# --------------------------------------------------------------------------
# AI summary (grounded on the selected classes only)
# --------------------------------------------------------------------------

def _clean_desc(desc: str, limit: int = 300) -> str:
    d = re.sub(r"https?://\S+", "", desc or "")
    d = " ".join(d.split())
    return d[:limit]


def build_prompt(label_fr: str, courses: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(courses, 1):
        lines = [f"[{i}] {c['rav']} — « {c['ep']['title']} »"]
        desc = _clean_desc(c["ep"].get("description", ""))
        if desc:
            lines.append(f"Description : {desc}")
        if c.get("extract"):
            lines.append(f"Début de la transcription : {c['extract']}")
        blocks.append("\n".join(lines))
    material = "\n\n".join(blocks)
    return (
        "Tu rédiges le chapeau d'une page « Les cours de la semaine » d'un site de cours de Torah "
        f"en podcast. Sujet de la semaine : {label_fr}.\n\n"
        "Voici les cours sélectionnés (titre, description, début de transcription quand il existe) :\n\n"
        f"{material}\n\n"
        "Consignes STRICTES :\n"
        "- Écris en français 3 à 5 phrases courtes (25 mots maximum chacune), UNE PAR LIGNE, "
        "700 caractères au total au maximum. Pas de puces, pas de titre.\n"
        "- Appuie-toi UNIQUEMENT sur le matériel ci-dessus : n'invente aucun enseignement, "
        "aucune citation, aucun chiffre, aucun nom qui n'y figure pas. Si le matériel est maigre, "
        "reste général et factuel (quels rabbins, quels angles d'après les titres).\n"
        "- Tu peux nommer les rabbins et les thèmes qui ressortent des titres et extraits.\n"
        "- Ton sobre et informatif (pas de « découvrez », pas de superlatifs), pas d'emoji, pas de hashtag, "
        "pas d'URL, pas d'appel à s'abonner.\n"
        "Réponds uniquement par le texte des lignes."
    )


SUMMARY_MAX_CHARS = 900
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÀ-ÖØ-Þ«\"'])")


def normalize_summary(text: str) -> str:
    """One sentence per line, bullets stripped, cut on whole sentences within
    SUMMARY_MAX_CHARS. Models often return a single paragraph."""
    lines = [ln.strip().lstrip("-•* ").strip() for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        lines = [s.strip() for s in _SENTENCE_SPLIT.split(" ".join(lines)) if s.strip()]
    out, used = [], 0
    for ln in lines[:5]:
        if out and used + len(ln) > SUMMARY_MAX_CHARS:
            break
        out.append(ln)
        used += len(ln) + 1
    return "\n".join(out)


def valid_summary(text: str) -> bool:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return 2 <= len(lines) <= 6 and 80 <= len(text) <= SUMMARY_MAX_CHARS + 200 and "http" not in text


def fallback_summary(label_fr: str, courses: list[dict]) -> str:
    """Deterministic text built from the data only (no API / credits)."""
    ravs = []
    for c in courses:
        if c["rav"] not in ravs:
            ravs.append(c["rav"])
    shown = ", ".join(ravs[:4]) + (f" et {len(ravs) - 4} autres" if len(ravs) > 4 else "")
    titles = " ; ".join(f"« {c['ep']['title'].strip()} »" for c in courses[:2])
    return (
        f"{len(courses)} cours sélectionnés cette semaine sur {label_fr}, par {shown}.\n"
        f"Parmi eux : {titles}.\n"
        "Chaque cours s'écoute directement sur la page, sans application ni abonnement."
    )


def generate_summary(label_fr: str, courses: list[dict], api_key: str | None) -> tuple[str, str]:
    """(summary, source). source = model id, or 'fallback'."""
    if not api_key or not _anthropic:
        print("  [summary] no ANTHROPIC_API_KEY / anthropic lib — deterministic fallback")
        return fallback_summary(label_fr, courses), "fallback"
    client = _anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=SUMMARY_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": build_prompt(label_fr, courses)}],
        )
    except Exception as exc:  # noqa: BLE001
        if sp._is_credit_error(exc):
            print(f"WARNING: Anthropic credits/quota exhausted — fallback summary: {exc}")
            sp._flag_credit_error(exc)
            return fallback_summary(label_fr, courses), "fallback"
        print(f"WARNING: summary API call failed ({type(exc).__name__}: {exc}) — fallback summary")
        return fallback_summary(label_fr, courses), "fallback"
    text = normalize_summary(first_text(msg))
    if not valid_summary(text):
        print(f"WARNING: summary rejected by the sanity check — fallback. Got: {text!r}")
        return fallback_summary(label_fr, courses), "fallback"
    return text, SUMMARY_MODEL


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

def course_record(c: dict) -> dict:
    ep, ch = c["ep"], c["ch"]
    artwork = "" if ch.get("speaker") else f"{SITE_URL}/artwork/{ch['slug']}.png"
    return {
        "video_id": ep["video_id"],
        "title": ep["title"].strip(),
        "rav": c["rav"],
        "rav_url": f"{SITE_URL}/{url_slug(c['rav_slug'])}.html",
        "channel": ch["slug"],
        "lang": episode_lang(ep, ch),
        "published": ep["published"],
        "duration_secs": int(ep.get("duration_secs") or 0),
        "audio_url": ep.get("audio_url", ""),
        "thumbnail": ep.get("thumbnail", ""),
        "artwork": artwork,
        "url": f"{SITE_URL}/{ep_path(ch['slug'], ep)}",
        "has_transcript": bool(c.get("has_transcript")),
        "match": c["match"],
    }


def build_week(ref: date, fetch=None, entries_loader=load_entries, fetch_tx=None,
               api_key: str | None = None, data_dir: Path = DATA_DIR,
               catalogue: tuple[list[dict], list[dict]] | None = None) -> dict:
    """Resolve, select, summarise. Returns the week data (not written)."""
    channels, speakers = catalogue or load_catalogue()
    week = resolve_week(ref, fetch)
    candidates = collect_candidates(week, channels, speakers, ref, entries_loader)
    if not any(c["match"] == "reading" for c in candidates) and week["reading"]["kind"] == "holiday":
        # Same rule as the Friday post: a festival without any class falls back
        # to the next parasha.
        print(f"  [weekly] no class for {week['reading']['fr']} — falling back to the next parasha")
        week["reading"] = sp.next_parasha_reading(week["shabbat"], fetch)
        candidates = collect_candidates(week, channels, speakers, ref, entries_loader)
    if not candidates:
        raise sp.ReadingError(f"no class at all for {week['reading']['fr']} ({week['reading']['source']})")

    enrich_with_transcripts(candidates, fetch_tx or fetch_transcript)
    chosen = pick(candidates)
    if len(chosen) < MIN_COURSES:
        print(f"  [weekly] only {len(chosen)} classes (< {MIN_COURSES}) for {week['reading']['fr']}")

    reading = week["reading"]
    label_fr, label_he = reading_labels(reading)
    slug = week_slug(reading, week["shabbat"], data_dir)
    existing = data_dir / f"{slug}.json"
    ids = [c["ep"]["video_id"] for c in chosen]
    summary, source = None, None
    if existing.exists():
        try:
            old = json.loads(existing.read_text(encoding="utf-8"))
            if [c["video_id"] for c in old.get("courses", [])] == ids and old.get("summary_source") != "fallback":
                summary, source = old["summary"], old["summary_source"]
                print("  [summary] same selection as the existing file — summary reused")
        except (OSError, ValueError, KeyError):
            pass
    if summary is None:
        summary, source = generate_summary(label_fr, chosen, api_key)

    more_url = (f"{SITE_URL}/paracha.html#{reading['slugs'][0]}" if reading["kind"] == "parasha"
                else reading.get("link") or f"{SITE_URL}/paracha.html")
    return {
        "week": slug,
        "shabbat": week["shabbat"].isoformat(),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "reading": {
            "kind": reading["kind"], "source": reading["source"], "slugs": reading["slugs"],
            "fr": reading["fr"], "he": reading["he"], "label_fr": label_fr, "label_he": label_he,
            "more_url": more_url,
        },
        "festivals": week["festivals"],
        "summary": summary,
        "summary_source": source,
        "candidates": len(candidates),
        "courses": [course_record(c) for c in chosen],
    }


def write_week(data: dict, data_dir: Path = DATA_DIR) -> list[str]:
    data_dir.mkdir(exist_ok=True)
    f = data_dir / f"{data['week']}.json"
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return [f.as_posix()] + pages.write_all(data_dir)


def already_done(ref: date, data_dir: Path = DATA_DIR) -> str | None:
    """Week file already generated for the Shabbat of `ref` (backup cron)."""
    shabbat = sp.upcoming_shabbat(ref).isoformat()
    for w in pages.load_weeks(data_dir):
        if w["shabbat"] == shabbat:
            return w["week"]
    return None


# --------------------------------------------------------------------------
# Telegram (dormant until TELEGRAM_BOT_TOKEN + TELEGRAM_CHANNEL_ID exist)
# --------------------------------------------------------------------------

def telegram_text(data: dict) -> str:
    esc = pages.esc
    lines = [f"<b>{esc(data['reading']['label_fr'])}</b> — les cours de la semaine", ""]
    lines += [esc(ln) for ln in data.get("summary", "").splitlines() if ln.strip()]
    lines.append("")
    for c in data["courses"][:6]:
        lines.append(f"• <a href=\"{esc(c['url'])}\">{esc(c['title'][:90])}</a> — {esc(c['rav'])}")
    lines += ["", f"<a href=\"{pages.week_url(data['week'])}\">Toute la sélection ({len(data['courses'])} cours)</a>"]
    text = "\n".join(lines)
    return text[:4000]


def wait_live(url: str, timeout_s: int = 45 * 60, every_s: int = 60) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if requests.get(url, params={"_": int(time.time())}, timeout=20).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(every_s)
    return False


def send_telegram(data: dict, token: str, chat_id: str, post=None) -> tuple[bool, str]:
    post = post or requests.post
    r = post(f"https://api.telegram.org/bot{token}/sendMessage",
             json={"chat_id": chat_id, "text": telegram_text(data), "parse_mode": "HTML",
                   "disable_web_page_preview": False}, timeout=20)
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 200 and body.get("ok"):
        return True, f"message_id {body.get('result', {}).get('message_id')}"
    return False, f"HTTP {r.status_code}: {str(body.get('description') or r.text)[:200]}"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _summary_md(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def _out(**kv) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            for k, v in kv.items():
                fh.write(f"{k}={v}\n")


def cmd_generate(args) -> int:
    ref = date.fromisoformat(args.date) if args.date else datetime.now(ISRAEL_TZ).date()
    done = already_done(ref)
    if done and not args.force:
        print(f"Week of {sp.upcoming_shabbat(ref)} already generated ({done}) — nothing to do.")
        _out(changed="false", week=done)
        _summary_md(f"### Paracha de la semaine\n- déjà générée : `{done}` (utiliser force pour régénérer)")
        return 0
    try:
        data = build_week(ref, api_key=None if args.no_ai else os.environ.get("ANTHROPIC_API_KEY"))
    except (sp.ReadingError, requests.RequestException, ValueError) as exc:
        print(f"::error title=Paracha de la semaine::{exc}")
        _summary_md(f"### Paracha de la semaine — ❌ ÉCHEC\n- {exc}")
        return 1
    written = write_week(data)
    print(f"Week {data['week']} ({data['reading']['label_fr']}, Shabbat {data['shabbat']}): "
          f"{len(data['courses'])} classes / {data['candidates']} candidates, "
          f"{len({c['rav'] for c in data['courses']})} rabbis, summary={data['summary_source']}")
    print("Summary:\n" + data["summary"])
    print("Written: " + ", ".join(written))
    _out(changed="true", week=data["week"])
    _summary_md(
        f"### Paracha de la semaine — ✅ `{data['week']}`\n"
        f"- {data['reading']['label_fr']} · Chabbat {data['shabbat']} · {len(data['courses'])} cours "
        f"({data['candidates']} candidats) · résumé : `{data['summary_source']}`\n\n"
        + "\n".join(f"> {ln}" for ln in data["summary"].splitlines())
    )
    if len(data["courses"]) < MIN_COURSES:
        print(f"::warning title=Paracha de la semaine::only {len(data['courses'])} classes")
    return 0


def cmd_render(_args) -> int:
    written = pages.write_all(DATA_DIR)
    print("Written: " + (", ".join(written) or "nothing (no week file)"))
    return 0


def cmd_telegram(args) -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
    if not token or not chat:
        print("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL_ID absent) — skipped.")
        _summary_md("### Telegram\n- non configuré (secrets absents) : rien envoyé")
        return 0
    weeks = pages.load_weeks(DATA_DIR)
    if not weeks:
        print("::error::no week file to announce")
        return 1
    data = weeks[0]
    url = pages.week_url(data["week"])
    if args.wait_live and not wait_live(url):
        print(f"::error title=Telegram::{url} still not live after 45 min — not sent")
        return 1
    ok, detail = send_telegram(data, token, chat)
    print(f"Telegram: {'OK' if ok else 'FAILED'} — {detail}")
    _summary_md(f"### Telegram\n- {'✅' if ok else '❌'} {detail}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--date", help="reference day (default: today, Israel)")
    g.add_argument("--force", action="store_true", help="regenerate even if this week exists")
    g.add_argument("--no-ai", action="store_true", help="deterministic summary, no API call")
    sub.add_parser("render")
    t = sub.add_parser("telegram")
    t.add_argument("--wait-live", action="store_true", help="wait for the week page to be served")
    args = ap.parse_args(argv)
    return {"generate": cmd_generate, "render": cmd_render, "telegram": cmd_telegram}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
