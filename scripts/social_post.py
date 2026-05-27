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
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))

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
    "vayera":         {"fr": "Vayera",          "he": "וַיֵּרָא",         "hebcal": "Vayeira",        "kw": ["vayera","vayéra","vaiera","vaïera","וירא"]},
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
    "beshalach":      {"fr": "Beshalach",       "he": "בְּשַׁלַּח",       "hebcal": "Beshallach",    "kw": ["béchala'h","beshalach","בשלח"]},
    "yitro":          {"fr": "Yitro",           "he": "יִתְרוֹ",          "hebcal": "Yitro",          "kw": ["yitro","jitro","יתרו"]},
    "mishpatim":      {"fr": "Mishpatim",       "he": "מִשְׁפָּטִים",     "hebcal": "Mishpatim",     "kw": ["mishpatim","michpatim","משפטים"]},
    "terumah":        {"fr": "Terouma",         "he": "תְּרוּמָה",        "hebcal": "Terumah",        "kw": ["terumah","terouma","trouma","תרומה"]},
    "tetzaveh":       {"fr": "Tetsavé",         "he": "תְּצַוֶּה",        "hebcal": "Tetzaveh",       "kw": ["tetzaveh","tetsavé","תצוה"]},
    "ki-tisa":        {"fr": "Ki Tissa",        "he": "כִּי תִשָּׂא",     "hebcal": "Ki Tisa",       "kw": ["ki tisa","ki tissa","כי תשא"]},
    "vayakhel":       {"fr": "Vayakhel",        "he": "וַיַּקְהֵל",       "hebcal": "Vayakhel",       "kw": ["vayakhel","ויקהל"]},
    "pekudei":        {"fr": "Pekudei",         "he": "פְקוּדֵי",         "hebcal": "Pekudei",        "kw": ["pekudei","פקודי"]},
    "vayikra":        {"fr": "Vayikra",         "he": "וַיִּקְרָא",       "hebcal": "Vayikra",        "kw": ["vayikra","ויקרא"]},
    "tzav":           {"fr": "Tsav",            "he": "צַו",              "hebcal": "Tzav",           "kw": ["tzav","tsav","צו"]},
    "shemini":        {"fr": "Chemini",         "he": "שְּׁמִינִי",       "hebcal": "Shemini",        "kw": ["shemini","chemini","שמיני"]},
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
HEBCAL_TO_SLUG = {p["hebcal"].lower(): slug for slug, p in PARASHIOT.items()}

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
def get_current_parasha_slug():
    try:
        r = requests.get(
            "https://www.hebcal.com/shabbat",
            params={"cfg": "json", "geonameid": "293397", "M": "on"},
            timeout=10,
        )
        for item in r.json().get("items", []):
            if item.get("category") == "parashat":
                name = item["title"].replace("Parashat ", "").replace("Shabbat ", "").strip()
                slug = HEBCAL_TO_SLUG.get(name.lower())
                if slug:
                    return slug
                # fuzzy fallback
                for s, p in PARASHIOT.items():
                    if p["hebcal"].lower() == name.lower():
                        return s
    except Exception as e:
        print(f"  [hebcal] Error: {e}")
    return None

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
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()

# ---------------------------------------------------------------------------
# Meta API posting
# ---------------------------------------------------------------------------
def post_facebook(message, link=None, dry_run=False):
    if dry_run:
        print(f"\n[DRY-RUN Facebook]\n{message}")
        if link:
            print(f"Link: {link}")
        return True
    if MAKE_WEBHOOK_URL:
        payload = {"message": message, "access_token": FB_TOKEN}
        if link:
            payload["link"] = link
        r = requests.post(MAKE_WEBHOOK_URL, json=payload, timeout=15)
        if r.ok:
            print(f"  [facebook] Sent via Make.com webhook: {r.status_code}")
            return True
        print(f"  [facebook] Make.com webhook error {r.status_code}: {r.text[:200]}")
        return False
    if not FB_PAGE_ID or not FB_TOKEN:
        print("  [facebook] Missing FB_PAGE_ID or FB_ACCESS_TOKEN — skipping")
        return False
    params = {"message": message, "access_token": FB_TOKEN}
    if link:
        params["link"] = link
    r = requests.post(f"{GRAPH_URL}/{FB_PAGE_ID}/feed", data=params, timeout=15)
    if r.ok:
        print(f"  [facebook] Posted: {r.json().get('id')}")
        return True
    print(f"  [facebook] Error {r.status_code}: {r.text[:200]}")
    return False

def post_instagram(caption, image_url, dry_run=False):
    if dry_run:
        print(f"\n[DRY-RUN Instagram]\n{caption}")
        print(f"Image: {image_url}")
        return True
    if not IG_USER_ID or not FB_TOKEN:
        print("  [instagram] Missing IG_USER_ID or FB_ACCESS_TOKEN — skipping")
        return False
    # Step 1: create media container
    r = requests.post(
        f"{GRAPH_URL}/{IG_USER_ID}/media",
        data={"image_url": image_url, "caption": caption, "access_token": FB_TOKEN},
        timeout=15,
    )
    if not r.ok:
        print(f"  [instagram] Container error {r.status_code}: {r.text[:200]}")
        return False
    creation_id = r.json().get("id")
    # Step 2: publish
    r2 = requests.post(
        f"{GRAPH_URL}/{IG_USER_ID}/media_publish",
        data={"creation_id": creation_id, "access_token": FB_TOKEN},
        timeout=15,
    )
    if r2.ok:
        print(f"  [instagram] Published: {r2.json().get('id')}")
        return True
    print(f"  [instagram] Publish error {r2.status_code}: {r2.text[:200]}")
    return False

# ---------------------------------------------------------------------------
# Post type: Paracha
# ---------------------------------------------------------------------------
def post_paracha(channels, state, dry_run=False):
    slug = get_current_parasha_slug()
    if not slug:
        print("  [paracha] Could not determine current parasha — skipping")
        return

    p = PARASHIOT[slug]
    print(f"  [paracha] Current: {p['fr']} ({p['he']})")

    results = find_paracha_episodes(slug, channels)
    if not results:
        print(f"  [paracha] No matching episodes found for {p['fr']}")
        return

    total = sum(r["count"] for r in results)
    rabbi_lines = "\n".join(
        f"  🎙️ {r['channel']['podcast_author']} — {r['count']} cours"
        for r in sorted(results, key=lambda x: -x["count"])
    )

    prompt = (
        f"Tu gères le compte Instagram/Facebook de 'The Torah Podcast', une plateforme de cours de Torah en podcast.\n"
        f"Écris un post engageant pour annoncer la paracha de la semaine : {p['fr']} ({p['he']}).\n"
        f"Il y a {total} cours disponibles sur cette paracha chez {len(results)} rabbins.\n"
        f"Le post doit :\n"
        f"- Commencer par une accroche forte (1-2 phrases max)\n"
        f"- Mentionner qu'on peut retrouver tous les cours sur le site\n"
        f"- Être en français avec les termes hébreux habituels\n"
        f"- Inclure 3-4 emojis pertinents\n"
        f"- Faire 80-120 mots maximum\n"
        f"Ne pas inclure les hashtags ni l'URL (ajoutés séparément)."
    )
    body = generate_text(prompt) or (
        f"📖 Paracha {p['fr']} — {p['he']}\n\n"
        f"Retrouvez cette semaine tous les cours de vos rabbins préférés sur la paracha {p['fr']} !\n"
        f"{total} cours disponibles en podcast 🎧"
    )

    link = f"{SITE_URL}/parasha.html#{slug}"
    message = f"{body}\n\n{rabbi_lines}\n\n🔗 {link}\n\n{HASHTAGS_FR} #{p['fr'].replace(' ','')} {HASHTAGS_HE}"

    # Image: artwork of first rabbi with matching episodes
    image_url = artwork_url(results[0]["channel"]["slug"])

    post_facebook(message, link=link, dry_run=dry_run)
    post_instagram(message, image_url=image_url, dry_run=dry_run)

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

    ok_fb = post_facebook(message, link=link, dry_run=dry_run)
    ok_ig = post_instagram(message, image_url=image_url, dry_run=dry_run)

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
        print(f"  [rabbi] No entries for {ch['slug']} — skipping")
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

    post_facebook(message, link=link, dry_run=dry_run)
    post_instagram(message, image_url=image_url, dry_run=dry_run)

# ---------------------------------------------------------------------------
# Post type: Zoom Thème
# ---------------------------------------------------------------------------
def post_theme(channels, state, dry_run=False):
    idx = state.get("theme_index", 0) % len(THEMES)
    theme = THEMES[idx]
    state["theme_index"] = (idx + 1) % len(THEMES)

    results = find_theme_episodes(theme, channels)
    if not results:
        print(f"  [theme] No episodes tagged '{theme}' — skipping")
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

    post_facebook(message, link=link, dry_run=dry_run)
    post_instagram(message, image_url=image_url, dry_run=dry_run)

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
        return

    print(f"=== Social Post — type: {post_type} | dry-run: {args.dry_run} ===")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")

    channels = load_channels()
    state = load_state()

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

    if not args.dry_run:
        state["last_posted"][post_type] = datetime.now(timezone.utc).date().isoformat()
        save_state(state)
        print("State saved.")

if __name__ == "__main__":
    main()
