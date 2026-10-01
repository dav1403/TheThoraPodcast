"""
social_post.py
--------------
Generates and publishes weekly social media posts to Facebook and Instagram.

Schedule (Mon/Wed/Fri via GitHub Actions):
  Monday    -> Zoom Rabbi   (round-robin across 9 channels)
  Wednesday -> Zoom Theme   (round-robin across themes)
  Friday    -> Paracha      (current week's Torah portion)

Usage:
  python scripts/social_post.py                   # auto-detect day
  python scripts/social_post.py --type rabbi      # force type
  python scripts/social_post.py --type theme
  python scripts/social_post.py --type paracha
  python scripts/social_post.py --dry-run         # print only, no posting
"""

import argparse
import json
import os
import sys
import re
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))

from llm_util import first_text  # noqa: E402

try:
    import anthropic as _anthropic
except ImportError:
    _anthropic = None

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SITE_URL       = "https://thetorahpodcast.net"
FEEDS_DIR      = Path("feeds")
CHANNELS_FILE  = "channels.json"
STATE_FILE     = Path("social_state.json")

FB_PAGE_ID       = os.environ.get("FB_PAGE_ID", "")
FB_TOKEN         = os.environ.get("FB_ACCESS_TOKEN", "")
IG_USER_ID       = os.environ.get("IG_USER_ID", "")
ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL", "")
GRAPH_URL        = "https://graph.facebook.com/v19.0"

# Flag file read by the CI "Alert if Anthropic credits exhausted" step. Same
# pattern as tag_episodes.py: the Python script only drops a flag, the workflow
# turns it into a GitHub issue so a credit/quota exhaustion no longer fails
# silently (the post still goes out with fallback text, but the run signals it).
CREDIT_ERROR_FLAG = Path("/tmp/anthropic_credit_error")

_CREDIT_KEYWORDS = ("credit", "quota", "insufficient", "billing", "payment", "balance")


def _is_credit_error(exc: Exception) -> bool:
    """True when the Anthropic API rejects the call for billing reasons
    (exhausted credits / insufficient quota) rather than a transient issue."""
    msg = str(getattr(exc, "message", "") or exc).lower()
    if "insufficient_quota" in msg:
        return True
    status = getattr(exc, "status_code", None)
    return status in (400, 402, 429) and any(k in msg for k in _CREDIT_KEYWORDS)


def _flag_credit_error(exc: Exception) -> None:
    """Best-effort: drop the flag file the CI alert step turns into an issue.
    Never raises — an alerting failure must not crash the posting run."""
    try:
        CREDIT_ERROR_FLAG.write_text(str(exc))
    except Exception as flag_err:  # noqa: BLE001 - best effort, never fatal
        print(f"WARNING: could not write credit-error flag: {flag_err}")

THEMES = [
    "Chabbat", "Tefila", "Téchouva", "Emouna", "Etude de Torah", "Moussar",
    "Halakha", "Kabbala & Spiritualité", "Mariage & Famille",
    "Histoire juive", "Am Israël & Actualité", "Santé & Réfoua", "Parnassa",
    "Roch Hachana & Yom Kippour", "Hanoucca", "Pourim", "Pessa'h", "Chavouot",
]

# Extracted from parasha.html — single source of truth for kw matching
PARASHIOT = {
    "bereshit":       {"fr": "Bereshit",       "he": "בְּרֵאשִׁית",      "hebcal": "Bereshit",      "kw": ["bereshit","béréchit","bereschit","beresheet","בראשית"]},
    "noach":          {"fr": "Noach",           "he": "נֹחַ",             "hebcal": "Noach",          "kw": ["noach","noah","noé","נח"]},
    "lech-lecha":     {"fr": "Lech Lecha",      "he": "לֶךְ-לְךָ",        "hebcal": "Lech-Lecha",    "kw": ["lech lecha","lech-lecha","lekh lekha","לך לך"]},
    "vayera":         {"fr": "Vayera",          "he": "וַיֵּרָא",         "hebcal": "Vayera",           "kw": ["vayera","vayéra","vaiera","vaïera","וירא"]},
    "chayei-sarah":   {"fr": "Chayei Sarah",    "he": "חַיֵּי שָׂרָה",    "hebcal": "Chayei Sara",   "kw": ["chayei sarah","hayé sarah","haïé sarah","חיי שרה"]},
    "toledot":        {"fr": "Toledot",         "he": "תּוֹלְדֹת",        "hebcal": "Toldot",         "kw": ["toledot","toledoth","toldot","תולדות"]},
    "vayetze":        {"fr": "Vayetze",         "he": "וַיֵּצֵא",         "hebcal": "Vayetzei",       "kw": ["vayetze","vayétsé","vayetzé","vaïetsé","ויצא"]},
    "vayishlach":     {"fr": "Vayishlach",      "he": "וַיִּשְׁלַח",      "hebcal": "Vayishlach",    "kw": ["vayishlach","vayichlah","וישלח"]},
    "vayeshev":       {"fr": "Vayeshev",        "he": "וַיֵּשֶׁב",        "hebcal": "Vayeshev",       "kw": ["vayeshev","vayéchev","vaïéchev","וישב"]},
    "miketz":         {"fr": "Miketz",          "he": "מִקֵּץ",           "hebcal": "Miketz",         "kw": ["miketz","mikeits","mikets","מקץ"]},
    "vayigash":       {"fr": "Vayigash",        "he": "וַיִּגַּשׁ",       "hebcal": "Vayigash",       "kw": ["vayigash","vayigach","ויגש"]},
    "vayechi":        {"fr": "Vayechi",         "he": "וַיְחִי",          "hebcal": "Vayechi",        "kw": ["vayechi","vayéhi","ויחי"]},
    "shemot":         {"fr": "Shemot",          "he": "שְׁמוֹת",          "hebcal": "Shemot",         "kw": ["shemot","chemot","שמות"]},
    "vaera":          {"fr": "Va'era",          "he": "וָאֵרָא",          "hebcal": "Vaera",          "kw": ["va'éra","vaéra","vaera","וארא"]},
    "bo":             {"fr": "Bo",              "he": "בֹּא",             "hebcal": "Bo",             "kw": ["bo","בא"]},
    "beshalach":      {"fr": "Beshalach",       "he": "בְּשַׁלַּח",       "hebcal": "Beshalach",     "kw": ["béchala'h","beshalach","בשלח"]},
    "yitro":          {"fr": "Yitro",           "he": "יִתְרוֹ",          "hebcal": "Yitro",          "kw": ["yitro","jitro","יתרו"]},
    "mishpatim":      {"fr": "Mishpatim",       "he": "מִשְׁפָּטִים",     "hebcal": "Mishpatim",     "kw": ["mishpatim","michpatim","משפטים"]},
    "terumah":        {"fr": "Terouma",         "he": "תְּרוּמָה",        "hebcal": "Terumah",        "kw": ["terumah","terouma","trouma","תרומה"]},
    "tetzaveh":       {"fr": "Tetsavé",         "he": "תְּצַוֶּה",        "hebcal": "Tetzaveh",       "kw": ["tetzaveh","tetsavé","תצוה"]},
    "ki-tisa":        {"fr": "Ki Tissa",        "he": "כִּי תִשָּׂא",     "hebcal": "Ki Tisa",       "kw": ["ki tisa","ki tissa","כי תשא"]},
    "vayakhel":       {"fr": "Vayakhel",        "he": "וַיַּקְהֵל",       "hebcal": "Vayakhel",       "kw": ["vayakhel","ויקהל"]},
    "pekudei":        {"fr": "Pekudei",         "he": "פְקוּדֵי",         "hebcal": "Pekudei",        "kw": ["pekudei","פקודי"]},
    "vayikra":        {"fr": "Vayikra",         "he": "וַיִּקְרָא",       "hebcal": "Vayikra",        "kw": ["vayikra","ויקרא"]},
    "tzav":           {"fr": "Tsav",            "he": "צַו",              "hebcal": "Tzav",           "kw": ["tzav","tsav","צו"]},
    "shemini":        {"fr": "Chemini",         "he": "שְּׁמִינִי",       "hebcal": "Shmini",         "kw": ["shemini","chemini","שמיני"]},
    "tazria":         {"fr": "Tazria",          "he": "תַזְרִיעַ",        "hebcal": "Tazria",         "kw": ["tazria","תזריע"]},
    "metzora":        {"fr": "Metsora",         "he": "מְּצֹרָע",         "hebcal": "Metzora",        "kw": ["metzora","metsora","מצורע"]},
    "acharei-mot":    {"fr": "Aharei Mot",      "he": "אַחֲרֵי מוֹת",    "hebcal": "Achrei Mot",    "kw": ["acharei mot","aharei mot","אחרי מות"]},
    "kedoshim":       {"fr": "Kedochim",        "he": "קְדֹשִׁים",        "hebcal": "Kedoshim",       "kw": ["kedoshim","kedochim","קדושים"]},
    "emor":           {"fr": "Emor",            "he": "אֱמֹר",            "hebcal": "Emor",           "kw": ["emor","אמור"]},
    "behar":          {"fr": "Behar",           "he": "בְּהַר",           "hebcal": "Behar",          "kw": ["behar","בהר"]},
    "bechukotai":     {"fr": "Bechukotaï",      "he": "בְּחֻקֹּתַי",     "hebcal": "Bechukotai",    "kw": ["bechukotai","בחקתי"]},
    "bamidbar":       {"fr": "Bamidbar",        "he": "בְּמִדְבַּר",      "hebcal": "Bamidbar",       "kw": ["bamidbar","במדבר"]},
    "nasso":          {"fr": "Nasso",           "he": "נָשֹׂא",           "hebcal": "Nasso",          "kw": ["nasso","נשא"]},
    "behaalotcha":    {"fr": "Beha'alotcha",    "he": "בְּהַעֲלֹתְךָ",   "hebcal": "Beha'alotcha",  "kw": ["beha'alotcha","behaalotcha","בהעלתך"]},
    "shlach":         {"fr": "Chelah",          "he": "שְׁלַח",           "hebcal": "Sh'lach",        "kw": ["shlach","shelah","chelah","שלח"]},
    "korah":          {"fr": "Koré",            "he": "קֹרַח",            "hebcal": "Korach",         "kw": ["korah","koré","קרח"]},
    "chukat":         {"fr": "Houkat",          "he": "חֻקַּת",           "hebcal": "Chukat",         "kw": ["chukat","houkat","חקת"]},
    "balak":          {"fr": "Balak",           "he": "בָּלָק",           "hebcal": "Balak",          "kw": ["balak","בלק"]},
    "pinchas":        {"fr": "Pinhas",          "he": "פִּינְחָס",        "hebcal": "Pinchas",        "kw": ["pinchas","pinhas","פינחס"]},
    "matot":          {"fr": "Matot",           "he": "מַטּוֹת",          "hebcal": "Matot",          "kw": ["matot","מטות"]},
    "masei":          {"fr": "Massei",          "he": "מַסְעֵי",          "hebcal": "Masei",          "kw": ["masei","massé","מסעי"]},
    "devarim":        {"fr": "Devarim",         "he": "דְּבָרִים",        "hebcal": "Devarim",        "kw": ["devarim","דברים"]},
    "vaetchanan":     {"fr": "Va'etchanan",     "he": "וָאֶתְחַנַּן",    "hebcal": "Vaetchanan",    "kw": ["vaetchanan","va'etchanan","ואתחנן"]},
    "ekev":           {"fr": "Eikev",           "he": "עֵקֶב",            "hebcal": "Eikev",          "kw": ["ekev","eikev","עקב"]},
    "reeh":           {"fr": "Ré'é",            "he": "רְאֵה",            "hebcal": "Re'eh",          "kw": ["reeh","ré'é","ראה"]},
    "shoftim":        {"fr": "Choftim",         "he": "שֹׁפְטִים",        "hebcal": "Shoftim",        "kw": ["shoftim","choftim","שופטים"]},
    "ki-teitzei":     {"fr": "Ki Tetsé",        "he": "כִּי-תֵצֵא",      "hebcal": "Ki Teitzei",    "kw": ["ki teitzei","ki tetsé","כי תצא"]},
    "ki-tavo":        {"fr": "Ki Tavo",         "he": "כִּי-תָבֹא",       "hebcal": "Ki Tavo",       "kw": ["ki tavo","כי תבוא"]},
    "nitzavim":       {"fr": "Nitsavim",        "he": "נִצָּבִים",        "hebcal": "Nitzavim",       "kw": ["nitzavim","nitsavim","נצבים"]},
    "vayelech":       {"fr": "Vayelech",        "he": "וַיֵּלֶךְ",        "hebcal": "Vayeilech",      "kw": ["vayelech","וילך"]},
    "haazinu":        {"fr": "Ha'azinou",       "he": "הַאֲזִינוּ",       "hebcal": "Ha'azinu",       "kw": ["haazinu","ha'azinou","האזינו"]},
    "vezot-habracha": {"fr": "Vézot Habracha",  "he": "וְזֹאת הַבְּרָכָה","hebcal": "Vezot Habracha","kw": ["vezot habracha","וזאת הברכה","simhat torah","sim'hat torah"]},
}
def _norm_name(name):
    """Normalise a Hebcal name for lookups: Hebcal uses typographic apostrophes
    (Ha’azinu, Re’eh, Sh’lach, Beha’alotcha) and its spelling drifts over time,
    so we compare on lowercase ASCII letters/digits only."""
    return re.sub(r"[^a-z0-9]", "", name.lower())

HEBCAL_TO_SLUG = {_norm_name(p["hebcal"]): slug for slug, p in PARASHIOT.items()}
# Historical Hebcal spellings kept as aliases (the table above tracks the
# current API; these keep us working if Hebcal flips back).
HEBCAL_TO_SLUG.update({
    _norm_name("Vayeira"): "vayera",
    _norm_name("Beshallach"): "beshalach",
    _norm_name("Shemini"): "shemini",
    _norm_name("Acharei Mot"): "acharei-mot",
    _norm_name("Vayelech"): "vayelech",
})

# Festival readings used when a Shabbat has no weekly parasha (Rosh Hashana,
# Sukkot, Pesach... falling on Shabbat). "hebcal" = normalised prefixes of the
# Hebcal holiday titles (e.g. "Sukkot I", "Sukkot III (CH''M)", "Rosh Hashana
# 5787"). "parasha" = the reading is itself a parasha of the table above
# (Shmini Atzeret / Simchat Torah in Israel = Vezot Habracha). "theme" = the
# THEMES tag used as a fallback when no title matches, and the themes.html anchor.
HOLIDAYS = {
    "rosh-hashana": {"fr": "Roch Hachana", "he": "רֹאשׁ הַשָּׁנָה",
                     "hebcal": ["roshhashana"],
                     "kw": ["roch hachana", "rosh hashana", "rosh hashanah", "roch hachanah", "ראש השנה"],
                     "theme": "Roch Hachana & Yom Kippour"},
    "yom-kippur":   {"fr": "Yom Kippour", "he": "יוֹם כִּפּוּר",
                     "hebcal": ["yomkippur"],
                     "kw": ["yom kippour", "yom kippur", "kippour", "kipour", "יום כיפור", "יום הכיפורים"],
                     "theme": "Roch Hachana & Yom Kippour"},
    "sukkot":       {"fr": "Souccot", "he": "סוּכּוֹת",
                     "hebcal": ["sukkot"],
                     "kw": ["souccot", "soukkot", "souccoth", "soukot", "sukkot", "succot", "סוכות"],
                     "theme": None},
    "shmini-atzeret": {"parasha": "vezot-habracha",
                       "hebcal": ["shminiatzeret", "simchattorah"]},
    "pesach":       {"fr": "Pessa'h", "he": "פֶּסַח",
                     "hebcal": ["pesach"],
                     "kw": ["pessah", "pessa'h", "pessa’h", "pesach", "passover", "פסח"],
                     "theme": "Pessa'h"},
    "shavuot":      {"fr": "Chavouot", "he": "שָׁבוּעוֹת",
                     "hebcal": ["shavuot"],
                     "kw": ["chavouot", "chavouoth", "shavuot", "shavouot", "שבועות"],
                     "theme": "Chavouot"},
}

HASHTAGS_FR = "#Torah #Podcast #TorahPodcast #Judaisme #Shiourim #Cours"
HASHTAGS_HE = "#תורה #פודקאסט #שיעורים #שיעוריתורה #יהדות #רבנים"
HASHTAGS_BOTH = f"{HASHTAGS_FR} {HASHTAGS_HE}"

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8-sig"))
    return {"rabbi_index": 0, "theme_index": 0, "last_posted": {}, "announced_rabbis": []}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------
def load_channels():
    return [c for c in json.loads(Path(CHANNELS_FILE).read_text(encoding="utf-8-sig")) if c.get("enabled", True)]

def load_entries(slug):
    f = FEEDS_DIR / f"{slug}.entries.json"
    if not f.exists():
        return []
    return json.loads(f.read_text(encoding="utf-8-sig"))

def load_channel_info(slug):
    f = FEEDS_DIR / f"{slug}.channel_info.json"
    if not f.exists():
        return {}
    return json.loads(f.read_text(encoding="utf-8-sig"))

def artwork_url(slug):
    return f"{SITE_URL}/artwork/{slug}.png"

def channel_page_url(slug):
    return f"{SITE_URL}/{slug}.html"

def platform_links(ch):
    p = ch.get("platforms", {})
    parts = []
    if p.get("spotify"):
        parts.append(f"🎵 Spotify: {p['spotify']}")
    if p.get("apple"):
        parts.append(f"🎙️ Apple Podcasts: {p['apple']}")
    if p.get("deezer"):
        parts.append(f"🎶 Deezer: {p['deezer']}")
    return "\n".join(parts)

# ---------------------------------------------------------------------------
# Hebcal — current parasha
# ---------------------------------------------------------------------------
HEBCAL_SHABBAT_URL = "https://www.hebcal.com/shabbat"
HEBCAL_GEONAME_ID = "293397"  # Israel (Tel Aviv) — Israeli reading cycle
NEXT_PARASHA_LOOKAHEAD_WEEKS = 4


class ReadingError(RuntimeError):
    """The weekly reading could not be determined. Never swallowed: the run
    must fail loudly instead of skipping the Friday post in silence."""


def upcoming_shabbat(ref_date):
    """The Saturday on or after ref_date (the Friday run targets tomorrow)."""
    return ref_date + timedelta(days=(5 - ref_date.weekday()) % 7)


def fetch_hebcal_shabbat(ref_date):
    """Raw Hebcal /shabbat JSON for the week of ref_date. Raises on HTTP or
    JSON errors (no silent None)."""
    r = requests.get(
        HEBCAL_SHABBAT_URL,
        params={"cfg": "json", "geonameid": HEBCAL_GEONAME_ID, "M": "on",
                "gy": ref_date.year, "gm": ref_date.month, "gd": ref_date.day},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def parasha_slugs_from_title(title):
    """'Parashat Nitzavim-Vayeilech' -> ['nitzavim', 'vayelech'].

    Handles typographic apostrophes and double parashiot. Hyphens also occur
    inside single names (Lech-Lecha), so the whole name is tried first, then
    every hyphen split. Returns [] when the name is unknown."""
    name = re.sub(r"^(parashat|parshat|shabbat)\s+", "", title.strip(), flags=re.I).strip()
    slug = HEBCAL_TO_SLUG.get(_norm_name(name))
    if slug:
        return [slug]
    parts = name.split("-")
    for i in range(1, len(parts)):
        left = HEBCAL_TO_SLUG.get(_norm_name("-".join(parts[:i])))
        right = HEBCAL_TO_SLUG.get(_norm_name("-".join(parts[i:])))
        if left and right:
            return [left, right]
    return []


def holiday_key_from_title(title):
    n = _norm_name(title)
    for key, h in HOLIDAYS.items():
        if any(n.startswith(prefix) for prefix in h["hebcal"]):
            return key
    return None


def _parasha_reading(slugs, source):
    ps = [PARASHIOT[s] for s in slugs]
    return {
        "kind": "parasha",
        "source": source,
        "slugs": slugs,
        "fr": "-".join(p["fr"] for p in ps),
        "he": "-".join(p["he"] for p in ps),
        "kw": [k for p in ps for k in p["kw"]],
        "theme": None,
        "link": f"{SITE_URL}/parasha.html#{slugs[0]}",
        "hashtag": "#" + "".join(re.sub(r"[\W_]", "", p["fr"]) for p in ps),
    }


def _holiday_reading(key):
    h = HOLIDAYS[key]
    if h.get("parasha"):
        return _parasha_reading([h["parasha"]], source=f"holiday:{key}")
    theme = h.get("theme")
    link = f"{SITE_URL}/themes.html" + (f"#{quote(theme)}" if theme else "")
    return {
        "kind": "holiday",
        "source": f"holiday:{key}",
        "slugs": [key],
        "fr": h["fr"],
        "he": h["he"],
        "kw": h["kw"],
        "theme": theme,
        "link": link,
        "hashtag": "#" + re.sub(r"[\W_]", "", h["fr"]),
    }


def reading_from_hebcal(data, shabbat_date):
    """Pick the reading of the Shabbat from a Hebcal /shabbat payload.

    1. a 'parashat' item -> that parasha (single or double);
    2. otherwise a festival falling ON the Shabbat -> the festival reading.
    Returns None when neither applies (caller then looks ahead)."""
    items = data.get("items", [])
    for item in items:
        if item.get("category") == "parashat":
            slugs = parasha_slugs_from_title(item.get("title", ""))
            if not slugs:
                raise ReadingError(f"Unknown parasha name from Hebcal: {item.get('title')!r}")
            return _parasha_reading(slugs, source="hebcal")
    day = shabbat_date.isoformat()
    for item in items:
        if item.get("category") != "holiday" or str(item.get("date", ""))[:10] != day:
            continue
        key = holiday_key_from_title(item.get("title", ""))
        if key:
            return _holiday_reading(key)
    return None


def resolve_weekly_reading(ref_date=None, fetch=None):
    """The reading to post about for the week of ref_date (default: today UTC).

    Order: weekly parasha -> festival reading on Shabbat -> next weekly parasha
    (up to NEXT_PARASHA_LOOKAHEAD_WEEKS ahead). Raises ReadingError if nothing
    is found: skipping in silence is exactly the bug of Sept. 2026."""
    fetch = fetch or fetch_hebcal_shabbat
    ref_date = ref_date or datetime.now(timezone.utc).date()
    shabbat = upcoming_shabbat(ref_date)
    data = fetch(shabbat)
    reading = reading_from_hebcal(data, shabbat)
    if reading:
        return reading
    holidays = [i.get("title") for i in data.get("items", []) if i.get("category") == "holiday"]
    print(f"  [paracha] No parasha nor mapped festival on {shabbat.isoformat()} "
          f"(holidays: {holidays}) — looking ahead for the next parasha")
    return next_parasha_reading(shabbat, fetch)


def next_parasha_reading(after_shabbat, fetch=None):
    """First weekly parasha strictly after after_shabbat. Raises ReadingError."""
    fetch = fetch or fetch_hebcal_shabbat
    for week in range(1, NEXT_PARASHA_LOOKAHEAD_WEEKS + 1):
        day = after_shabbat + timedelta(weeks=week)
        reading = reading_from_hebcal(fetch(day), day)
        if reading and reading["kind"] == "parasha":
            reading["source"] = f"next-parasha:{day.isoformat()}"
            return reading
    raise ReadingError(
        f"No parasha found in the {NEXT_PARASHA_LOOKAHEAD_WEEKS} weeks after "
        f"{after_shabbat.isoformat()}"
    )


def get_current_parasha_slug():
    """Backward-compatible helper: first parasha slug of this week's reading,
    or None for a festival reading."""
    reading = resolve_weekly_reading()
    return reading["slugs"][0] if reading["kind"] == "parasha" else None

# ---------------------------------------------------------------------------
# Episode matching
# ---------------------------------------------------------------------------
def match_parasha(title, keywords):
    t = title.lower()
    for kw in keywords:
        k = kw.lower()
        if re.search(r'(?:^|[\s\-_:,!?])' + re.escape(k) + r'(?:[\s\-_:,!?]|$)', t):
            return True
    return False

def find_paracha_episodes(slug, channels):
    p = PARASHIOT.get(slug)
    if not p:
        return []
    results = []
    for ch in channels:
        entries = load_entries(ch["slug"])
        matching = [e for e in entries if match_parasha(e["title"], p["kw"])]
        if matching:
            results.append({"channel": ch, "episodes": matching, "count": len(matching)})
    return results

def find_reading_episodes(reading, channels):
    """Episodes for a weekly reading: title keyword match (all parashiot of a
    double parasha, or the festival keywords). For a festival with no keyword
    hit, fall back to the AI theme tag (broader: "Roch Hachana & Yom Kippour"
    also covers Kippour, so it is only a fallback)."""
    def _collect(pred):
        out = []
        for ch in channels:
            matching = [e for e in load_entries(ch["slug"]) if pred(e)]
            if matching:
                out.append({"channel": ch, "episodes": matching, "count": len(matching)})
        return out

    results = _collect(lambda e: match_parasha(e["title"], reading["kw"]))
    theme = reading.get("theme")
    if not results and theme:
        results = _collect(lambda e: theme in (e.get("tags") or []))
    return results

def find_theme_episodes(theme, channels, max_per_channel=3):
    results = []
    for ch in channels:
        entries = load_entries(ch["slug"])
        matching = [e for e in entries if theme in (e.get("tags") or [])]
        if matching:
            recent = sorted(matching, key=lambda e: e["published"], reverse=True)[:max_per_channel]
            results.append({"channel": ch, "episodes": recent})
    return results

# ---------------------------------------------------------------------------
# Claude Haiku content generation
# ---------------------------------------------------------------------------
def generate_text(prompt, max_tokens=400):
    if not ANTHROPIC_KEY or not _anthropic:
        return None
    client = _anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        # On credit/quota exhaustion: flag it for the CI alert step (visible
        # signal → GitHub issue) and fall back to the caller's canned text so
        # the post still goes out. Any other error is a real bug — re-raise.
        if _is_credit_error(exc):
            print(f"WARNING: Anthropic credits/quota exhausted — using fallback text: {exc}")
            _flag_credit_error(exc)
            return None
        raise
    # No text block (e.g. thinking only) -> None, so the caller uses its fallback.
    return first_text(msg) or None

# ---------------------------------------------------------------------------
# Meta API posting
# ---------------------------------------------------------------------------
# Run report (fail-loud). Every publish attempt and every post-level problem is
# recorded here; main() turns it into $GITHUB_STEP_SUMMARY and exits 1 when at
# least one CONFIGURED channel failed. A channel whose secrets are absent is
# "not_configured" (reported, not fatal); a Make.com webhook 200 is
# "unconfirmed" (the webhook acknowledges receipt, not publication).
STATUS_OK = "ok"
STATUS_UNCONFIRMED = "unconfirmed"
STATUS_FAILED = "failed"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_DRY_RUN = "dry_run"
STATUS_SKIPPED = "skipped"

RUN_RESULTS = []


def record(post, channel, status, detail=""):
    RUN_RESULTS.append({"post": post, "channel": channel, "status": status, "detail": detail})
    print(f"  [{channel}] {status}{' — ' + detail if detail else ''}")


def _graph_error(r):
    """Short, token-free description of a Graph API error response."""
    try:
        err = r.json().get("error") or {}
    except ValueError:
        return f"HTTP {r.status_code}: {r.text[:200]}"
    if err:
        parts = [f"HTTP {r.status_code}"]
        if err.get("code") is not None:
            parts.append(f"code {err.get('code')}" + (f"/{err['error_subcode']}" if err.get("error_subcode") else ""))
        parts.append(str(err.get("message", ""))[:200])
        return " ".join(parts)
    return f"HTTP {r.status_code}: {r.text[:200]}"


def _graph_id(r):
    """The object id of a successful Graph API response, else None."""
    if not r.ok:
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    return body.get("post_id") or body.get("id")


def _post_facebook_make(message, link):
    payload = {"message": message, "access_token": FB_TOKEN}
    if link:
        payload["link"] = link
    try:
        r = requests.post(MAKE_WEBHOOK_URL, json=payload, timeout=15)
    except requests.RequestException as exc:
        return False, f"Make.com webhook unreachable: {type(exc).__name__}"
    if r.ok:
        return True, f"Make.com webhook HTTP {r.status_code} — publication NOT confirmed"
    return False, f"Make.com webhook HTTP {r.status_code}: {r.text[:200]}"


def post_facebook(message, link=None, dry_run=False, post="post"):
    """Publish on the Facebook page. Direct Graph API first (the returned post
    id proves publication); Make.com webhook only as a fallback, reported as
    unconfirmed. Returns True when something was accepted."""
    if dry_run:
        print(f"\n[DRY-RUN Facebook]\n{message}")
        if link:
            print(f"Link: {link}")
        record(post, "facebook", STATUS_DRY_RUN)
        return True

    graph_error = None
    if FB_PAGE_ID and FB_TOKEN:
        params = {"message": message, "access_token": FB_TOKEN}
        if link:
            params["link"] = link
        try:
            r = requests.post(f"{GRAPH_URL}/{FB_PAGE_ID}/feed", data=params, timeout=15)
            post_id = _graph_id(r)
            if post_id:
                record(post, "facebook", STATUS_OK, f"Graph API post id {post_id}")
                return True
            graph_error = _graph_error(r) if not r.ok else f"HTTP {r.status_code} without post id"
        except requests.RequestException as exc:
            graph_error = f"Graph API unreachable: {type(exc).__name__}"

    if MAKE_WEBHOOK_URL:
        ok, detail = _post_facebook_make(message, link)
        if graph_error:
            # The direct path is configured and broken: that is a failure even
            # if Make.com said 200 (it relays the same token anyway).
            record(post, "facebook", STATUS_FAILED,
                   f"Graph API: {graph_error} | fallback {detail}")
            return ok
        record(post, "facebook", STATUS_UNCONFIRMED if ok else STATUS_FAILED, detail)
        return ok

    if graph_error:
        record(post, "facebook", STATUS_FAILED, f"Graph API: {graph_error}")
        return False
    record(post, "facebook", STATUS_NOT_CONFIGURED,
           "no FB_PAGE_ID/FB_ACCESS_TOKEN and no MAKE_WEBHOOK_URL")
    return False


def post_instagram(caption, image_url, dry_run=False, post="post"):
    if dry_run:
        print(f"\n[DRY-RUN Instagram]\n{caption}")
        print(f"Image: {image_url}")
        record(post, "instagram", STATUS_DRY_RUN)
        return True
    if not IG_USER_ID or not FB_TOKEN:
        record(post, "instagram", STATUS_NOT_CONFIGURED, "no IG_USER_ID/FB_ACCESS_TOKEN")
        return False
    try:
        # Step 1: create media container
        r = requests.post(
            f"{GRAPH_URL}/{IG_USER_ID}/media",
            data={"image_url": image_url, "caption": caption, "access_token": FB_TOKEN},
            timeout=15,
        )
        creation_id = _graph_id(r)
        if not creation_id:
            record(post, "instagram", STATUS_FAILED, f"container: {_graph_error(r)}")
            return False
        # Step 2: publish
        r2 = requests.post(
            f"{GRAPH_URL}/{IG_USER_ID}/media_publish",
            data={"creation_id": creation_id, "access_token": FB_TOKEN},
            timeout=15,
        )
    except requests.RequestException as exc:
        record(post, "instagram", STATUS_FAILED, f"Graph API unreachable: {type(exc).__name__}")
        return False
    media_id = _graph_id(r2)
    if media_id:
        record(post, "instagram", STATUS_OK, f"media id {media_id}")
        return True
    record(post, "instagram", STATUS_FAILED, f"publish: {_graph_error(r2)}")
    return False

# ---------------------------------------------------------------------------
# Post type: Paracha
# ---------------------------------------------------------------------------
def post_paracha(channels, state, dry_run=False, ref_date=None, fetch=None):
    """Friday post: the reading of the coming Shabbat.

    Weekly parasha (single or double) -> festival reading when the Shabbat is
    a festival without parasha (Rosh Hashana, Sukkot...) -> next parasha when
    the festival has no matching episode. Never skips in silence: an
    unresolvable reading or a reading without any episode is recorded as a
    failure, which makes the CI run red."""
    try:
        reading = resolve_weekly_reading(ref_date, fetch)
        results = find_reading_episodes(reading, channels)
        if not results and reading["kind"] == "holiday":
            print(f"  [paracha] No episode for festival {reading['fr']} — falling back to the next parasha")
            shabbat = upcoming_shabbat(ref_date or datetime.now(timezone.utc).date())
            reading = next_parasha_reading(shabbat, fetch)
            results = find_reading_episodes(reading, channels)
    except (ReadingError, requests.RequestException, ValueError) as exc:
        record("paracha", "reading", STATUS_FAILED, f"could not determine the weekly reading: {exc}")
        return

    label = "Paracha" if reading["kind"] == "parasha" else "Fête"
    print(f"  [paracha] {label}: {reading['fr']} ({reading['he']}) — source {reading['source']}")
    if not results:
        record("paracha", "episodes", STATUS_FAILED,
               f"no matching episode for {reading['fr']} ({reading['source']})")
        return

    total = sum(r["count"] for r in results)
    rabbi_lines = "\n".join(
        f"  🎙️ {r['channel']['podcast_author']} — {r['count']} cours"
        for r in sorted(results, key=lambda x: -x["count"])
    )

    if reading["kind"] == "parasha":
        subject = f"la paracha de la semaine : {reading['fr']} ({reading['he']})"
        if reading["source"].startswith("next-parasha"):
            subject = f"la prochaine paracha : {reading['fr']} ({reading['he']})"
        fallback = (
            f"📖 Paracha {reading['fr']} — {reading['he']}\n\n"
            f"Retrouvez tous les cours de vos rabbins préférés sur la paracha {reading['fr']} !\n"
            f"{total} cours disponibles en podcast 🎧"
        )
    else:
        subject = f"la fête de {reading['fr']} ({reading['he']}), lue ce Chabbat à la place de la paracha"
        fallback = (
            f"✨ {reading['fr']} — {reading['he']}\n\n"
            f"Ce Chabbat, pas de paracha : c'est {reading['fr']} ! "
            f"Retrouvez les cours de vos rabbins préférés sur la fête.\n"
            f"{total} cours disponibles en podcast 🎧"
        )

    prompt = (
        f"Tu gères le compte Instagram/Facebook de 'The Torah Podcast', une plateforme de cours de Torah en podcast.\n"
        f"Écris un post engageant pour annoncer {subject}.\n"
        f"Il y a {total} cours disponibles sur ce sujet chez {len(results)} rabbins.\n"
        f"Le post doit :\n"
        f"- Commencer par une accroche forte (1-2 phrases max)\n"
        f"- Mentionner qu'on peut retrouver tous les cours sur le site\n"
        f"- Être en français avec les termes hébreux habituels\n"
        f"- Inclure 3-4 emojis pertinents\n"
        f"- Faire 80-120 mots maximum\n"
        f"Ne pas inclure les hashtags ni l'URL (ajoutés séparément)."
    )
    body = generate_text(prompt) or fallback

    link = reading["link"]
    # Thursday's "paracha de la semaine" page, when it covers this very reading.
    from weekly_paracha_pages import weekly_page_link
    link = weekly_page_link(reading, ref_date) or link
    message = f"{body}\n\n{rabbi_lines}\n\n🔗 {link}\n\n{HASHTAGS_FR} {reading['hashtag']} {HASHTAGS_HE}"

    # Image: artwork of the rabbi with the most matching episodes
    image_url = artwork_url(max(results, key=lambda x: x["count"])["channel"]["slug"])

    post_facebook(message, link=link, dry_run=dry_run, post="paracha")
    post_instagram(message, image_url=image_url, dry_run=dry_run, post="paracha")

# ---------------------------------------------------------------------------
# Post type: Nouveau rabbin sur la plateforme
# ---------------------------------------------------------------------------
def post_new_rabbi(channels, state, dry_run=False):
    """Post when a new channel was added since the last announcement run."""
    announced = set(state.get("announced_rabbis", []))
    pending = [ch for ch in channels if ch["slug"] not in announced]
    if not pending:
        print("  [new_rabbi] All channels already announced — skipping")
        return False

    ch = pending[0]  # announce one per run to avoid flooding
    print(f"  [new_rabbi] Announcing: {ch['slug']} ({ch.get('podcast_language', 'fr')})")

    info = load_channel_info(ch["slug"])
    entries = load_entries(ch["slug"])
    total = len(entries)
    lang = ch.get("podcast_language", "fr").lower()

    lang_note = " *(cours en hébreu)*" if lang == "he" else ""

    prompt = (
        "Tu geres le compte Facebook de 'The Torah Podcast'.\n"
        f"Ecris un post pour annoncer l'arrivee d'un nouveau rabbin sur la plateforme : {ch['podcast_author']}{lang_note}.\n"
        f"Description : {info.get('description', '')[:300]}\n"
        f"Il y a {total} cours disponibles des le depart.\n"
        "Le post doit :\n"
        "- Commencer par '🆕' et mettre en avant que c'est un nouveau rabbin\n"
        "- Le presenter brievement et chaleureusement\n"
        "- Inviter a ecouter ses cours sur Spotify, Apple Podcasts, Deezer\n"
        "- Etre entierement en francais\n"
        + ("- Preciser que les cours sont en hebreu\n" if lang == "he" else "")
        + "- Inclure 3-4 emojis\n"
        "- Faire 80-120 mots maximum\n"
        "Ne pas inclure les hashtags ni l'URL."
    )
    fallback = (
        f"🆕 Nouveau rabbin sur The Torah Podcast — {ch['podcast_author']}\n\n"
        f"On est ravis d'accueillir {ch['podcast_author']} dans le réseau !"
        + (f" Ses cours sont en hébreu." if lang == "he" else "")
        + f" {total} cours disponibles dès maintenant.\n"
        f"À écouter sur Spotify, Apple Podcasts et Deezer 🎧"
    )
    hashtags = HASHTAGS_BOTH if lang == "he" else HASHTAGS_FR

    body = generate_text(prompt) or fallback
    link = channel_page_url(ch["slug"])
    platforms = platform_links(ch)
    message = f"{body}\n\n🔗 {link}\n\n{platforms}\n\n{hashtags}"
    image_url = artwork_url(ch["slug"])

    ok_fb = post_facebook(message, link=link, dry_run=dry_run, post="new_rabbi")
    ok_ig = post_instagram(message, image_url=image_url, dry_run=dry_run, post="new_rabbi")

    # Only mark as announced if at least one platform succeeded (or in dry-run)
    if dry_run or ok_fb or ok_ig:
        state.setdefault("announced_rabbis", []).append(ch["slug"])
        print(f"  [new_rabbi] Marked {ch['slug']} as announced")
    return True


# ---------------------------------------------------------------------------
# Post type: Zoom Rabbi
# ---------------------------------------------------------------------------
def post_rabbi(channels, state, dry_run=False):
    idx = state.get("rabbi_index", 0) % len(channels)
    ch = channels[idx]
    state["rabbi_index"] = (idx + 1) % len(channels)

    entries = load_entries(ch["slug"])
    info = load_channel_info(ch["slug"])
    if not entries:
        record("rabbi", "episodes", STATUS_SKIPPED, f"no entries for {ch['slug']}")
        return

    recent = sorted(entries, key=lambda e: e["published"], reverse=True)[:5]
    titles = "\n".join(f"  • {e['title']}" for e in recent[:3])
    total = len(entries)
    lang = ch.get("podcast_language", "fr").lower()
    lang_note = " *(cours en hébreu)*" if lang == "he" else ""

    prompt = (
        "Tu geres le compte Facebook de 'The Torah Podcast'.\n"
        f"Ecris un post 'Zoom Rabbi' pour mettre en avant : {ch['podcast_author']}{lang_note}.\n"
        f"Description du rabbi : {info.get('description', '')[:300]}\n"
        f"Il a {total} cours disponibles en podcast. Cours recents :\n{titles}\n"
        "Le post doit :\n"
        "- Presenter le rabbi chaleureusement (qui il est, son style)\n"
        "- Donner envie d'ecouter ses cours\n"
        "- Etre entierement en francais\n"
        + ("- Preciser que les cours sont en hebreu\n" if lang == "he" else "")
        + "- Inclure 3-4 emojis\n"
        "- Faire 80-120 mots maximum\n"
        "Ne pas inclure les hashtags ni l'URL."
    )
    fallback = (
        f"🎙️ Zoom Rabbi — {ch['podcast_author']}\n\n"
        f"Découvrez ou redécouvrez les enseignements de {ch['podcast_author']} !\n"
        + (f"📚 Cours en hébreu — {total} épisodes disponibles en podcast 🎧\n" if lang == "he"
           else f"{total} cours disponibles en podcast, à écouter partout et à tout moment 🎧\n")
    )
    hashtags = HASHTAGS_BOTH if lang == "he" else HASHTAGS_FR

    body = generate_text(prompt) or fallback
    link = channel_page_url(ch["slug"])
    platforms = platform_links(ch)
    message = f"{body}\n\n🔗 {link}\n\n{platforms}\n\n{hashtags}"
    image_url = artwork_url(ch["slug"])

    post_facebook(message, link=link, dry_run=dry_run, post="rabbi")
    post_instagram(message, image_url=image_url, dry_run=dry_run, post="rabbi")

# ---------------------------------------------------------------------------
# Post type: Zoom Thème
# ---------------------------------------------------------------------------
def post_theme(channels, state, dry_run=False):
    idx = state.get("theme_index", 0) % len(THEMES)
    theme = THEMES[idx]
    state["theme_index"] = (idx + 1) % len(THEMES)

    results = find_theme_episodes(theme, channels)
    if not results:
        record("theme", "episodes", STATUS_SKIPPED, f"no episode tagged '{theme}'")
        return

    total = sum(len(r["episodes"]) for r in results)
    sample_titles = [e["title"] for r in results for e in r["episodes"]][:3]
    titles_str = "\n".join(f"  • {t}" for t in sample_titles)

    prompt = (
        f"Tu gères le compte Instagram/Facebook de 'The Torah Podcast'.\n"
        f"Écris un post thématique sur le thème : '{theme}'.\n"
        f"Il y a {total} cours disponibles sur ce thème. Exemples de titres :\n{titles_str}\n"
        f"Le post doit :\n"
        f"- Accrocher sur l'importance de ce thème dans la vie juive\n"
        f"- Inviter à écouter les cours disponibles\n"
        f"- Être en français avec les termes hébreux usuels\n"
        f"- Inclure 3-4 emojis\n"
        f"- Faire 80-120 mots maximum\n"
        f"Ne pas inclure les hashtags ni l'URL."
    )
    body = generate_text(prompt) or (
        f"📚 Thème de la semaine : {theme}\n\n"
        f"Retrouvez {total} cours sur le thème '{theme}' par vos rabbins préférés !\n"
        f"Des enseignements profonds pour nourrir votre réflexion 🎧"
    )

    link = f"{SITE_URL}/themes.html"
    theme_tag = "#" + re.sub(r"[^a-zA-Z]", "", theme)
    message = f"{body}\n\n🔗 {link}\n\n{HASHTAGS_FR} {theme_tag} {HASHTAGS_HE}"

    # Image: artwork of first channel that has matching episodes
    image_url = artwork_url(results[0]["channel"]["slug"])

    post_facebook(message, link=link, dry_run=dry_run, post="theme")
    post_instagram(message, image_url=image_url, dry_run=dry_run, post="theme")

# ---------------------------------------------------------------------------
# Run summary (fail-loud)
# ---------------------------------------------------------------------------
_STATUS_LABEL = {
    STATUS_OK: "OK (publication confirmée)",
    STATUS_UNCONFIRMED: "NON CONFIRMÉ (webhook Make.com)",
    STATUS_FAILED: "ÉCHEC",
    STATUS_NOT_CONFIGURED: "non configuré",
    STATUS_DRY_RUN: "dry-run",
    STATUS_SKIPPED: "sauté",
}
PUBLISH_CHANNELS = ("facebook", "instagram")


def run_verdict(results, dry_run=False):
    """(exit_code, reasons). Exit 1 when a configured channel failed, when the
    post itself could not be built (reading/episodes), or when a real run had
    no configured channel at all (nothing could possibly be published)."""
    reasons = [f"{r['post']}/{r['channel']}: {r['detail']}" for r in results if r["status"] == STATUS_FAILED]
    publish = [r for r in results if r["channel"] in PUBLISH_CHANNELS]
    if not dry_run and publish and all(r["status"] == STATUS_NOT_CONFIGURED for r in publish):
        reasons.append("no social channel configured (Meta and Make.com secrets all empty)")
    return (1 if reasons else 0), reasons


def _md_cell(text):
    return str(text).replace("|", r"\|").replace("\n", " ")


def render_summary(post_type, results, dry_run=False):
    code, reasons = run_verdict(results, dry_run)
    head = "❌ ÉCHEC" if code else "✅ OK"
    lines = [f"## Social post — `{post_type}`{' (dry-run)' if dry_run else ''} — {head}", ""]
    if results:
        lines += ["| Post | Canal | Statut | Détail |", "|---|---|---|---|"]
        for r in results:
            lines.append(f"| {r['post']} | {r['channel']} | {_STATUS_LABEL.get(r['status'], r['status'])} "
                         f"| {_md_cell(r['detail'])} |")
    else:
        lines.append("_Aucune tentative de publication._")
    if any(r["status"] == STATUS_UNCONFIRMED for r in results):
        lines += ["", "> ⚠️ Make.com a accusé réception (HTTP 200) mais rien ne prouve que le post est "
                  "en ligne : vérifier la page Facebook."]
    if reasons:
        lines += ["", "**Causes de l'échec :**"] + [f"- {_md_cell(x)}" for x in reasons]
    return "\n".join(lines) + "\n"


def emit_summary(post_type, results, dry_run=False):
    """Print the summary, append it to $GITHUB_STEP_SUMMARY, emit ::error::
    annotations. Returns the exit code."""
    code, reasons = run_verdict(results, dry_run)
    text = render_summary(post_type, results, dry_run)
    print("\n" + text)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            print(f"WARNING: could not write step summary: {exc}")
    if os.environ.get("GITHUB_ACTIONS"):
        for reason in reasons:
            print(f"::error title=Social post {post_type}::{_md_cell(reason)}")
        for r in results:
            if r["status"] == STATUS_UNCONFIRMED:
                print(f"::warning title=Social post {post_type}::{r['channel']} non confirmé — {_md_cell(r['detail'])}")
    return code


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def detect_post_type():
    day = datetime.now(timezone.utc).weekday()  # 0=Mon, 2=Wed, 4=Fri
    return {0: "rabbi", 2: "theme", 4: "paracha"}.get(day)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=["rabbi", "theme", "paracha", "new_rabbi"], help="Force post type")
    parser.add_argument("--dry-run", action="store_true", help="Print without posting")
    args = parser.parse_args()

    post_type = args.type or detect_post_type()
    if not post_type:
        print("Today is not a posting day (Mon/Wed/Fri). Use --type to force.")
        return 0

    print(f"=== Social Post — type: {post_type} | dry-run: {args.dry_run} ===")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")

    channels = load_channels()
    state = load_state()

    if not args.type and not args.dry_run:
        today = datetime.now(timezone.utc).date().isoformat()
        if state.get("last_posted", {}).get(post_type) == today:
            print(f"Already posted '{post_type}' today ({today}) — skipping.")
            return 0

    try:
        # Priority: announce any newly-added rabbi first (one per run max)
        if post_type != "new_rabbi":
            # Auto-trigger when there are unannounced channels, regardless of scheduled type
            announced = set(state.get("announced_rabbis", []))
            if any(ch["slug"] not in announced for ch in channels):
                print("New rabbi(s) detected — announcing before scheduled post.")
                post_new_rabbi(channels, state, dry_run=args.dry_run)

        if post_type == "paracha":
            post_paracha(channels, state, dry_run=args.dry_run)
        elif post_type == "rabbi":
            post_rabbi(channels, state, dry_run=args.dry_run)
        elif post_type == "theme":
            post_theme(channels, state, dry_run=args.dry_run)
        elif post_type == "new_rabbi":
            post_new_rabbi(channels, state, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - reported in the summary, then exit 1
        record(post_type, "script", STATUS_FAILED, f"unexpected {type(exc).__name__}: {exc}")

    if not args.dry_run:
        state.setdefault("last_posted", {})[post_type] = datetime.now(timezone.utc).date().isoformat()
        save_state(state)
        print("State saved.")

    return emit_summary(post_type, RUN_RESULTS, dry_run=args.dry_run)

if __name__ == "__main__":
    sys.exit(main())
