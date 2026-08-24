"""Build `mobile-index.json` — the pre-computed course/study rattachements.

Why this file exists
--------------------
Every "study" page of the site (daf-hayomi, hitat, hayom-yom, paracha, hiloula,
themes) does the same thing client-side: fetch **all** `feeds/<slug>.entries.json`
(~31 MB today) and filter them in the browser. That is acceptable on a desktop
page, and unacceptable on mobile data.

The mobile app needs exactly the same rattachements, so we compute them once
here, at build time, with the SAME rules the pages use, and serve one compact
JSON:

  - daf       : episodes tagged "Daf Hayomi"                (daf-hayomi.html)
  - hitat     : "HITAT DU JOUR" episodes keyed YYYYMMDD     (hitat.html)
  - hayomyom  : "Hayom Yom" episodes keyed <monthCanon><dd> (hayom-yom.html)
  - paracha   : title keyword match per parasha             (paracha.html)
  - hiloula   : title/description keyword match per tsadik  (hiloula.html)
  - themes    : the AI `tags` carried by each episode       (themes.html)
  - durations : every class binned by length                (mobile only)

Only episodes belonging to at least one bucket are emitted, and each episode is
stored ONCE as a positional array; buckets hold integer indices into it.

Called by `generate_channel_pages.py`; also runnable standalone from the repo
root, where it reads `channels.json` + `feeds/*.entries.json` and writes the
`mobile/` directory (one manifest + one file per bucket, so the app downloads
only the bucket the user opened).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# ─── Tables — kept byte-identical to the site pages ──────────────────────────
# THEMES: themes.html · PARACHIOT: paracha.html · HILOULOT: hiloula.html
# When one of those pages changes its table, change it here too.

THEMES = [
    "Chabbat", "Tefila", "Téchouva", "Emouna", "Etude de Torah", "Moussar",
    "Halakha", "Kabbala & Spiritualité", "Mariage & Famille",
    "Daf Hayomi", "Likoutei Moharan",
    "Roch Hachana & Yom Kippour", "Souccot & Sim'hat Torah", "Hanoucca",
    "Pourim", "Pessa'h", "Chavouot", "Paracha", "Histoire juive",
    "Am Israël & Actualité", "Santé & Réfoua", "Parnassa",
]

# Books, in order, for the paracha screen's grouping (paracha.html BOOKS).
BOOKS = [
    ("Bereshit", ["bereshit", "noach", "lech-lecha", "vayera", "chayei-sarah", "toledot",
                  "vayetze", "vayishlach", "vayeshev", "miketz", "vayigash", "vayechi"]),
    ("Shemot", ["shemot", "vaera", "bo", "beshalach", "yitro", "mishpatim", "terumah",
                "tetzaveh", "ki-tisa", "vayakhel", "pekudei"]),
    ("Vayikra", ["vayikra", "tzav", "shemini", "tazria", "metzora", "acharei-mot",
                 "kedoshim", "emor", "behar", "bechukotai"]),
    ("Bamidbar", ["bamidbar", "nasso", "behaalotcha", "shlach", "korah", "chukat",
                  "balak", "pinchas", "matot", "masei"]),
    ("Devarim", ["devarim", "vaetchanan", "ekev", "reeh", "shoftim", "ki-teitzei",
                 "ki-tavo", "nitzavim", "vayelech", "haazinu", "vezot-habracha"]),
]

PARACHIOT = {'bereshit': {'fr': 'Bereshit',
              'he': 'בְּרֵאשִׁית',
              'hebcal': 'Bereshit',
              'kw': ['bereshit', 'béréchit', 'bereschit', 'beresheet', 'בראשית']},
 'noach': {'fr': 'Noach', 'he': 'נֹחַ', 'hebcal': 'Noach', 'kw': ['noach', 'noah', 'noé', 'נח']},
 'lech-lecha': {'fr': 'Lech Lecha',
                'he': 'לֶךְ-לְךָ',
                'hebcal': 'Lech-Lecha',
                'kw': ['lech lecha', 'lech-lecha', 'lekh lekha', 'לך לך']},
 'vayera': {'fr': 'Vayera',
            'he': 'וַיֵּרָא',
            'hebcal': 'Vayeira',
            'kw': ['vayera', 'vayéra', 'vaiera', 'vaïera', 'וירא']},
 'chayei-sarah': {'fr': 'Chayei Sarah',
                  'he': 'חַיֵּי שָׂרָה',
                  'hebcal': 'Chayei Sara',
                  'kw': ['chayei sarah', 'hayé sarah', 'haïé sarah', 'vie de sarah', 'חיי שרה']},
 'toledot': {'fr': 'Toledot',
             'he': 'תּוֹלְדֹת',
             'hebcal': 'Toldot',
             'kw': ['toledot', 'toledoth', 'toldot', 'תולדות']},
 'vayetze': {'fr': 'Vayetze',
             'he': 'וַיֵּצֵא',
             'hebcal': 'Vayetzei',
             'kw': ['vayetze', 'vayétsé', 'vayetzé', 'vaïetsé', 'vayetse', 'ויצא']},
 'vayishlach': {'fr': 'Vayishlach',
                'he': 'וַיִּשְׁלַח',
                'hebcal': 'Vayishlach',
                'kw': ['vayishlach', 'vayichlah', 'vayishlah', 'וישלח']},
 'vayeshev': {'fr': 'Vayeshev',
              'he': 'וַיֵּשֶׁב',
              'hebcal': 'Vayeshev',
              'kw': ['vayeshev', 'vayéchev', 'vaïéchev', 'וישב']},
 'miketz': {'fr': 'Miketz',
            'he': 'מִקֵּץ',
            'hebcal': 'Miketz',
            'kw': ['miketz', 'mikeits', 'mikets', 'מקץ']},
 'vayigash': {'fr': 'Vayigash',
              'he': 'וַיִּגַּשׁ',
              'hebcal': 'Vayigash',
              'kw': ['vayigash', 'vayigach', 'ויגש']},
 'vayechi': {'fr': 'Vayechi',
             'he': 'וַיְחִי',
             'hebcal': 'Vayechi',
             'kw': ['vayechi', 'vayéhi', 'vayhi', 'ויחי']},
 'shemot': {'fr': 'Shemot',
            'he': 'שְׁמוֹת',
            'hebcal': 'Shemot',
            'kw': ['shemot', 'chemot', 'chémot', 'שמות']},
 'vaera': {'fr': "Va'era",
           'he': 'וָאֵרָא',
           'hebcal': 'Vaera',
           'kw': ["va'éra", 'vaéra', 'vaera', 'vaïéra', 'וארא']},
 'bo': {'fr': 'Bo', 'he': 'בֹּא', 'hebcal': 'Bo', 'kw': ['bo', 'בא']},
 'beshalach': {'fr': 'Beshalach',
               'he': 'בְּשַׁלַּח',
               'hebcal': 'Beshallach',
               'kw': ["béchala'h", 'beshalach', 'béchalah', 'bechalach', 'בשלח']},
 'yitro': {'fr': 'Yitro',
           'he': 'יִתְרוֹ',
           'hebcal': 'Yitro',
           'kw': ['yitro', 'jitro', 'iéthro', 'יתרו']},
 'mishpatim': {'fr': 'Mishpatim',
               'he': 'מִשְׁפָּטִים',
               'hebcal': 'Mishpatim',
               'kw': ['mishpatim', 'michpatim', 'מישפטים', 'משפטים']},
 'terumah': {'fr': 'Terouma',
             'he': 'תְּרוּמָה',
             'hebcal': 'Terumah',
             'kw': ['terumah', 'terouma', 'trouma', 'תרומה']},
 'tetzaveh': {'fr': 'Tetsavé',
              'he': 'תְּצַוֶּה',
              'hebcal': 'Tetzaveh',
              'kw': ['tetzaveh', 'tetsavé', 'tetsave', 'תצוה']},
 'ki-tisa': {'fr': 'Ki Tissa',
             'he': 'כִּי תִשָּׂא',
             'hebcal': 'Ki Tisa',
             'kw': ['ki tisa', 'ki tissa', 'ki-tissa', 'כי תשא']},
 'vayakhel': {'fr': 'Vayakhel',
              'he': 'וַיַּקְהֵל',
              'hebcal': 'Vayakhel',
              'kw': ['vayakhel', 'vaïaqhel', 'ויקהל']},
 'pekudei': {'fr': 'Pekudei',
             'he': 'פְקוּדֵי',
             'hebcal': 'Pekudei',
             'kw': ['pekudei', 'pékoudéi', 'pekoudei', 'pékoudé', 'pékoude', 'pekoudé', 'פקודי']},
 'vayikra': {'fr': 'Vayikra',
             'he': 'וַיִּקְרָא',
             'hebcal': 'Vayikra',
             'kw': ['vayikra', 'vayiqra', 'ויקרא']},
 'tzav': {'fr': 'Tsav', 'he': 'צַו', 'hebcal': 'Tzav', 'kw': ['tzav', 'tsav', 'צו']},
 'shemini': {'fr': 'Chemini',
             'he': 'שְּׁמִינִי',
             'hebcal': 'Shemini',
             'kw': ['shemini', 'chemini', 'שמיני']},
 'tazria': {'fr': 'Tazria', 'he': 'תַזְרִיעַ', 'hebcal': 'Tazria', 'kw': ['tazria', 'תזריע']},
 'metzora': {'fr': 'Metsora',
             'he': 'מְּצֹרָע',
             'hebcal': 'Metzora',
             'kw': ['metzora', 'metsora', 'מצורע']},
 'acharei-mot': {'fr': 'Aharei Mot',
                 'he': 'אַחֲרֵי מוֹת',
                 'hebcal': 'Achrei Mot',
                 'kw': ['acharei mot',
                        'aharei mot',
                        'aharé mot',
                        'aharet mot',
                        'אחרי מות',
                        'אחרי']},
 'kedoshim': {'fr': 'Kedochim',
              'he': 'קְדֹשִׁים',
              'hebcal': 'Kedoshim',
              'kw': ['kedoshim', 'kedochim', 'קדושים']},
 'emor': {'fr': 'Emor', 'he': 'אֱמֹר', 'hebcal': 'Emor', 'kw': ['emor', 'אמור']},
 'behar': {'fr': 'Behar', 'he': 'בְּהַר', 'hebcal': 'Behar', 'kw': ['behar', 'בהר']},
 'bechukotai': {'fr': 'Bechukotaï',
                'he': 'בְּחֻקֹּתַי',
                'hebcal': 'Bechukotai',
                'kw': ['bechukotai', 'bechukotaï', 'behoukotai', 'behoukotaï', 'בחקתי']},
 'bamidbar': {'fr': 'Bamidbar',
              'he': 'בְּמִדְבַּר',
              'hebcal': 'Bamidbar',
              'kw': ['bamidbar', 'bamidvar', 'במדבר']},
 'nasso': {'fr': 'Nasso', 'he': 'נָשֹׂא', 'hebcal': 'Nasso', 'kw': ['nasso', 'נשא']},
 'behaalotcha': {'fr': "Beha'alotcha",
                 'he': 'בְּהַעֲלֹתְךָ',
                 'hebcal': "Beha'alotcha",
                 'kw': ["beha'alotcha",
                        'behaalotcha',
                        'behaalotkha',
                        "beha'alotkha",
                        'béhaalotéha',
                        'behaloteha',
                        'בהעלתך']},
 'shlach': {'fr': 'Chelah',
            'he': 'שְׁלַח',
            'hebcal': "Sh'lach",
            'kw': ['shlach', 'shelah', 'chelah', 'שלח']},
 'korah': {'fr': 'Koré',
           'he': 'קֹרַח',
           'hebcal': 'Korach',
           'kw': ['korah', "kora'h", 'koré', 'coré', 'קרח']},
 'chukat': {'fr': 'Houkat',
            'he': 'חֻקַּת',
            'hebcal': 'Chukat',
            'kw': ['chukat', 'houkat', 'houqat', 'חקת']},
 'balak': {'fr': 'Balak', 'he': 'בָּלָק', 'hebcal': 'Balak', 'kw': ['balak', 'בלק']},
 'pinchas': {'fr': 'Pinhas',
             'he': 'פִּינְחָס',
             'hebcal': 'Pinchas',
             'kw': ['pinchas', 'pinhas', 'pinéhas', 'פינחס']},
 'matot': {'fr': 'Matot', 'he': 'מַטּוֹת', 'hebcal': 'Matot', 'kw': ['matot', 'מטות']},
 'masei': {'fr': 'Massei',
           'he': 'מַסְעֵי',
           'hebcal': 'Masei',
           'kw': ['masei', 'massé', 'massei', 'מסעי']},
 'devarim': {'fr': 'Devarim',
             'he': 'דְּבָרִים',
             'hebcal': 'Devarim',
             'kw': ['devarim', 'dévarim', 'דברים']},
 'vaetchanan': {'fr': "Va'etchanan",
                'he': 'וָאֶתְחַנַּן',
                'hebcal': 'Vaetchanan',
                'kw': ['vaetchanan',
                       "va'etchanan",
                       'vaétchanan',
                       'vaethanan',
                       'va-ethanan',
                       'ואתחנן']},
 'ekev': {'fr': 'Eikev', 'he': 'עֵקֶב', 'hebcal': 'Eikev', 'kw': ['ekev', 'eikev', 'éqev', 'עקב']},
 'reeh': {'fr': "Ré'é", 'he': 'רְאֵה', 'hebcal': "Re'eh", 'kw': ['reeh', "ré'é", 'reé', 'ראה']},
 'shoftim': {'fr': 'Choftim',
             'he': 'שֹׁפְטִים',
             'hebcal': 'Shoftim',
             'kw': ['shoftim', 'choftim', 'שופטים']},
 'ki-teitzei': {'fr': 'Ki Tetsé',
                'he': 'כִּי-תֵצֵא',
                'hebcal': 'Ki Teitzei',
                'kw': ['ki teitzei', 'ki tetsé', 'ki-tetsé', 'כי תצא']},
 'ki-tavo': {'fr': 'Ki Tavo',
             'he': 'כִּי-תָבֹא',
             'hebcal': 'Ki Tavo',
             'kw': ['ki tavo', 'ki-tavo', 'כי תבוא']},
 'nitzavim': {'fr': 'Nitsavim',
              'he': 'נִצָּבִים',
              'hebcal': 'Nitzavim',
              'kw': ['nitzavim', 'nitsavim', 'nécavim', 'נצבים']},
 'vayelech': {'fr': 'Vayelech',
              'he': 'וַיֵּלֶךְ',
              'hebcal': 'Vayeilech',
              'kw': ['vayelech', 'vaïélech', 'וילך']},
 'haazinu': {'fr': "Ha'azinou",
             'he': 'הַאֲזִינוּ',
             'hebcal': "Ha'azinu",
             'kw': ['haazinu', 'haazinou', "ha'azinou", 'האזינו']},
 'vezot-habracha': {'fr': 'Vézot Habracha',
                    'he': 'וְזֹאת הַבְּרָכָה',
                    'hebcal': 'Vezot Habracha',
                    'kw': ['vezot habracha',
                           'vézot habérakha',
                           'simhat torah',
                           "sim'hat torah",
                           'וזאת הברכה']}}

HILOULOT = [{'fr': 'Rabbi Chimon bar Yochaï',
  'he': 'רשב״י',
  'extra': 'Lag BaOmer',
  'hm': 'Iyyar',
  'hd': 18,
  'kw': ['rachbi',
         'rabbi chimon',
         'rabbi shimon',
         'bar yochai',
         'bar yohai',
         'רשב"י',
         'שמעון בר יוחאי',
         'lag baomer',
         "lag ba'omer",
         'lag baomer']},
 {'fr': 'Rabbi Meir Baal Haness',
  'he': 'רבי מאיר בעל הנס',
  'extra': None,
  'hm': 'Iyyar',
  'hd': 14,
  'kw': ['meir baal haness', 'baal haness', 'מאיר בעל הנס']},
 {'fr': 'Rabbi Nahman de Breslev',
  'he': 'רבי נחמן מברסלב',
  'extra': None,
  'hm': 'Tishrei',
  'hd': 18,
  'kw': ['rabbi nahman', 'rabbi na7man', 'na nach', 'נחמן', 'breslev', 'ברסלב']},
 {'fr': "Baba Salé (Rabbi Israël Abou'hatséra)",
  'he': 'בבא סאלי',
  'extra': None,
  'hm': 'Shvat',
  'hd': 4,
  'kw': ['baba sali',
         'baba salé',
         'baba sale',
         'בבא סאלי',
         'abehssera',
         "abou'hatsera",
         'abouhatsera']},
 {'fr': 'Le Rambam (Maïmonide)',
  'he': 'הרמב״ם',
  'extra': None,
  'hm': 'Tevet',
  'hd': 20,
  'kw': ['rambam', 'maimonide', 'maïmonide', 'רמב"ם']},
 {'fr': "Le Ari Zal (Rabbi Its'hak Louria)",
  'he': 'האר״י הקדוש',
  'extra': None,
  'hm': 'Av',
  'hd': 5,
  'kw': ['ari zal', 'arizal', 'האר"י', 'isaac louria', "its'hak louria"]},
 {'fr': "L'Or Ha'haïm (Rabbi Haïm ben Attar)",
  'he': 'אור החיים הקדוש',
  'extra': None,
  'hm': 'Tamuz',
  'hd': 15,
  'kw': ['or hahaim', "or ha'haim", 'אור החיים', 'ben attar']},
 {'fr': 'Rabbi Moché (Moché Rabbenou)',
  'he': 'משה רבנו',
  'extra': None,
  'hm': 'Adar',
  'hd': 7,
  'kw': ['moche rabbenou', 'moshe rabbenu', 'משה רבנו']},
 {'fr': 'Le Baal Chem Tov',
  'he': 'הבעל שם טוב',
  'extra': 'Chavouot',
  'hm': 'Sivan',
  'hd': 6,
  'kw': ['baal chem tov', 'baal shem tov', 'בעל שם טוב', 'bécht', 'besht']},
 {'fr': 'Rabbi Yéhouda ben Baba / le Netivot',
  'he': '',
  'extra': None,
  'hm': 'Cheshvan',
  'hd': 25,
  'kw': ['netivot']},
 {'fr': 'Le Rabbi de Loubavitch',
  'he': 'הרבי מליובאוויטש',
  'extra': None,
  'hm': 'Tamuz',
  'hd': 3,
  'kw': ['rabbi de loubavitch', 'loubavitch', 'ליובאוויטש', 'rebbe', 'rayatz']},
 {'fr': "Rabbi Israël Abou'hatséra — Baba Méir",
  'he': 'בבא מאיר',
  'extra': None,
  'hm': 'Elul',
  'hd': 16,
  'kw': ['baba méir', 'baba meir', 'בבא מאיר']}]

# ─── Matching — ports of the site's client-side predicates ───────────────────

_HEBREW = re.compile(r"[֐-׿]")


def _paracha_patterns(keywords: list[str]) -> list:
    """Compile paracha.html's `matchesParacha` keyword rules.

    Hebrew keywords are plain substring tests; latin ones must sit on word
    boundaries, with `-` and `'` treated as "any character" so "lech-lecha",
    "lech lecha" and "lech'lecha" all match.
    """
    out = []
    for kw in keywords:
        k = kw.lower()
        if _HEBREW.search(k):
            out.append(k)
            continue
        escaped = re.escape(k)
        # re.escape() escapes '-' and "'" too; undo, then make them wildcards.
        escaped = escaped.replace("\\-", "-").replace("\\'", "'")
        escaped = re.sub(r"[-']", ".", escaped)
        out.append(re.compile(r"(?:^|[\s\-_:,!?])" + escaped + r"(?:[\s\-_:,!?]|$)", re.I))
    return out


PARACHA_PATTERNS = {slug: _paracha_patterns(p["kw"]) for slug, p in PARACHIOT.items()}
HILOULA_KEYWORDS = [[k.lower() for k in h["kw"]] for h in HILOULOT]

IS_HITAT = re.compile(r"HITAT DU JOUR", re.I)
IS_HAYOM_YOM = re.compile(r"hayom\s*yom", re.I)

FR_MONTHS = {
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4, "mai": 5,
    "juin": 6, "juillet": 7, "aout": 8, "août": 8, "septembre": 9, "sept": 9,
    "octobre": 10, "oct": 10, "novembre": 11, "nov": 11, "decembre": 12,
    "décembre": 12, "dec": 12, "déc": 12,
}

# Canonical Hebrew month indices used by the Hayom Yom titles (hayom-yom.html).
HMONTH_CANON = {
    "tishri": 1, "tichri": 1, "tishrei": 1,
    "heshvan": 2, "cheshvan": 2, "marheshvan": 2,
    "kislev": 3,
    "teveth": 4, "tevet": 4, "tebeth": 4,
    "chevat": 5, "shevat": 5, "shvat": 5,
    "adar": 6, "adar i": 6, "adar a": 6, "adar ii": 7, "adar b": 7, "adar bet": 7,
    "nissan": 8, "nisan": 8,
    "iyar": 9, "iyyar": 9,
    "sivan": 10,
    "tamouz": 11, "tamuz": 11, "tammuz": 11,
    "av": 12,
    "eloul": 13, "elul": 13,
}

_HITAT_DATE = re.compile(r"-\s*(\d{1,2})\s+([A-Za-zÀ-ÿ.]+)\s+(\d{4})\s*$")
_HY_DATE = re.compile(r"^\s*(\d{1,2})(?:er)?(?:\s*[-–]\s*\d{1,2})?\s*[-–]?\s*([A-Za-z’'.]+)", re.I)


def canon_month(name: str | None) -> int:
    if not name:
        return 0
    n = re.sub(r"[\"'’.]", "", name.lower())
    n = re.sub(r"\s+", " ", n).strip()
    return HMONTH_CANON.get(n, 0)


def parse_hitat_key(title: str) -> str | None:
    """"… - HITAT DU JOUR 3 Elloul 5786 - 26 août 2026" → "20260826"."""
    m = _HITAT_DATE.search(title)
    if not m:
        return None
    month = FR_MONTHS.get(m.group(2).lower().rstrip(".").strip())
    if not month:
        return None
    return f"{int(m.group(3)):04d}{month:02d}{int(m.group(1)):02d}"


def parse_hayomyom_key(title: str) -> str | None:
    """"23 Tamouz - Hayom Yom …" → "1123" (monthCanon * 100 + day)."""
    m = _HY_DATE.match(title)
    if not m:
        return None
    day = int(m.group(1))
    month = canon_month(m.group(2))
    if not day or not month:
        return None
    return str(month * 100 + day)


def matches_paracha(title: str, slug: str) -> bool:
    t = title.lower()
    for pat in PARACHA_PATTERNS[slug]:
        if isinstance(pat, str):
            if pat in t:
                return True
        elif pat.search(t):
            return True
    return False


def matches_hiloula(title: str, description: str, idx: int) -> bool:
    hay = (title + " " + description).lower()
    return any(k in hay for k in HILOULA_KEYWORDS[idx])


# ─── Index build ─────────────────────────────────────────────────────────────

OUT_DIR = Path("mobile")

# Compact codec (the app's `src/api/mobileIndex.ts` mirrors it exactly).
#
# audio : "<prefixIdx>|<rest>" — nearly every episode sits behind the same R2
#         bucket prefix, so we table the prefixes in the manifest and store the
#         remainder only. An empty prefixIdx means `rest` is the full URL.
# thumb : "" → i.ytimg.com/vi/<video_id>/maxresdefault.jpg (the common case),
#         "h" → hqdefault, "l" → maxresdefault_live, "hl" → hqdefault_live,
#         anything else → the literal URL.
THUMB_CODES = {
    "maxresdefault": "",
    "hqdefault": "h",
    "maxresdefault_live": "l",
    "hqdefault_live": "hl",
}
_YTIMG = re.compile(r"^https://i\.ytimg\.com/vi/([^/]+)/([a-z_]+)\.jpg$")

# Per-bucket payload caps. The app paginates inside a bucket and shows the real
# `total` from the manifest, so a cap only bounds how deep you can scroll — it
# never changes the counts the user reads. Newest-first, so the cap drops the
# oldest classes.
CAP_THEME = 300
CAP_PARACHA = 250
CAP_DAF = 300
CAP_HILOULA = 60
CAP_DURATION = 300

# Duration bins (mobile only).
#
# The site's shared filter (js/utils.js `filterDurAll`) offers three coarse
# buckets - < 5 min / 5-20 min / > 20 min. The app wants a finer, *continuous*
# ladder so "j'ai 25 minutes" is one tap: the 300 s and 1200 s cut points are
# the site's, 600 s and 1800 s only subdivide them, so the two never disagree -
# it is the same ladder, one notch finer.
# `(slug, label, min_secs_inclusive, max_secs_exclusive_or_None)`.
DURATIONS = [
    ("moins-5", "< 5 min", 1, 300),
    ("5-10", "5-10 min", 300, 600),
    ("10-20", "10-20 min", 600, 1200),
    ("20-30", "20-30 min", 1200, 1800),
    ("30-plus", "30 min et +", 1800, None),
]


def duration_bin(secs: int) -> str | None:
    """Slug of the bin a length falls into; None when unknown (0) or negative."""
    if not secs or secs < 1:
        return None
    for slug, _label, lo, hi in DURATIONS:
        if secs >= lo and (hi is None or secs < hi):
            return slug
    return None


THEME_SLUGS = {
    "Chabbat": "chabbat", "Tefila": "tefila", "Téchouva": "techouva",
    "Emouna": "emouna", "Etude de Torah": "etude-de-torah", "Moussar": "moussar",
    "Halakha": "halakha", "Kabbala & Spiritualité": "kabbala",
    "Mariage & Famille": "mariage-famille", "Daf Hayomi": "daf-hayomi",
    "Likoutei Moharan": "likoutei-moharan",
    "Roch Hachana & Yom Kippour": "roch-hachana-yom-kippour",
    "Souccot & Sim'hat Torah": "souccot", "Hanoucca": "hanoucca",
    "Pourim": "pourim", "Pessa'h": "pessah", "Chavouot": "chavouot",
    "Paracha": "paracha", "Histoire juive": "histoire-juive",
    "Am Israël & Actualité": "am-israel", "Santé & Réfoua": "sante-refoua",
    "Parnassa": "parnassa",
}


def _encode_thumb(thumb: str, video_id: str) -> str:
    m = _YTIMG.match(thumb or "")
    if m and m.group(1) == video_id and m.group(2) in THUMB_CODES:
        return THUMB_CODES[m.group(2)]
    return thumb or ""


def _row(ch_idx: int, ep: dict, prefixes: dict[str, int]) -> list:
    """One episode as a positional row — the app's `decodeEpisode` mirrors it."""
    url = ep["audio_url"]
    head, _, tail = url.rpartition("/")
    idx = prefixes.get(head + "/")
    # Prefixes seen only once (self-hosted one-offs whose "directory" carries an
    # episode id) would bloat the table for nothing — those keep the full URL.
    audio = f"{idx}|{tail}" if idx is not None else f"|{url}"
    return [
        ch_idx,
        ep["video_id"],
        ep["title"],
        ep["published"][:10],
        int(ep.get("duration_secs") or 0),
        audio,
        _encode_thumb(ep.get("thumbnail") or "", ep["video_id"]),
    ]


def _write(name: str, payload: dict) -> int:
    path = OUT_DIR / name
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return len(text.encode("utf-8"))


def build_mobile_index(all_data: list[tuple]) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    channels = [[ch["slug"], ch["podcast_author"]] for ch, _ in all_data]

    # Flatten newest-first so every bucket comes out pre-sorted.
    flat: list[tuple[int, dict]] = []
    for ch_idx, (_ch, entries) in enumerate(all_data):
        for ep in entries:
            if not ep.get("title") or not ep.get("published") or not ep.get("audio_url"):
                continue
            flat.append((ch_idx, ep))
    flat.sort(key=lambda x: x[1].get("published", ""), reverse=True)

    counts = Counter(ep["audio_url"].rpartition("/")[0] + "/" for _i, ep in flat)
    prefix_list = [p for p, n in counts.most_common() if n >= 5]
    prefixes = {p: i for i, p in enumerate(prefix_list)}

    themes: dict[str, list] = {t: [] for t in THEMES}
    theme_totals: dict[str, int] = {t: 0 for t in THEMES}
    paracha: dict[str, list] = {s: [] for s in PARACHIOT}
    paracha_totals: dict[str, int] = {s: 0 for s in PARACHIOT}
    hiloula: dict[str, list] = {str(i): [] for i in range(len(HILOULOT))}
    hiloula_totals = [0] * len(HILOULOT)
    hitat: dict[str, list] = {}
    hayomyom: dict[str, list] = {}
    daf: list = []
    daf_total = 0
    durations: dict[str, list] = {slug: [] for slug, *_ in DURATIONS}
    duration_totals: dict[str, int] = {slug: 0 for slug, *_ in DURATIONS}

    for ch_idx, ep in flat:
        title = ep["title"]
        tags = ep.get("tags") or []
        row = None

        def get_row():
            nonlocal row
            if row is None:
                row = _row(ch_idx, ep, prefixes)
            return row

        bin_slug = duration_bin(int(ep.get("duration_secs") or 0))
        if bin_slug:
            duration_totals[bin_slug] += 1
            if len(durations[bin_slug]) < CAP_DURATION:
                durations[bin_slug].append(get_row())

        if "Daf Hayomi" in tags:
            daf_total += 1
            if len(daf) < CAP_DAF:
                daf.append(get_row())

        for tag in tags:
            if tag in themes:
                theme_totals[tag] += 1
                if len(themes[tag]) < CAP_THEME:
                    themes[tag].append(get_row())

        is_hitat = bool(IS_HITAT.search(title))
        if is_hitat:
            key = parse_hitat_key(title)
            if key:
                hitat.setdefault(key, []).append(get_row())
        elif IS_HAYOM_YOM.search(title):
            key = parse_hayomyom_key(title)
            if key:
                hayomyom.setdefault(key, []).append(get_row())

        # paracha.html deliberately excludes the daily HITAT shiurim: their
        # titles carry the parasha name and would flood every parasha.
        if not is_hitat:
            for slug in PARACHIOT:
                if matches_paracha(title, slug):
                    paracha_totals[slug] += 1
                    if len(paracha[slug]) < CAP_PARACHA:
                        paracha[slug].append(get_row())

        description = ep.get("description") or ""
        for i, _h in enumerate(HILOULOT):
            if matches_hiloula(title, description, i):
                hiloula_totals[i] += 1
                if len(hiloula[str(i)]) < CAP_HILOULA:
                    hiloula[str(i)].append(get_row())

    written = 0
    for theme, rows in themes.items():
        if not rows:
            continue
        written += _write(
            f"theme-{THEME_SLUGS[theme]}.json",
            {"generated_at": generated_at, "total": theme_totals[theme], "episodes": rows},
        )
    for slug, rows in paracha.items():
        if not rows:
            continue
        written += _write(
            f"paracha-{slug}.json",
            {"generated_at": generated_at, "total": paracha_totals[slug], "episodes": rows},
        )
    for slug, rows in durations.items():
        if not rows:
            continue
        written += _write(
            f"duration-{slug}.json",
            {"generated_at": generated_at, "total": duration_totals[slug], "episodes": rows},
        )
    written += _write("daf.json", {"generated_at": generated_at, "total": daf_total, "episodes": daf})
    written += _write("hiloula.json", {"generated_at": generated_at, "tsadikim": hiloula})

    # Hitat and Hayom Yom are keyed by day; the app only ever needs one day, so
    # they are sharded (by Gregorian month / by Hebrew month) instead of served
    # as one fat file. The manifest carries every available key so the app can
    # pick the site's "latest published on or before" fallback without probing.
    hitat_shards: dict[str, dict] = {}
    for key, rows in hitat.items():
        hitat_shards.setdefault(key[:6], {})[key] = rows
    for month, days in hitat_shards.items():
        written += _write(f"hitat-{month}.json", {"generated_at": generated_at, "days": days})

    hy_shards: dict[str, dict] = {}
    for key, rows in hayomyom.items():
        hy_shards.setdefault(key[:-2], {})[key] = rows
    for month, days in hy_shards.items():
        written += _write(f"hayom-yom-{month}.json", {"generated_at": generated_at, "days": days})

    manifest = {
        "generated_at": generated_at,
        "version": 1,
        "channels": channels,
        "audio_prefixes": prefix_list,
        "daf": {"file": "daf.json", "total": daf_total},
        "themes": [
            {"name": t, "slug": THEME_SLUGS[t], "file": f"theme-{THEME_SLUGS[t]}.json",
             "total": theme_totals[t]}
            for t in THEMES if themes[t]
        ],
        "parachiot": [
            {"slug": s, "fr": PARACHIOT[s]["fr"], "he": PARACHIOT[s]["he"],
             "hebcal": PARACHIOT[s]["hebcal"], "book": book,
             "file": f"paracha-{s}.json" if paracha[s] else None,
             "total": paracha_totals[s]}
            for book, slugs in BOOKS for s in slugs
        ],
        "durations": [
            {"slug": slug, "label": label, "min": lo, "max": hi,
             "file": f"duration-{slug}.json", "total": duration_totals[slug]}
            for slug, label, lo, hi in DURATIONS if durations[slug]
        ],
        "hitat_days": sorted(hitat),
        "hayomyom_days": sorted(hayomyom, key=int),
        "hiloulot": [
            {"fr": h["fr"], "he": h["he"], "extra": h["extra"], "hm": h["hm"], "hd": h["hd"],
             "total": hiloula_totals[i]}
            for i, h in enumerate(HILOULOT)
        ],
    }
    written += _write("manifest.json", manifest)

    print(f"  mobile/  ({len(themes)} themes, {len(paracha)} parashiot, "
          f"{len(hitat)} hitat days, {len(hayomyom)} hayom-yom days, "
          f"{written // 1024} KB total)")


def _load_all_data() -> list[tuple]:
    channels = json.loads(Path("channels.json").read_text(encoding="utf-8"))
    out = []
    for ch in channels:
        if not ch.get("enabled"):
            continue
        path = Path("feeds") / f"{ch['slug']}.entries.json"
        if not path.exists():
            continue
        out.append((ch, json.loads(path.read_text(encoding="utf-8"))))
    return out


if __name__ == "__main__":
    build_mobile_index(_load_all_data())
