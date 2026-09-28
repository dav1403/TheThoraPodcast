"""The episode description fields must never carry raw auto-captions.

Measured live on 28/09/2026: /lev/pirke-avot-episode-1-avraham-lemmel-2016-02-29.html
served "... une nouvelle série de cours sur pire carottes ..." (the captioner's
guess for "Pirké Avot") as <meta description>, og:description, the JSON-LD
description AND abstract — i.e. what Google and WhatsApp/Facebook display.
The description is now templated from metadata; the transcript stays in the
page body only.
"""
import html as html_lib
import json
import re

GIBBERISH = (
    "après tu es vraiment je trouve ce team oak les mots carter donc on va "
    "commencer aujourd'hui une nouvelle série de cours sur pire carottes quand "
    "on a commencé notre nouvelle série de cours sur perea vote il va falloir "
)


def _render(gen, workdir, all_data, transcript, ep_overrides=None):
    tdir = workdir / "feeds" / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    ch, entries = all_data[0]
    ep = dict(next(e for e in entries if e.get("video_id")))
    ep.update(ep_overrides or {})
    (tdir / f"{ep['video_id']}.txt").write_text(transcript, encoding="utf-8")
    all_channels = [c for c, _ in all_data]
    return ep, ch, gen.render_episode_page(ep, ch, entries, all_channels)


def _meta(page, attr, key):
    m = re.search(rf'<meta {attr}="{re.escape(key)}" content="([^"]*)">', page)
    assert m, f"{key} missing"
    return html_lib.unescape(m.group(1))


def _episode_jsonld(page):
    for block in re.findall(
        r'<script type="application/ld\+json">\s*(.*?)\s*</script>', page, re.S
    ):
        data = json.loads(block)
        if data.get("@type") == "PodcastEpisode":
            return data
    raise AssertionError("PodcastEpisode JSON-LD missing")


def test_description_fields_are_templated_not_transcript(gen, fixture_workdir, all_data):
    ep, ch, page = _render(
        gen, fixture_workdir, all_data, GIBBERISH * 10, {"description": ""}
    )
    expected = gen.episode_seo_description(
        ep["title"], ch["podcast_author"], ep["published"][:10]
    )
    assert expected.startswith(gen.seo_snippet(ep["title"], 200)[:20])
    assert f"cours de Torah par {ch['podcast_author']}" in expected
    assert expected.endswith("Écoute gratuite en podcast.")
    assert len(expected) <= gen.EP_DESC_MAX

    schema = _episode_jsonld(page)
    fields = {
        "meta description": _meta(page, "name", "description"),
        "og:description": _meta(page, "property", "og:description"),
        "twitter:description": _meta(page, "name", "twitter:description"),
        "JSON-LD description": schema["description"],
    }
    for label, value in fields.items():
        assert value == expected, label
        for word in ("pire carottes", "team oak", "perea vote"):
            assert word not in value, f"transcript text leaked into {label}"
    # No reliable summary exists, so no abstract rather than raw captions.
    assert "abstract" not in schema

    # The transcript itself is still on the page (extract + panel) ...
    assert 'id="transcript"' in page
    assert "pire carottes" in page
    # ... and the other JSON-LD fields are untouched.
    assert schema["@context"] == "https://schema.org"
    assert schema["name"] == ep["title"]
    assert schema["datePublished"] == ep["published"][:10]
    assert schema["url"] == f"{gen.BASE_URL}/{gen.ep_path(ch['slug'], ep)}"
    assert schema["partOfSeries"] == {
        "@type": "PodcastSeries",
        "name": ch["podcast_author"],
        "url": f"{gen.BASE_URL}/{gen.url_slug(ch['slug'])}.html",
    }
    assert schema["author"] == {"@type": "Person", "name": ch["podcast_author"]}
    assert schema["publisher"] == gen.SITE_PUBLISHER
    assert schema["inLanguage"] == ch.get("podcast_language", "fr")
    assert schema["wordCount"] == len(gen.clean_transcript(GIBBERISH * 10).split())
    if ep.get("audio_url"):
        assert schema["associatedMedia"] == {
            "@type": "MediaObject", "contentUrl": ep["audio_url"],
        }
    if ep.get("thumbnail"):
        assert schema["image"] == ep["thumbnail"]
    # Key order is part of the byte-stable output (no churn on the next run).
    keys = list(schema)
    assert keys[:8] == [
        "@context", "@type", "name", "datePublished", "url",
        "partOfSeries", "author", "publisher",
    ]
    assert keys[8] == "description"


def test_real_youtube_description_feeds_the_abstract(gen, fixture_workdir, all_data):
    real = (
        "Dans ce cours, le Rav explique le premier chapitre des Pirké Avot : "
        "la chaîne de transmission de la Torah depuis Moché au Sinaï jusqu'aux "
        "hommes de la Grande Assemblée, et le sens des trois conseils donnés aux juges."
    )
    _, _, page = _render(
        gen, fixture_workdir, all_data, GIBBERISH * 10, {"description": real}
    )
    schema = _episode_jsonld(page)
    assert schema["abstract"] == gen.seo_snippet(real, 300)
    assert "pire carottes" not in schema["abstract"]


def test_description_length_is_bounded(gen):
    long_title = "Pirké Avot chapitre premier michna " * 10
    for author in ("Rav Itshak Cohen", "Lev", "X" * 150, ""):
        for pub in ("2016-02-29", ""):
            d = gen.episode_seo_description(long_title, author, pub)
            assert 0 < len(d) <= gen.EP_DESC_MAX, (author, pub, d)
    short = gen.episode_seo_description("Pirke Avot 1", "Lev", "2016-02-29")
    assert short == (
        "Pirke Avot 1 — cours de Torah par Lev, 29 février 2016. "
        "Écoute gratuite en podcast."
    )
