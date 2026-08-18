"""Tests for the transcript panel rendered under the player (chantier D).

Chantier B already put the transcript text on the page; these guard the two
display behaviours it did not cover:
  * the transcript's direction follows the SCRIPT OF THE TRANSCRIPT, not the UI
    language — a French-speaking visitor can open a Hebrew shiur, and the
    page-level dir set by applyLang() would otherwise render it left-to-right;
  * a copy button lives inside the <summary>, so it must exist with its i18n
    labels for all three locales.
"""

LESSON_FR = (
    "Nous allons étudier ce soir la paracha de la semaine, et plus précisément "
    "la question posée par Rachi sur le premier verset du chapitre. "
)
LESSON_HE = (
    "אנחנו לומדים הערב את פרשת השבוע, ובמיוחד את השאלה ששואל רש\"י על הפסוק "
    "הראשון של הפרק, שהמפרשים דנים בה באריכות. "
)


def test_is_rtl_text_detects_hebrew(gen):
    assert gen.is_rtl_text(LESSON_HE)


def test_is_rtl_text_rejects_latin_and_empty(gen):
    assert not gen.is_rtl_text(LESSON_FR)
    assert not gen.is_rtl_text("")
    # A few quoted Hebrew words inside a French class must not flip the panel.
    assert not gen.is_rtl_text(LESSON_FR * 3 + "שלום עולם")


def _render_with_transcript(gen, workdir, all_data, text):
    tdir = workdir / "feeds" / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    ch, entries = all_data[0]
    ep = next(e for e in entries if e.get("video_id"))
    (tdir / f"{ep['video_id']}.txt").write_text(text, encoding="utf-8")
    all_channels = [c for c, _ in all_data]
    return gen.render_episode_page(ep, ch, entries, all_channels)


def test_hebrew_transcript_is_marked_rtl(gen, fixture_workdir, all_data):
    html = _render_with_transcript(gen, fixture_workdir, all_data, LESSON_HE * 10)
    assert '<div class="transcript-body" dir="rtl" lang="he">' in html


def test_french_transcript_stays_ltr(gen, fixture_workdir, all_data):
    html = _render_with_transcript(gen, fixture_workdir, all_data, LESSON_FR * 10)
    assert '<div class="transcript-body">' in html
    # (the page-level `dir="rtl"` for the he UI still lives in applyLang())
    assert '<div class="transcript-body" dir=' not in html


def test_copy_button_present_and_translated(gen, fixture_workdir, all_data):
    html = _render_with_transcript(gen, fixture_workdir, all_data, LESSON_FR * 10)
    assert 'class="transcript-copy" id="transcript-copy"' in html
    assert 'data-i18n="transcript_copy"' in html
    for label in ("transcript_copy:", "transcript_copied:", "transcript_copy_error:"):
        assert html.count(label) == 3, f"{label} missing from one of fr/en/he"
    # The button sits inside <summary>; the handler must neutralise the toggle.
    assert "e.preventDefault(); e.stopPropagation();" in html


def test_no_transcript_no_panel(gen, fixture_workdir, all_data):
    html = _render_with_transcript(gen, fixture_workdir, all_data, "")
    assert 'class="transcript"' not in html
    assert 'id="transcript-copy"' not in html
