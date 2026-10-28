"""
date_titles.py
--------------
The ONE definition of a "date-only" episode title, shared by every script that
needs it (stdlib only, so the CI test-suite can import it):

  * generate_channel_pages.py  LOG-04 - such episodes are left out of the
                               static episode list of a channel page;
  * verify_platform_titles.py  placeholder titles are never reported as a
                               title mismatch with Apple/Spotify;
  * title_resync.py            the daily pass that re-reads these titles on
                               YouTube, because a rav often publishes a class
                               under a date ("24 août 2026") and renames it
                               later, while the pipeline only reads a title
                               once, at ingestion.

Forms seen in feeds/*.entries.json (measured 28/10/2026, 157 titles out of
41 045), all matched:
    30/09/22   02/10/2022   24-04-017            numeric day/month/year
    24 août 2026   12 Janv 2025   29 Av 5783      day + month word + 4-digit year
    28 באוקטובר 2026   1 במרץ 2017               (Hebrew month names, YouTube's
                                                  own default title)
    9 OCT. 2013 11:54:01                          + optional time of day
    19 Janv 25   22 juin 25                        day + month NAME + 2-digit year
    Direct 02-08-2017                              "Direct"/"Live" + a date
Never matched: titles carrying a real subject next to a date, e.g.
"Souccot (04.10.2017)" or "Pirké Avoth (5-5)".
"""
import re

_NUMERIC = r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}"
# Any word as the month when the year has 4 digits (the historical LOG-04
# rule: it covers French, English and Hebrew month names, abbreviations and
# Hebrew-calendar months such as "Av").
_WORD_YEAR4 = r"\d{1,2}\s+\w+\.?\s+\d{4}"
# A 2-digit year is only accepted after a real month name: "2 Rois 25" is a
# class on Kings II, not a date.
_MONTHS = (r"(?:janv|f[ée]vr|mars|avr|mai|juin|juil|ao[uû]t|sept?|oct|nov|d[ée]c"
           r"|jan|feb|mar|apr|may|jun|jul|aug|dec)[a-zéû]*")
_WORD_YEAR2 = r"\d{1,2}\s+" + _MONTHS + r"\.?\s+\d{2}"
_TIME = r"(?:\s+(?:à\s+|a\s+|at\s+)?\d{1,2}[:h]\d{2}(?::\d{2})?)?"
_PREFIX = r"(?:(?:en\s+)?direct|live)\s*[-–—:|]?\s*"

DATE_TITLE_RE = re.compile(
    r"^(?:" + _PREFIX + r")?(?:" + _NUMERIC + "|" + _WORD_YEAR4 + "|" + _WORD_YEAR2 + r")"
    + _TIME + r"$",
    re.IGNORECASE,
)


def is_date_title(title) -> bool:
    """True when the whole title is a date (optionally with a time of day or
    a "Direct"/"Live" prefix) - i.e. a placeholder, not the subject of the class."""
    return bool(DATE_TITLE_RE.match((title or "").strip()))
