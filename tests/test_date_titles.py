"""The shared "date-only title" rule (scripts/date_titles.py).

Positives: the forms measured in feeds/*.entries.json on 28/10/2026 (157 date
titles out of 41 045, plus the "en direct" variants). Negatives: real titles
that start with or contain a number/date and must keep their place in the
channel list (LOG-04) and in the platform title check.
"""
import importlib
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
date_titles = importlib.import_module("date_titles")

# The rule LOG-04 and verify_platform_titles used before the shared module.
OLD_RULE = re.compile(r"^\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}$|^\d{1,2}\s+\w+\s+\d{4}$")

POSITIVES = [
    "24 août 2026",
    "14 mai 2024",
    "7 juillet 2026",
    "22 octobre 2026",
    "12 Janv 2025",
    "24 novembre 2023",
    "1 février 2023",
    "29 Av 5783",
    "28 באוקטובר 2026",
    "1 במרץ 2017",
    "5 ביוני 2026",
    "30/09/22",
    "02/10/2022",
    "24-04-017",
    "9 OCT. 2013 11:54:01",
    "25 MARS 2014 12:01:09",
    "19 Janv 25",
    "22 juin 25",
    "6 juillet 25",
    "Direct 02-08-2017",
    "en direct 27-07-17",
    "Live 24 août 2026",
    "  24 août 2026  ",
]

NEGATIVES = [
    "Souccot (04.10.2017)",
    "Pirké Avoth (5-5)",
    "29 Juin 25 Part-1",
    "30 Av 5783.  Hodech Tov",
    "28 09 012 Gouter  la Souca",
    "09-08-017 Torah 51 Leçon 41",
    "320. Identité et plaisir",
    "17:09",
    "5 janv",
    "29/9",
    "2 Rois 25",
    "15 Av - TOU BE AV",
    "9 AV - TICHA BEAV - 13 août -",
    "10 jours pour tout donner !!",
    'רב יואל משי זהב שליט"א - סיפורי מעשיות א\' חלק ו\' - כולל נחל חיים',
    "Ce Qui Est Autorisé vs Interdis, pendant les 3 semaines du 9 Av.. Rav Ruben Attal",
    "",
    None,
]


@pytest.mark.parametrize("title", POSITIVES)
def test_date_only_titles_are_detected(title):
    assert date_titles.is_date_title(title)


@pytest.mark.parametrize("title", NEGATIVES)
def test_real_titles_are_not_dates(title):
    assert not date_titles.is_date_title(title)


@pytest.mark.parametrize("title", POSITIVES + [t for t in NEGATIVES if t])
def test_new_rule_is_a_superset_of_the_old_one(title):
    """Nothing the historical LOG-04 rule hid may reappear."""
    if OLD_RULE.match(title.strip()):
        assert date_titles.is_date_title(title)


def test_generator_and_title_check_share_the_module():
    """One definition only: no private copy of the regex left behind."""
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    for name in ("generate_channel_pages.py", "verify_platform_titles.py", "title_resync.py"):
        text = (scripts / name).read_text(encoding="utf-8")
        assert "from date_titles import is_date_title" in text, name
        assert r"\d{1,2}\s+\w+\s+\d{4}" not in text, name
