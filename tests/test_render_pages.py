"""Tests for render_page / render_speaker_page HTML output.

These render in-memory (no files written) so they stay fast, and guard the
"generator trap": a broken template that ships literal placeholders or drops
required SEO tags to the client site.
"""
import re

# An unresolved f-string placeholder or leftover mustache would surface as a
# literal double brace in the output. Real pages carry none (verified against
# the committed dataset), so any hit is a template bug.
_MUSTACHE_RE = re.compile(r"\{\{|\}\}")


def _speaker_episodes(gen, speaker, entries_cache):
    eps = []
    for ch_slug in speaker["from_channels"]:
        eps += [
            e
            for e in entries_cache.get(ch_slug, [])
            if gen.speaker_matches(e.get("title", ""), speaker["title_patterns"])
        ]
    eps.sort(key=lambda x: x.get("published", ""), reverse=True)
    return eps


def _playable(gen, entries):
    date_re = re.compile(
        r"^\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}$|^\d{1,2}\s+\w+\s+\d{4}$"
    )
    return [
        e
        for e in entries
        if e.get("audio_url") and not date_re.match((e.get("title") or "").strip())
    ]


def _assert_well_formed(html):
    assert html.lstrip().startswith("<!DOCTYPE html>")
    for tag in ("<html", "</html>", "</head>", "<body", "</body>"):
        assert tag in html, f"missing {tag}"
    assert not _MUSTACHE_RE.search(html), "residual mustache/placeholder in output"


def test_channel_page_well_formed(gen, channels, entries_un):
    enabled = [c for c in channels if c.get("enabled")]
    html = gen.render_page(enabled[0], entries_un, enabled, 4, 28, 21)
    _assert_well_formed(html)


def test_channel_page_has_seo_tags(gen, channels, entries_un):
    enabled = [c for c in channels if c.get("enabled")]
    ch = enabled[0]
    html = gen.render_page(ch, entries_un, enabled, 4, 28, 21)
    assert "<title>" in html and "</title>" in html
    assert re.search(r'<html lang="(fr|he)"', html)
    assert f'rel="canonical" href="{gen.BASE_URL}/{ch["slug"]}.html"' in html


def test_channel_page_lang_follows_channel(gen, channels, entries_deux):
    enabled = [c for c in channels if c.get("enabled")]
    he_ch = next(c for c in enabled if c["slug"] == "rav-test-deux")
    html = gen.render_page(he_ch, entries_deux, enabled, 4, 28, 21)
    assert re.search(r'<html lang="he"', html)


def test_channel_page_audio_count_matches_episodes(gen, channels, entries_un):
    enabled = [c for c in channels if c.get("enabled")]
    html = gen.render_page(enabled[0], entries_un, enabled, 4, 28, 21)
    assert html.count('class="play-btn"') == len(_playable(gen, entries_un))


def test_speaker_page_well_formed_and_seo(gen, channels, speakers, entries_cache):
    enabled = [c for c in channels if c.get("enabled")]
    speaker = next(s for s in speakers if s["slug"] == "rav-invite-test")
    eps = _speaker_episodes(gen, speaker, entries_cache)
    html = gen.render_speaker_page(speaker, eps, enabled, speakers, 4, 28, 21)
    _assert_well_formed(html)
    assert "<title>" in html
    assert f'rel="canonical" href="{gen.BASE_URL}/{speaker["slug"]}.html"' in html


def test_speaker_page_audio_count_matches_episodes(gen, channels, speakers, entries_cache):
    enabled = [c for c in channels if c.get("enabled")]
    speaker = next(s for s in speakers if s["slug"] == "rav-invite-test")
    eps = _speaker_episodes(gen, speaker, entries_cache)
    assert eps, "fixture must yield matched speaker episodes"
    html = gen.render_speaker_page(speaker, eps, enabled, speakers, 4, 28, 21)
    assert html.count('class="play-btn"') == len(_playable(gen, eps))


def test_speaker_name_appears_in_page(gen, channels, speakers, entries_cache):
    enabled = [c for c in channels if c.get("enabled")]
    speaker = next(s for s in speakers if s["slug"] == "rav-invite-test")
    eps = _speaker_episodes(gen, speaker, entries_cache)
    html = gen.render_speaker_page(speaker, eps, enabled, speakers, 4, 28, 21)
    assert speaker["name"] in html


def test_capitalised_slug_page_url_is_lowercase(gen, channels, entries_un):
    """A capitalised channel slug (e.g. Nahal-Haim) must expose a lowercase
    canonical page URL, while the feed/artwork keep the original slug so the
    already-ingested podcast feeds never move."""
    enabled = [c for c in channels if c.get("enabled")]
    cap = dict(enabled[0])
    cap["slug"] = "Nahal-Haim"
    html = gen.render_page(cap, entries_un, enabled + [cap], 4, 28, 21)
    assert f'rel="canonical" href="{gen.BASE_URL}/nahal-haim.html"' in html
    assert f'{gen.BASE_URL}/Nahal-Haim.html' not in html
    # Feed and artwork stay on the original (capitalised) slug.
    assert f'{gen.BASE_URL}/feeds/Nahal-Haim.xml' in html
    assert f'{gen.BASE_URL}/artwork/Nahal-Haim.png' in html


def test_url_slug_and_redirect_stub(gen, tmp_path, monkeypatch):
    assert gen.url_slug("Nahal-Haim") == "nahal-haim"
    assert gen.url_slug("already-low") == "already-low"
    monkeypatch.chdir(tmp_path)
    gen.write_redirect_stub("Nahal-Haim", "nahal-haim")
    stub = (tmp_path / "Nahal-Haim.html").read_text(encoding="utf-8")
    assert 'url=https://thetorahpodcast.net/nahal-haim.html' in stub
    assert 'noindex' in stub
    # No stub when the slug is already lowercase.
    gen.write_redirect_stub("already-low", "already-low")
    assert not (tmp_path / "already-low.html").exists()
