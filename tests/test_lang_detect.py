"""Tests for scripts/lang_detect.py — the language of the COURSE.

Not to be confused with the UI language (FR/EN/HE), which is a front-end
concern. This module answers "in which language does the rav speak in this
class", from the dominant script of the title, with the channel's
`podcast_language` as the only fallback.
"""
import pytest

from lang_detect import DEFAULT_LANG, channel_lang, detect_lang, episode_lang


@pytest.mark.parametrize("title", [
    "שיעור בפרשת השבוע",
    "הלכות שבת - חלק ג",
    "תפילין",
])
def test_full_hebrew_titles(title):
    assert detect_lang(title) == "he"


@pytest.mark.parametrize("title", [
    "Les lois du Chabbat - partie 3",
    "TU N'AS AUCUNE ENVIE DE CHANGER, ALORS VA T'EN",
    "Halakha Yomit — Berakhot",
])
def test_full_latin_titles(title):
    assert detect_lang(title) == "fr"


def test_transliteration_is_french():
    # A Hebrew *name* written in latin letters is a French class, not a Hebrew
    # one — this is the single most common source of false "he".
    assert detect_lang("Ben Ich 'Haï - Hilloula du 13 Eloul") == "fr"
    assert detect_lang("Likoutei Moharan 282") == "fr"


def test_bilingual_title_follows_the_dominant_script():
    assert detect_lang("Rav Shmueli — שיעור שבועי בגמרא על מסכת ברכות") == "he"
    assert detect_lang("שבת - Les lois du Chabbat expliquees en detail") == "fr"


def test_niqqud_and_marks_count_as_hebrew():
    assert detect_lang("שַׁבָּת") == "he"


@pytest.mark.parametrize("title", ["", "   ", "26/08/2026", "5786 - 12", "🔥 ▶️ 12"])
def test_titles_without_letters_fall_back_to_the_channel(title):
    assert detect_lang(title, "he") == "he"
    assert detect_lang(title, "fr") == "fr"
    assert detect_lang(title) == DEFAULT_LANG
    # An unknown/absent channel language never leaks through.
    assert detect_lang(title, "en") == DEFAULT_LANG


def test_a_title_with_letters_ignores_the_channel_fallback():
    # shavei-hevron is a `podcast_language: he` channel; a latin title on it is
    # still a French class (and vice-versa).
    assert detect_lang("Cours en francais", "he") == "fr"
    assert detect_lang("שיעור", "fr") == "he"


def test_channel_lang_normalises():
    assert channel_lang({"podcast_language": "HE"}) == "he"
    assert channel_lang({"podcast_language": "es"}) == DEFAULT_LANG
    assert channel_lang({}) == DEFAULT_LANG
    assert channel_lang(None) == DEFAULT_LANG


def test_episode_lang_uses_title_then_channel():
    ch = {"podcast_language": "he"}
    assert episode_lang({"title": "שיעור"}, ch) == "he"
    assert episode_lang({"title": "Cours"}, ch) == "fr"
    assert episode_lang({"title": "2026"}, ch) == "he"
    assert episode_lang({}, ch) == "he"
    assert episode_lang({"title": "2026"}) == DEFAULT_LANG
