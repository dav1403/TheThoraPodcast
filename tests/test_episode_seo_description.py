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


# --- Hand-written YouTube descriptions, promo blocks, guests, Hebrew ---------
# Live cases measured 28/09/2026 on thetorahpodcast.net feeds.
JONAS = (
    "La pensée du Grand Rabbin Jonas sur la fête de Soukot en 2 x 3 minutes\n"
    "Hag saméah!"
)
SAKHOUN = (
    "Tous les jours de 8h40 à 9h20 en direct de Jerusalem - Likouté Moharan par "
    "Rav Menahem Sakhoun\n\nTous les jours de 9h30 à 10h35, Daf Hayomi en direct "
    "de Jerusalem par Rav Menahem Sakhoun."
)


def _fields(gen, ch, entries, idx, desc=None, n_same=0):
    """episode_description_fields on a COPY of the channel list where entry
    `idx` gets `desc` and `n_same` other entries get the very same text."""
    entries = [dict(e) for e in entries]
    if desc is not None:
        entries[idx]["description"] = desc
        others = [i for i in range(len(entries)) if i != idx][:n_same]
        for i in others:
            entries[i]["description"] = desc
    return gen.episode_description_fields(entries[idx], ch, entries)


def test_handwritten_description_is_used(gen, fixture_workdir, all_data):
    ch, entries = all_data[0]
    desc, abstract = _fields(gen, ch, entries, 0, JONAS, n_same=1)  # 2 episodes
    assert desc == (
        "La pensée du Grand Rabbin Jonas sur la fête de Soukot en 2 x 3 minutes. "
        "Hag saméah!"
    )
    assert abstract == ""  # too short for an abstract (desc_text_score)


def test_promo_block_repeated_on_three_episodes_falls_back(gen, fixture_workdir, all_data):
    ch, entries = all_data[0]
    long_promo = JONAS + " " + "Retrouvez chaque semaine un nouveau cours. " * 4
    desc, abstract = _fields(gen, ch, entries, 0, long_promo, n_same=2)  # 3 episodes
    ep = entries[0]
    assert desc == gen.episode_seo_description(
        ep["title"], ch["podcast_author"], ep["published"][:10]
    )
    assert abstract == ""
    # Only the opening counts: a different ending does not hide the block.
    entries2 = [dict(e) for e in entries]
    for i, tail in enumerate(("page 9 fin", "page 10", "autre chose")):
        entries2[i]["description"] = f"{long_promo} {tail}"
    d2, a2 = gen.episode_description_fields(entries2[0], ch, entries2)
    assert d2.endswith("Écoute gratuite en podcast.") and a2 == ""


def test_fixture_boilerplate_is_detected_as_promo(gen, fixture_workdir, all_data):
    # "Description : cours numero N de la chaine un." on 16 episodes: digits
    # are ignored, so it is ONE block, not 16 descriptions.
    ch, entries = all_data[0]
    desc, _ = gen.episode_description_fields(entries[1], ch, entries)
    assert "cours de Torah par Rav Test Un" in desc


def test_timetable_description_falls_back_without_abstract(gen, fixture_workdir, all_data):
    ch, entries = all_data[0]
    # Unique on the channel, but a timetable: never a description/abstract.
    ep = dict(next(e for e in entries if e.get("video_id")))
    ep["description"] = SAKHOUN
    tdir = fixture_workdir / "feeds" / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / f"{ep['video_id']}.txt").write_text(GIBBERISH * 10, encoding="utf-8")
    page = gen.render_episode_page(ep, ch, entries, [c for c, _ in all_data])
    schema = _episode_jsonld(page)
    assert "abstract" not in schema
    meta = _meta(page, "name", "description")
    assert "8h40" not in meta and "Tous les jours" not in meta
    assert meta.endswith("Écoute gratuite en podcast.")


def test_links_and_short_leads_fall_back(gen):
    assert gen.youtube_description_snippet(
        "COURS POUR FEMMES Pour recevoir les cours de Joy Galam "
        "https://chat.whatsapp.com/EVEBS7 ..."
    ) == ""
    assert gen.youtube_description_snippet(
        "Rejoignez le groupe fermé : 1push.com/link/pessah pour recevoir les vidéos"
    ) == ""
    assert gen.youtube_description_snippet("Chapitre 3 premiers versets 1-11") == ""
    assert gen.youtube_description_snippet("") == ""


def test_snippet_cuts_on_words_and_drops_rss_truncation(gen):
    trunc = (
        "Une réflexion profonde sur la sensibilité, le pouvoir des mots, les "
        "blessures invisibles, le couple, les tensions humaines et la ..."
    )
    s = gen.youtube_description_snippet(trunc)
    assert s == (
        "Une réflexion profonde sur la sensibilité, le pouvoir des mots, les "
        "blessures invisibles, le couple, les tensions humaines…"
    )
    long = "Un " + "très long cours sur les Pirké Avot " * 10 + "fin."
    s = gen.youtube_description_snippet(long)
    assert len(s) <= gen.EP_DESC_MAX and s.endswith("…")
    assert s[:-1] == s[:-1].rstrip() and long.startswith(s[:-1])
    # Whole sentences are kept while they fit; the next one is dropped.
    first = "Première phrase complète sur le cours du jour et la paracha de la semaine."
    two = f"{first} " + "Suite " * 40
    assert gen.youtube_description_snippet(two) == first


def test_guest_is_the_author_on_the_host_channel(gen, fixture_workdir, all_data):
    ch, entries = all_data[0]
    idx = next(i for i, e in enumerate(entries) if e["title"].startswith("Rav Invite Test"))
    desc, _ = gen.episode_description_fields(entries[idx], ch, entries)
    assert "cours de Torah par Rav Invite Test" in desc
    assert "Rav Test Un," not in desc
    # A non-guest episode keeps the host.
    desc0, _ = gen.episode_description_fields(entries[0], ch, entries)
    assert "cours de Torah par Rav Test Un" in desc0
    # JSON-LD author stays the channel (only the description names the guest).
    ep = entries[idx]
    page = gen.render_episode_page(ep, ch, entries, [c for c, _ in all_data])
    assert _meta(page, "name", "description") == desc
    assert _episode_jsonld(page)["author"]["name"] == ch["podcast_author"]


def test_guest_page_uses_host_promo_counts(gen, fixture_workdir, all_data):
    ch, entries = all_data[0]
    entries = [dict(e) for e in entries]
    promo = JONAS + " Abonnez-vous à la chaîne pour ne rien manquer des cours."
    for e in entries[:5]:
        e["description"] = promo
    guest_ep = next(e for e in entries if e["title"].startswith("Rav Invite Test"))
    guest_ep["description"] = promo
    gen.episode_description_fields(entries[0], ch, entries)  # host rendered first
    fake_ch = {"slug": "rav-invite-test", "podcast_author": "Rav Invite Test",
               "podcast_language": "fr", "speaker": True}
    desc, _ = gen.episode_description_fields(guest_ep, fake_ch, [guest_ep])
    assert "cours de Torah par Rav Invite Test" in desc


def test_hebrew_channel_uses_hebrew_template(gen, fixture_workdir, all_data):
    ch, entries = all_data[1]
    assert ch["podcast_language"] == "he"
    desc, _ = gen.episode_description_fields(entries[0], ch, entries)
    assert desc == (
        "שיעור מספר 1 — Rav Test Deux — שיעור תורה מאת Rav Test Deux, "
        "1 ביוני 2026. האזנה חינם בפודקאסט."
    )
    assert gen.episode_seo_description("שיעור", "הרב", "2016-02-29", "he") == (
        "שיעור — שיעור תורה מאת הרב, 29 בפברואר 2016. האזנה חינם בפודקאסט."
    )
    long_title = "שיעור ארוך מאוד על פרקי אבות " * 10
    d = gen.episode_seo_description(long_title, "הרב", "2016-02-29", "he")
    assert len(d) <= gen.EP_DESC_MAX and "האזנה חינם בפודקאסט." in d
    page = gen.render_episode_page(entries[0], ch, entries, [c for c, _ in all_data])
    assert _meta(page, "name", "description") == desc


def test_html_entities_in_title_are_decoded(gen):
    d = gen.episode_seo_description("פרק מ&quot;ה - הרב מיידנצ&#39;יק", "הרב", "", "he")
    assert d.startswith('פרק מ"ה - הרב מיידנצ\'יק — ')


def test_trailing_link_sentence_is_dropped_not_the_lead(gen):
    desc = (
        "Le temps des vacances... Rav Elie Lemmel nous offre quelques conseils "
        "pour profiter pleinement de nos vacances !\nPour suivre Elie Lemmel :\n"
        "https://www.instagram.com/elielemmel"
    )
    assert gen.youtube_description_snippet(desc) == (
        "Le temps des vacances... Rav Elie Lemmel nous offre quelques conseils "
        "pour profiter pleinement de nos vacances !"
    )
    # A timetable in the LEAD sentence rejects the whole description.
    assert gen.youtube_description_snippet(SAKHOUN) == ""
