"""Tests for the transcript-driven SEO surface of episode pages (chantier B).

Two defects measured on the live dataset (18/08/2026) are guarded here:
  * auto-captions open on a jingle / music cue, which used to become the visible
    lead AND the <meta name="description"> of thousands of pages;
  * 9 846 of the 27 156 transcript files are 0 byte (fetch_transcripts.py writes
    an empty file when YouTube has no captions), which used to be advertised in
    sitemap.xml at the priority reserved for pages carrying real text.
"""

LESSON = (
    "Nous allons étudier ce soir la paracha de la semaine, et plus précisément "
    "la question posée par Rachi sur le premier verset du chapitre, que les "
    "commentateurs discutent longuement depuis des siècles. "
)


def test_lead_drops_the_music_cue(gen):
    text = "[Musique] " + LESSON
    extract = gen.transcript_extract(text)
    assert not extract.startswith("[")
    assert "Musique" not in extract
    assert extract.startswith("Nous allons étudier")


def test_lead_drops_a_chanted_intro(gen):
    text = "spéciale spéciale spéciale bonjour à tous les amis de la communauté. " + LESSON
    assert gen.transcript_extract(text).startswith("Nous allons étudier")


def test_lead_drops_a_bare_greeting(gen):
    text = "Bonjour à tous. " + LESSON
    assert gen.transcript_extract(text).startswith("Nous allons étudier")


def test_mid_class_cue_is_kept_as_content(gen):
    """Only the *leading* filler is skipped: the class itself must survive."""
    text = LESSON + "[Musique] " + LESSON
    assert gen.transcript_extract(text).startswith("Nous allons étudier")


def test_lead_falls_back_when_everything_looks_like_filler(gen):
    """A page must never lose its lead entirely (short or chanted classes)."""
    text = "Amen amen amen amen. Amen amen amen amen."
    assert gen.transcript_extract(text)


def test_extract_respects_the_word_budget(gen):
    extract = gen.transcript_extract(LESSON * 40)
    assert len(extract.split()) <= gen.EXTRACT_WORDS * 1.6


def test_sitemap_priority_ignores_empty_transcript_placeholders(
    gen, fixture_workdir, all_data
):
    tdir = fixture_workdir / "feeds" / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    eps = [ep for _, entries in all_data for ep in entries if ep.get("video_id")]
    assert len(eps) >= 2, "fixture needs at least two episodes with a video_id"
    real, placeholder = eps[0], eps[1]
    (tdir / f"{real['video_id']}.txt").write_text(
        LESSON * 10, encoding="utf-8"
    )
    (tdir / f"{placeholder['video_id']}.txt").write_text("", encoding="utf-8")

    gen.update_sitemap([(ch["slug"], entries) for ch, entries in all_data])
    sitemap = (fixture_workdir / "sitemap.xml").read_text(encoding="utf-8")

    def priority_of(ep, slug):
        loc = f"{gen.BASE_URL}/{gen.ep_path(slug, ep)}"
        block = sitemap.split(f"<loc>{loc}</loc>", 1)[1]
        return block.split("<priority>", 1)[1].split("</priority>", 1)[0]

    slug_of = {
        ep["video_id"]: ch["slug"]
        for ch, entries in all_data
        for ep in entries
        if ep.get("video_id")
    }
    assert priority_of(real, slug_of[real["video_id"]]) == "0.7"
    assert priority_of(placeholder, slug_of[placeholder["video_id"]]) == "0.5"
