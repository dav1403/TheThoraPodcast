#!/usr/bin/env python3
"""
generate_channel_pages.py
Generates a static <slug>.html per enabled channel and updates sitemap.xml.
No external dependencies — stdlib only.
"""
import json
import html as _html
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

from build_mobile_index import build_mobile_index
from lang_detect import episode_lang

BASE_URL       = "https://thetorahpodcast.net"

# Brand entity shared by every generated page. The @id matches the Organization
# node declared on the home page (index.html), so Google consolidates all pages
# under a single "The Torah Podcast" entity instead of treating each show as an
# unrelated site. Keep the @id in sync with index.html.
SITE_PUBLISHER = {
    "@type": "Organization",
    "@id": f"{BASE_URL}/#organization",
    "name": "The Torah Podcast",
    "url": f"{BASE_URL}/",
}
CHANNELS_FILE  = Path("channels.json")
SPEAKERS_FILE  = Path("speakers.json")
FEEDS_DIR      = Path("feeds")

MONTHS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

# Nav "Rabbins" dropdown items, shared across channel/speaker/episode pages.
# Each item: {"name": str, "slug": str, "count": int}. Populated in main() so the
# episode count next to each rav is computed once (not recomputed per page/client)
# and the list is sorted A-Z. Mirrors the client-side dropdown in rabbins.html.
NAV_ITEMS: list[dict] = []


def url_slug(slug: str) -> str:
    """Canonical, lowercase URL form of a channel/speaker slug.

    The on-disk feed files, episode directories and artwork keep their original
    (sometimes capitalised) slug — e.g. feeds/Nahal-Haim.xml, which Spotify/Apple
    already ingest and must never move. Only the *HTML page* URL is normalised to
    lowercase so the site exposes a single canonical, lowercase URL per rav. The
    capitalised page is emitted as a redirect stub (see write_redirect_stub)."""
    return slug.lower()


REDIRECT_STUB = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Redirection — The Torah Podcast</title>
<link rel="canonical" href="{base}/{target}.html">
<meta http-equiv="refresh" content="0; url={base}/{target}.html">
<meta name="robots" content="noindex, follow">
</head>
<body>
<p>Cette page a été déplacée. <a href="{base}/{target}.html">Cliquez ici si vous n'êtes pas redirigé automatiquement</a>.</p>
<script>location.replace("{base}/{target}.html");</script>
</body>
</html>
"""


def write_redirect_stub(from_slug: str, to_slug: str) -> None:
    """Write a canonical redirect stub at <from_slug>.html pointing to the
    lowercase canonical <to_slug>.html. No-op when they are already equal."""
    if from_slug == to_slug:
        return
    Path(f"{from_slug}.html").write_text(
        REDIRECT_STUB.format(base=BASE_URL, target=to_slug), encoding="utf-8"
    )


def render_nav_submenu(href_prefix: str = "") -> str:
    """Render the Rabbins dropdown links from NAV_ITEMS, showing '(N)' episode counts.
    href_prefix is "" for top-level pages and "../" for episode subpages."""
    parts = []
    for it in NAV_ITEMS:
        count_tag = " ({})".format(it["count"]) if it["count"] else ""
        parts.append(
            '      <a href="{}{}.html">{}{}</a>'.format(
                href_prefix, esc(url_slug(it["slug"])), esc(it["name"]), count_tag
            )
        )
    return "\n".join(parts)

def slugify(title: str, max_len: int = 70) -> str:
    nfd = unicodedata.normalize("NFD", title)
    result = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    result = result.lower()
    result = re.sub(r"[^a-z0-9א-תװ-״]+", "-", result)
    result = result.strip("-")
    result = re.sub(r"-+", "-", result)
    return result[:max_len].rstrip("-")


def ep_filename(ep: dict, ch_slug: str = "") -> str:
    slug = slugify(ep.get("title", "")) or ep.get("video_id", "episode")
    if ch_slug:
        prefix = ch_slug + "-"
        if slug.startswith(prefix):
            slug = slug[len(prefix):] or ep.get("video_id", "episode")
    return f"{slug}-{ep['published'][:10]}.html"


def ep_path(ch_slug: str, ep: dict) -> str:
    return f"{ch_slug}/{ep_filename(ep, ch_slug)}"


def js_str(s) -> str:
    """A safe JS string literal (also escapes `<` so `</script>` can never end
    the inline block)."""
    return json.dumps("" if s is None else str(s), ensure_ascii=False).replace("<", "\u003c")


def esc(s):
    return _html.escape(str(s), quote=True)


def img_chain(sources: list[str], prefix: str = "") -> str:
    """Render src/data-q/onerror attributes for an ordered <img> fallback list.

    Channel artwork exists in two sizes: `artwork/<slug>.png` is the 3000x3000
    Apple Podcasts master (0.5-5 MB apiece) that the RSS `itunes:image` needs,
    and `artwork/thumb/<slug>.webp` is the 256 px variant built by
    scripts/build_artwork_thumbs.py. Small display slots must take the thumbnail
    and keep the master only as a fallback (a channel added between two pipeline
    runs has no thumbnail yet), hence a chain rather than a single onerror.

    Each failure pops the next candidate off data-q; the last one stops the
    chain. `prefix` prepends a relative path for pages nested one level down.
    """
    urls = [prefix + u if u.startswith("artwork/") else u for u in sources if u]
    rest = json.dumps(urls[1:]).replace('"', "&quot;")
    onerr = (
        "var q=JSON.parse(this.dataset.q||'[]');"
        "if(q.length){this.src=q.shift();this.dataset.q=JSON.stringify(q)}"
        "else{this.onerror=null}"
    )
    return f'src="{esc(urls[0] if urls else "")}" data-q="{rest}" onerror="{onerr}"'

def fmt_date(iso, lang):
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if lang == "he":
            return f"{d.day}.{d.month:02d}.{d.year}"
        return f"{d.day} {MONTHS_FR[d.month - 1]} {d.year}"
    except Exception:
        return iso[:10]


# Auto-caption files start with a "Kind: captions Language: fr" metadata header
# (kept by fetch_transcripts.py's VTT flattening). Same regex as
# scripts/build_transcript_index.py so display, SEO and search agree.
TRANSCRIPT_HEADER_RE = re.compile(
    r"^\s*Kind:\s*captions\s+Language:\s*\S+\s*", re.IGNORECASE
)


def clean_transcript(text: str) -> str:
    """Remove the caption metadata header and normalise whitespace."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", TRANSCRIPT_HEADER_RE.sub("", text)).strip()


# A transcript's script is a property of the audio, not of the UI language:
# a French-speaking visitor can open a Hebrew shiur. The page-level dir follows
# the UI (applyLang), so the transcript needs its own dir to avoid a Hebrew
# body rendered left-to-right with the punctuation flipped to the wrong end.
HEBREW_CHAR_RE = re.compile("[֐-׿]")
LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def is_rtl_text(text: str, sample: int = 2000) -> bool:
    """True when the sampled head of `text` is predominantly Hebrew."""
    head = text[:sample]
    letters = len(LETTER_RE.findall(head))
    if not letters:
        return False
    return len(HEBREW_CHAR_RE.findall(head)) / letters > 0.5


MAX_WORDS_PER_PARA = 90


def _split_by_words(text: str, size: int = MAX_WORDS_PER_PARA) -> list[str]:
    words = text.split()
    return [" ".join(words[i:i + size]) for i in range(0, len(words), size)]


def transcript_paragraphs(text: str, sentences_per_para: int = 4) -> list[str]:
    """Split a flat auto-caption blob into readable paragraphs.

    Auto-captions are often unpunctuated, so sentence grouping is only a first
    pass: any chunk that stays too long is cut again on a word budget.
    """
    if not text:
        return []
    # Keep the punctuation with the sentence it closes.
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", text) if p.strip()]
    if not parts:
        parts = [text]
    grouped = [
        " ".join(parts[i:i + sentences_per_para])
        for i in range(0, len(parts), sentences_per_para)
    ]
    out: list[str] = []
    for chunk in grouped:
        if len(chunk.split()) > MAX_WORDS_PER_PARA * 1.5:
            out.extend(_split_by_words(chunk))
        else:
            out.append(chunk)
    return out


def render_transcript_html(text: str) -> str:
    """Escaped <p> blocks for the transcript panel under the player."""
    return "".join(f"<p>{esc(p)}</p>" for p in transcript_paragraphs(text))


# --- SEO (chantier B): make the episode page carry real, crawlable text ------
# The transcript panel added by chantier D lives in a collapsed <details>, which
# search engines de-prioritise. We additionally surface a short readable extract
# above the fold and derive the meta description from the transcript whenever
# the YouTube description is promotional boilerplate (emoji/links/hashtags).

EXTRACT_WORDS = 130          # visible lead extract under the player
# Below this a transcript file is a no-captions placeholder or a few stray
# words, not an indexable body (see update_sitemap).
MIN_TRANSCRIPT_BYTES = 400
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2190-\u21FF\u2600-\u27BF\uFE00-\uFE0F]"
)
_URL_RE = re.compile(r"https?://\S+|www\.\S+")


def desc_text_score(desc: str) -> int:
    """Letters left in a YouTube description once links/emoji/hashtags are gone.

    Many channels reuse the same promo block on every upload, which produces
    thousands of identical meta descriptions. A low score means the description
    carries no episode-specific text and the transcript is a better source.
    """
    if not desc:
        return 0
    stripped = _EMOJI_RE.sub(" ", _URL_RE.sub(" ", desc))
    stripped = re.sub(r"[#@]\S+", " ", stripped)
    return sum(1 for c in stripped if c.isalpha())


def seo_snippet(text: str, limit: int = 155) -> str:
    """Trim to `limit` chars on a word boundary (no mid-word cuts in SERPs)."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut[:cut.rindex(" ")]
    return cut.rstrip(" ,;:.-") + "…"


# Auto-captions almost never open on the actual class: they start with a music
# cue, applause, a chanted jingle or a bare greeting ("[Musique] Bircat chalom
# à tous"). Taking the first words verbatim shipped that noise as the visible
# lead AND as <meta name="description"> on thousands of pages, so the lead
# skips the non-informative opening paragraphs instead.
_CUE_RE = re.compile(r"[\[\(][^\[\]\(\)]{0,40}[\]\)]|[♪♫]")
MIN_LEAD_WORDS = 12          # a shorter paragraph is a cue/greeting, not content
_JINGLE_RATIO = 0.55         # distinct/total words below this = chanted jingle


def strip_cues(text: str) -> str:
    """Drop bracketed caption cues ([Musique], [Applaudissements], ♪)."""
    return re.sub(r"\s+", " ", _CUE_RE.sub(" ", text or "")).strip()


def _is_chant(words: list[str]) -> bool:
    """True for a sung/repeated passage rather than speech."""
    if not words:
        return True
    lowered = [w.lower().strip(".,;:!?…\"'") for w in words]
    if len(set(lowered)) / len(lowered) < _JINGLE_RATIO:
        return True
    # A word chanted three times in a row ("spéciale spéciale spéciale") is a
    # sung intro, never speech.
    return any(
        lowered[i] and lowered[i] == lowered[i + 1] == lowered[i + 2]
        for i in range(len(lowered) - 2)
    )


def _is_filler(para: str) -> bool:
    """True for an opening paragraph that carries no episode-specific content."""
    words = strip_cues(para).split()
    return len(words) < MIN_LEAD_WORDS or _is_chant(words)


MIN_LEAD_SENTENCE_WORDS = 6  # "Bonjour à tous." is a greeting, not the class


def _strip_lead_filler_sentences(text: str) -> str:
    """Drop the cues/greetings/chants the class opens on.

    Paragraphs group several sentences, so a jingle short enough to share a
    paragraph with the actual class has to be cut at sentence level first.
    """
    sentences = [s for s in re.split(r"(?<=[.!?…])\s+", text) if s.strip()]
    for i, sentence in enumerate(sentences):
        words = strip_cues(sentence).split()
        if len(words) < MIN_LEAD_SENTENCE_WORDS or _is_chant(words):
            continue
        return " ".join(sentences[i:])
    return text


def informative_paragraphs(text: str) -> list[str]:
    """Transcript paragraphs with the filler intro dropped and cues stripped.

    Only *leading* filler is skipped: a music cue in the middle of a class is
    part of the flow, while the ones at the top are the channel's jingle.
    """
    out: list[str] = []
    for para in transcript_paragraphs(_strip_lead_filler_sentences(text)):
        if not out and _is_filler(para):
            continue
        cleaned = strip_cues(para)
        if cleaned:
            out.append(cleaned)
    return out


def transcript_extract(text: str, max_words: int = EXTRACT_WORDS) -> str:
    """First `max_words` words of real content, cut on a paragraph boundary.

    Falls back to the raw paragraphs when every paragraph looks like filler
    (short clips, chanted classes) so a page never loses its lead entirely.
    """
    paras = informative_paragraphs(text) or transcript_paragraphs(text)
    out, used = [], 0
    for para in paras:
        n = len(para.split())
        if out and used + n > max_words:
            break
        out.append(para)
        used += n
        if used >= max_words:
            break
    return " ".join(out)


def iso_duration(secs) -> str:
    """schema.org ISO-8601 duration, e.g. 3725 -> PT1H2M5S."""
    try:
        secs = int(secs or 0)
    except (TypeError, ValueError):
        return ""
    if secs <= 0:
        return ""
    h, rem = divmod(secs, 3600)
    m, sec = divmod(rem, 60)
    return "PT" + (f"{h}H" if h else "") + (f"{m}M" if m else "") + (f"{sec}S" if sec else "")


def fmt_dur(secs):
    if not secs or int(secs) <= 0:
        return ""
    h, remainder = divmod(int(secs), 3600)
    m = remainder // 60
    if h:
        return f"{h}h{m:02d}" if m else f"{h}h"
    return f"{m} min"


PLATFORM_META = {
    "spotify": (
        '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/></svg>',
        "Spotify", "btn-spotify",
    ),
    "apple": (
        '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm0 4.3a7.7 7.7 0 0 1 5.44 13.14c-.37.37-.79.69-1.25.94-.1-.76-.28-1.63-.58-2.35.52-.48.87-1.15.87-1.9 0-1.43-1.16-2.59-2.59-2.59s-2.59 1.16-2.59 2.59c0 .75.32 1.42.83 1.9-.3.72-.48 1.59-.58 2.35a7.68 7.68 0 0 1-1.25-.94A7.7 7.7 0 0 1 12 4.3zm0 2.1a5.6 5.6 0 0 0-3.96 9.56c.18-.83.46-1.74.9-2.45a4.15 4.15 0 0 1-.54-2.03A3.6 3.6 0 0 1 12 7.9a3.6 3.6 0 0 1 3.6 3.58 4.15 4.15 0 0 1-.54 2.03c.44.71.72 1.62.9 2.45A5.6 5.6 0 0 0 12 6.4zm0 3.1a2 2 0 1 1 0 4 2 2 0 0 1 0-4zm0 5.6c1.05 0 1.82.28 2.3.55-.26 1.18-.38 2.28-.32 3.03a7.65 7.65 0 0 1-1.98.26 7.65 7.65 0 0 1-1.98-.26c.06-.75-.06-1.85-.32-3.03.48-.27 1.25-.55 2.3-.55z"/></svg>',
        "Apple Podcasts", "btn-apple",
    ),
    "deezer": (
        '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M18.944 17.369h4.512v1.601h-4.512zm0-3.075h4.512v1.6h-4.512zm0-3.074h4.512v1.601h-4.512zm-5.944 6.15h4.512v1.6H13zm0-3.075h4.512v1.6H13zm0-3.074h4.512v1.601H13zm-5.944 6.149h4.512v1.6H7.056zm0-3.075h4.512v1.6H7.056zM1.112 17.37h4.512v1.6H1.112z"/></svg>',
        "Deezer", "btn-deezer",
    ),
}

CSS = """\
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, -apple-system, sans-serif; background: #f5f5f0; color: #222; line-height: 1.5; padding-bottom: 60px; }
    header { background: #1a1a2e; color: #fff; padding: 28px 20px 0; text-align: center; }
    .site-brand { font-size: 2rem; font-weight: 700; margin-bottom: 6px; }
    .site-brand a { color: inherit; text-decoration: none; }
    header p { color: #aab; font-size: .95rem; margin-bottom: 14px; }
    .header-nav { display: flex; justify-content: center; flex-wrap: wrap; gap: 6px; padding: 0 16px 20px; }
    .header-nav a { color: rgba(255,255,255,.65); text-decoration: none; font-size: .8rem; padding: 5px 14px; border-radius: 20px; border: 1px solid rgba(255,255,255,.2); transition: background .15s, color .15s; }
    .header-nav a:hover { color: #fff; background: rgba(255,255,255,.1); border-color: rgba(255,255,255,.5); }
    .header-nav a.active { color: #1a1a2e; background: #fff; border-color: #fff; font-weight: 600; }
    .nav-dropdown { position: relative; display: inline-flex; }
    .nav-dd-link { color: rgba(255,255,255,.65); text-decoration: none; font-size: .8rem; padding: 5px 4px 5px 14px; border-radius: 20px 0 0 20px; border: 1px solid rgba(255,255,255,.2); border-right: none; transition: background .15s, color .15s; }
    .nav-dd-link:hover { color: #fff; background: rgba(255,255,255,.1); border-color: rgba(255,255,255,.5); }
    .nav-dd-caret { color: rgba(255,255,255,.65); font-size: .8rem; padding: 5px 10px 5px 6px; border-radius: 0 20px 20px 0; border: 1px solid rgba(255,255,255,.2); border-left: none; background: none; cursor: pointer; font-family: inherit; transition: background .15s, color .15s; }
    .nav-dd-caret:hover, .nav-dropdown.open .nav-dd-caret { color: #fff; background: rgba(255,255,255,.1); border-color: rgba(255,255,255,.5); }
    .nav-dd-link.active { color: #1a1a2e; background: #fff; border-color: #fff; font-weight: 600; }
    .nav-dd-link.active + .nav-dd-caret { color: #1a1a2e; background: #fff; border-color: #fff; }
    .nav-submenu { display: none; position: absolute; top: calc(100% + 8px); left: 50%; transform: translateX(-50%); background: #252545; border: 1px solid rgba(255,255,255,.12); border-radius: 12px; padding: 10px; z-index: 300; box-shadow: 0 8px 28px rgba(0,0,0,.5); flex-wrap: wrap; gap: 4px; min-width: 340px; max-width: 92vw; }
    .nav-dropdown.open .nav-submenu { display: flex; }
    .nav-submenu a { color: rgba(255,255,255,.78); text-decoration: none; padding: 6px 12px; border-radius: 20px; font-size: .78rem; white-space: nowrap; border: 1px solid rgba(255,255,255,.15); transition: background .12s, color .12s; }
    .nav-submenu a:hover { background: rgba(255,255,255,.12); color: #fff; border-color: rgba(255,255,255,.35); }
    .nav-submenu .nav-submenu-all { color: rgba(255,255,255,.5); border-style: dashed; font-size: .74rem; margin-top: 4px; width: 100%; text-align: center; }
        .lang-switch { display: inline-flex; gap: 2px; margin: 6px auto 14px; padding: 2px; border: 1px solid rgba(255,255,255,.2); border-radius: 20px; background: rgba(255,255,255,.05); }
    .lang-opt { color: rgba(255,255,255,.6); font-size: .75rem; font-weight: 600; padding: 4px 11px; border-radius: 16px; border: none; background: none; cursor: pointer; font-family: inherit; letter-spacing: .02em; transition: background .15s, color .15s; }
    .lang-opt:hover { color: #fff; }
    .lang-opt.active { background: #e87722; color: #fff; }
    main { max-width: 860px; margin: 0 auto; padding: 24px 16px 40px; }
    .ch-card { background: #fff; border-radius: 14px; box-shadow: 0 1px 6px rgba(0,0,0,.08); display: flex; align-items: flex-start; gap: 20px; padding: 20px 24px; margin-bottom: 28px; }
    .ch-art { width: 80px; height: 80px; border-radius: 10px; object-fit: cover; flex-shrink: 0; background: #e0e0e0; }
    .ch-name { font-size: 1.4rem; font-weight: 700; margin-bottom: 4px; }
    .ch-count { font-size: .82rem; color: #888; margin-bottom: 12px; }
    .platform-links { display: flex; flex-wrap: wrap; gap: 8px; }
    .platform-btn { display: inline-flex; align-items: center; gap: 6px; padding: 5px 13px; border-radius: 20px; font-size: .78rem; font-weight: 600; text-decoration: none; transition: opacity .15s; white-space: nowrap; }
    .platform-btn:hover { opacity: .82; }
    .platform-btn svg { width: 14px; height: 14px; flex-shrink: 0; }
    .btn-spotify { background: #1DB954; color: #fff; }
    .btn-apple   { background: #872EC4; color: #fff; }
    .btn-deezer  { background: #EF5466; color: #fff; }
    .btn-rss     { background: #f5f5f0; color: #555; border: 1px solid #ddd; }
    .header-stats { font-size: .88rem; color: #9ab; margin-bottom: 12px; min-height: 1.3em; }
    .ch-about { background: #fff; border-radius: 12px; box-shadow: 0 1px 4px rgba(0,0,0,.07); padding: 20px 24px; margin-bottom: 4px; font-size: .9rem; color: #444; line-height: 1.7; position: relative; }
    .ch-about p { margin-bottom: .9em; }
    .ch-about p:last-child { margin-bottom: 0; }
    .ch-about.collapsed { max-height: 6em; overflow: hidden; }
    .ch-about.collapsed::after { content: ''; position: absolute; bottom: 0; left: 0; right: 0; height: 2.5em; background: linear-gradient(transparent, #fff); pointer-events: none; }
    .show-more-btn { background: none; border: none; color: #e87722; font-size: .82rem; font-weight: 600; cursor: pointer; padding: 0 0 20px; display: none; font-family: inherit; }
    .show-more-btn.visible { display: block; }
    .show-more-btn:hover { text-decoration: underline; }
    .section-label { font-size: .75rem; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; color: #999; margin-bottom: 12px; }
    .dur-filter { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:14px; }
    .dur-btn { background:#fff; border:1px solid #ddd; border-radius:16px; padding:4px 13px; font-size:.76rem; cursor:pointer; color:#555; transition:all .15s; font-family:inherit; }
    .dur-btn:hover { border-color:#aaa; color:#333; }
    .dur-btn.active { background:#1a1a2e; color:#fff; border-color:#1a1a2e; }
    .episode-list { display: flex; flex-direction: column; gap: 2px; }
    .episode { background: #fff; border-radius: 10px; display: flex; gap: 14px; padding: 13px 18px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
    .episode[hidden] { display: none !important; }
    .ep-thumb { width: 80px; height: 54px; object-fit: cover; border-radius: 6px; flex-shrink: 0; background: #ddd; }
    .ep-thumb-ph { width: 80px; height: 54px; border-radius: 6px; flex-shrink: 0; background: #e8e8e8; }
    .ep-body { flex: 1; min-width: 0; }
    .ep-title { font-size: .88rem; font-weight: 600; margin-bottom: 2px; }
    .ep-date { font-size: .73rem; color: #888; display: block; margin-bottom: 5px; }
    .ep-desc { font-size: .78rem; color: #555; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; margin-bottom: 8px; }
    .ep-audio { width: 100%; height: 32px; accent-color: #e87722; margin-top: 4px; }
    @media (max-width: 500px) {
      .ch-card { flex-direction: column; }
      .ep-thumb, .ep-thumb-ph { display: none; }
    }
    .speed-bar { display:flex; align-items:center; gap:6px; margin-bottom:16px; font-size:.78rem; color:#888; flex-wrap:wrap; }
    .speed-btn { background:none; border:1px solid #ddd; color:#777; border-radius:4px; padding:3px 9px; font-size:.72rem; cursor:pointer; font-family:inherit; transition:background .12s,color .12s,border-color .12s; }
    .speed-btn:hover,.speed-btn.active { background:#1a1a2e; color:#fff; border-color:#1a1a2e; }
    .ep-actions { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-top:4px; }
    .ep-actions .ep-audio { flex:1; min-width:200px; }
    .share-btn { display:inline-flex; align-items:center; gap:5px; background:none; border:1px solid #ddd; border-radius:20px; padding:5px 10px; font-size:.72rem; color:#888; cursor:pointer; font-family:inherit; transition:background .12s,border-color .12s,color .12s; }
    .share-btn:hover { background:#f5f5f0; border-color:#bbb; color:#444; }
    .site-footer { background: #1a1a2e; color: rgba(255,255,255,.5); text-align: center; padding: 20px 16px; font-size: .78rem; margin-top: 40px; }
    .site-footer a { color: rgba(255,255,255,.65); text-decoration: none; margin: 0 8px; }
    .site-footer a:hover { color: #fff; }
    .toast { position:fixed; bottom:20px; left:50%; transform:translateX(-50%); background:#1a1a2e; color:#fff; padding:8px 18px; border-radius:20px; font-size:.82rem; z-index:500; opacity:0; transition:opacity .2s; pointer-events:none; white-space:nowrap; box-shadow:0 4px 14px rgba(0,0,0,.3); }
    .toast.show { opacity:1; }
    .ep-extract { margin-top:20px; padding:16px 18px; background:#fbfaf7; border-inline-start:3px solid #e87722; border-radius:8px; }
    .ep-extract h2 { font-size:.78rem; font-weight:700; text-transform:uppercase; letter-spacing:.08em; color:#999; margin:0 0 8px; }
    .ep-extract p { margin:0; font-size:.88rem; color:#444; line-height:1.75; }
    .ep-extract-more { margin-top:10px !important; font-size:.78rem !important; }
    .ep-extract-more a { color:#e87722; text-decoration:none; font-weight:600; }
    .ep-extract-more a:hover { text-decoration:underline; }
    details.transcript { margin-top:20px; border:1px solid #e8e8e8; border-radius:10px; overflow:hidden; }
    details.transcript summary { padding:10px 16px; font-size:.78rem; font-weight:600; color:#555; cursor:pointer; background:#fafafa; list-style:none; display:flex; align-items:center; gap:6px; }
    details.transcript summary::-webkit-details-marker { display:none; }
    details.transcript summary::before { content:'▶'; font-size:.6rem; color:#999; transition:transform .2s; }
    details.transcript[open] summary::before { transform:rotate(90deg); }
    details.transcript summary:hover { background:#f5f5f0; }
    .transcript-count { margin-inline-start:auto; font-weight:400; color:#999; font-size:.72rem; }
    .transcript-copy { background:none; border:1px solid #ddd; border-radius:14px; padding:3px 10px; font-size:.7rem; font-family:inherit; color:#666; cursor:pointer; transition:background .15s,border-color .15s; }
    .transcript-copy:hover { background:#fff; border-color:#e87722; color:#e87722; }
    .transcript-body[dir="rtl"] { text-align:right; }
    .transcript-note { margin:0; padding:10px 18px 0; font-size:.7rem; color:#999; font-style:italic; }
    .transcript-body { padding:14px 18px; font-size:.82rem; color:#555; line-height:1.75; word-break:break-word; max-height:460px; overflow-y:auto; }
    .transcript-body p { margin:0 0 12px; }
    .transcript-body p:last-child { margin-bottom:0; }
    .play-btn { display:inline-flex; align-items:center; gap:6px; background:#1a1a2e; color:#fff; border:none; border-radius:20px; padding:5px 13px; font-size:.76rem; cursor:pointer; transition:background .15s; font-family:inherit; }
    .play-btn:hover { background:#2d2d50; }
    .play-btn.playing { background:#e87722; }
    .play-btn svg { width:10px; height:10px; flex-shrink:0; }
    /* The bottom player (markup + CSS + controls) lives in js/utils.js. */
    .btn-embed { background:#f5f5f0; color:#555; border:1px solid #ddd; }
    .btn-embed:hover { background:#eee; border-color:#bbb; }
    .embed-modal { display:none; position:fixed; inset:0; z-index:500; align-items:center; justify-content:center; background:rgba(0,0,0,.45); padding:16px; }
    .embed-modal.open { display:flex; }
    .embed-box { background:#fff; border-radius:16px; padding:24px; max-width:460px; width:100%; box-shadow:0 12px 48px rgba(0,0,0,.3); }
    .embed-box-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:14px; }
    .embed-box-header h3 { font-size:.95rem; font-weight:700; }
    .embed-close { background:none; border:none; color:#aaa; font-size:1.3rem; cursor:pointer; line-height:1; padding:0; }
    .embed-close:hover { color:#333; }
    .embed-code { background:#f5f5f0; border-radius:8px; padding:12px; font-family:monospace; font-size:.72rem; color:#333; white-space:pre-wrap; word-break:break-all; margin-bottom:12px; border:1px solid #e0e0e0; user-select:all; cursor:text; }
    .embed-actions { display:flex; gap:8px; align-items:center; }
    .embed-copy-btn { background:#1a1a2e; color:#fff; border:none; border-radius:8px; padding:8px 18px; font-size:.82rem; cursor:pointer; font-family:inherit; transition:background .15s; }
    .embed-copy-btn:hover { background:#2d2d50; }
    .embed-preview-label { font-size:.72rem; color:#999; margin:14px 0 6px; font-weight:600; text-transform:uppercase; letter-spacing:.04em; }
    .embed-iframe-wrap { border:1px solid #e0e0e0; border-radius:10px; overflow:hidden; height:200px; }
    .embed-iframe-wrap iframe { width:100%; height:100%; border:none; transform:scale(.6); transform-origin:top left; width:167%; height:167%; pointer-events:none; }
    /* Mobile: keep dropdown submenus centered under the nav and within the viewport (fixes off-screen/unclickable items on phones) */
    @media (max-width: 600px) {
      .header-nav { position: relative; }
      .nav-dropdown { position: static; }
      .nav-submenu { left: 50%; right: auto; transform: translateX(-50%); min-width: 0; max-width: calc(100vw - 24px); width: max-content; }
    }"""

GTAG = """\
  <link rel="preconnect" href="https://www.googletagmanager.com">
  <link rel="preconnect" href="https://pub-a5fae25ce5124edebe0bf7393f72823c.r2.dev" crossorigin>
  <!-- <audio> requests are not CORS, so they use a different connection pool than
       the crossorigin preconnect above. This second one is the one that actually
       saves the R2 TLS handshake before the first play. -->
  <link rel="preconnect" href="https://pub-a5fae25ce5124edebe0bf7393f72823c.r2.dev">
  <!-- Google tag (gtag.js) -->
  <script async src="https://www.googletagmanager.com/gtag/js?id=G-7Z2QEN865Y"></script>
  <script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','G-7Z2QEN865Y');</script>"""


def load_channel_info(slug: str) -> dict:
    path = FEEDS_DIR / f"{slug}.channel_info.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def render_page(ch: dict, entries: list, all_channels: list,
                site_channels: int = 0, site_episodes: int = 0, site_hours: int = 0) -> str:
    slug      = ch["slug"]
    pslug     = url_slug(slug)  # lowercase canonical URL slug (feeds/dirs keep `slug`)
    name      = ch["podcast_author"]
    lang      = ch.get("podcast_language", "fr")
    platforms = ch.get("platforms", {})
    ep_count  = len(entries)

    channel_info    = load_channel_info(slug)
    yt_description  = (channel_info.get("description") or "").strip()

    default_lang = lang  # channel's native language is the default

    fallback_desc_fr = f"Écoutez tous les cours de Torah du {name}. {ep_count} épisodes disponibles sur Spotify, Apple Podcasts et Deezer."
    fallback_desc_he = f"האזינו לשיעורי התורה של {name}. {ep_count} פרקים זמינים בפודקאסט."

    seo_description  = (channel_info.get("seo_description") or "").strip()
    page_description = (channel_info.get("page_description") or "").strip()
    description      = seo_description or (yt_description[:155] if yt_description else (fallback_desc_he if lang == "he" else fallback_desc_fr))

    # Platform buttons
    btns = []
    for key, (icon, label, cls) in PLATFORM_META.items():
        url = platforms.get(key, "").strip()
        if url:
            btns.append(
                f'<a class="platform-btn {cls}" href="{esc(url)}" target="_blank" rel="noopener">'
                f'{icon}{label}</a>'
            )
    if not ch.get("speaker"):
        rss_url = ch.get("rss_url") or f"{BASE_URL}/feeds/{slug}.xml"
        btns.append(f'<a class="platform-btn btn-rss" href="{esc(rss_url)}" target="_blank" rel="noopener">RSS</a>')
        btns.append(f'<button class="platform-btn btn-embed" onclick="openEmbedModal()">⊞ Intégrer</button>')
    platform_html = "\n        ".join(btns)

    # Static episode list
    sorted_entries = sorted(entries, key=lambda x: x["published"], reverse=True)
    _date_title_re = re.compile(r"^\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}$|^\d{1,2}\s+\w+\s+\d{4}$")
    ep_parts = []
    for ep in sorted_entries:
        if _date_title_re.match((ep.get("title") or "").strip()):
            continue  # LOG-04: skip episodes whose title is just a date
        thumb     = ep.get("thumbnail", "")
        _raw = (ep.get("description") or "").strip()
        _raw = re.sub(r"^[Dd]escription\s*:\s*", "", _raw)
        desc_raw = (_raw[:200].rstrip() + "…") if len(_raw) > 200 else _raw
        audio_url = ep.get("audio_url", "")

        thumb_tag = (
            f'<a href="{esc(ep_path(slug, ep))}"><img class="ep-thumb" src="{esc(thumb)}" alt="{esc(ep["title"])}" loading="lazy"></a>'
            if thumb else '<div class="ep-thumb-ph"></div>'
        )
        desc_tag  = f'<p class="ep-desc">{esc(desc_raw)}</p>' if desc_raw else ""
        video_id  = ep.get("video_id", "")
        audio_tag = (
            f'<button class="play-btn" data-ep-id="{esc(video_id)}" '
            f'data-audio="{esc(audio_url)}" data-title="{esc(ep["title"])}" data-thumb="{esc(thumb)}">'
            f'<svg viewBox="0 0 10 10" fill="currentColor"><polygon points="2,1 9,5 2,9"/></svg> Écouter</button>'
            if audio_url else ""
        )
        share_tag = (
            f'<button class="share-btn" data-epfile="{esc(ep_path(slug, ep))}" data-title="{esc(ep["title"])}">'
            f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" width="11" height="11">'
            f'<path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/>'
            f'<polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/></svg>'
            f' Partager</button>'
            if ep.get("title") and ep.get("published") else ""
        )
        dur_secs = ep.get("duration_secs", 0) or 0
        ep_parts.append(
            # data-ep-lang lets the shared course-language filter (js/utils.js)
            # hide the classes of the other language on a MIXED channel page
            # (Rav-benizri: 1 133 he / 515 fr, Nahal-Haim: 175 he / 199 fr).
            f'    <article class="episode" data-dur="{dur_secs}" data-ep-lang="{episode_lang(ep, ch)}">\n'
            f'      {thumb_tag}\n'
            f'      <div class="ep-body">\n'
            f'        <a class="ep-title" href="{esc(ep_path(slug, ep))}" style="color:inherit;text-decoration:none;display:block">{esc(ep["title"])}</a>\n'
            f'        <time class="ep-date" datetime="{ep["published"][:10]}">{fmt_date(ep["published"], lang)}{" - " + fmt_dur(ep.get("duration_secs",0)) if ep.get("duration_secs") else ""}</time>\n'
            f'        {desc_tag}\n'
            f'        <div class="ep-actions">{audio_tag}{share_tag}</div>\n'
            f'      </div>\n'
            f'    </article>'
        )
    episodes_html = "\n".join(ep_parts)

    # JSON-LD PodcastSeries schema
    ep_schema = []
    for ep in sorted_entries[:30]:
        item = {
            "@type": "PodcastEpisode",
            "name": ep["title"],
            "datePublished": ep["published"][:10],
        }
        if ep.get("audio_url"):
            item["associatedMedia"] = {
                "@type": "MediaObject",
                "contentUrl": ep["audio_url"],
            }
        ep_schema.append(item)

    schema = {
        "@context": "https://schema.org",
        "@type": "PodcastSeries",
        "name": name,
        "description": description,
        "url": f"{BASE_URL}/{pslug}.html",
        "webFeed": f"{BASE_URL}/feeds/{slug}.xml",
        "image": f"{BASE_URL}/artwork/{slug}.png",
        "inLanguage": ["fr", "he"],
        "author": {"@type": "Person", "name": name},
        "publisher": SITE_PUBLISHER,
        "episode": ep_schema,
    }
    schema_json = json.dumps(schema, ensure_ascii=False, indent=2)

    if page_description:
        paras = "".join(
            f"<p>{esc(p.strip())}</p>"
            for p in page_description.splitlines()
            if p.strip()
        )
        about_block = (
            f'<div class="ch-about" id="ch-about">{paras}</div>'
            f'<button class="show-more-btn" id="show-more-btn"></button>'
        )
    else:
        about_block = ""

    submenu_links = render_nav_submenu("")

    return f"""<!DOCTYPE html>
<html lang="{default_lang}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(name)} — Cours de Torah en podcast — The Torah Podcast</title>
  <meta name="description" content="{esc(description)}">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{BASE_URL}/{pslug}.html">
  <link rel="alternate" hreflang="fr" href="{BASE_URL}/{pslug}.html">
  <link rel="alternate" hreflang="he" href="{BASE_URL}/{pslug}.html">
  <link rel="alternate" hreflang="x-default" href="{BASE_URL}/{pslug}.html">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{BASE_URL}/{pslug}.html">
  <meta property="og:title" content="{esc(name)} — The Torah Podcast">
  <meta property="og:description" content="{esc(description)}">
  <meta property="og:image" content="{BASE_URL}/artwork/{slug}.png">
  <meta property="og:site_name" content="The Torah Podcast">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:locale:alternate" content="he_IL">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{esc(name)} — The Torah Podcast">
  <meta name="twitter:description" content="{esc(description)}">
  <meta name="twitter:image" content="{BASE_URL}/artwork/{slug}.png">
  <link rel="manifest" href="/manifest.json">
  <meta name="theme-color" content="#1a1a2e">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <link rel="apple-touch-icon" href="/artwork/{slug}.png">
  <script type="application/ld+json">
{schema_json}
  </script>
{GTAG}
  <style>
{CSS}
  </style>
</head>
<body>
<header>
  <p class="site-brand"><a href="./">The Torah Podcast</a></p>
  <p data-i18n="subtitle">Cours de Torah — disponibles sur vos plateformes favorites</p>
  <div class="header-stats" id="header-stats"></div>
  <nav class="header-nav">
    <a href="./" data-i18n="nav_home">Accueil</a>
    <div class="nav-dropdown" id="nav-dropdown">
      <a class="nav-dd-link active" href="rabbins.html" data-i18n="nav_rabbis">Rabbins</a><button class="nav-dd-caret" aria-label="Voir la liste">▾</button>
      <div class="nav-submenu">
{submenu_links}
        <a class="nav-submenu-all" href="rabbins.html">Tous les rabbins →</a>
      </div>
    </div>
    <a href="derniers-cours.html" data-i18n="nav_last_classes">Derniers cours</a>
    <div class="nav-dropdown">
      <a class="nav-dd-link" href="daf-hayomi.html" data-i18n="nav_limud">Limud Yomi</a><button class="nav-dd-caret" aria-label="Limud Yomi">▾</button>
      <div class="nav-submenu" style="min-width:170px;flex-direction:column;">
        <a href="daf-hayomi.html" data-i18n="nav_daf_hayomi">Daf Hayomi</a>
        <a href="hitat.html" data-i18n="nav_hitat">Hitat Yomi</a>
        <a href="hayom-yom.html" data-i18n="nav_hayomyom">Hayom Yom</a>
        <a href="hiloula.html" data-i18n="nav_hiloula">Hiloula</a>
      </div>
    </div>
    <a href="paracha.html" data-i18n="nav_paracha">Paracha</a>
    <a href="themes.html" data-i18n="nav_themes">Thème</a>
    <a href="mes-favoris.html" data-i18n="nav_favorites">Mes favoris</a>
  </nav>
  <div class="lang-switch" role="group" aria-label="Language">
    <button type="button" class="lang-opt" data-lang="fr" onclick="setLang('fr')">FR</button>
    <button type="button" class="lang-opt" data-lang="en" onclick="setLang('en')">EN</button>
    <button type="button" class="lang-opt" data-lang="he" onclick="setLang('he')">עב</button>
  </div>
</header>
<main>
  <div class="ch-card">
    <!-- 80 px header slot: the 256 px thumbnail, never the 3000x3000 master
         (which stays reserved for the RSS itunes:image). See img_chain(). -->
    <img class="ch-art" {img_chain([ch.get('thumbnail', ''), f'artwork/thumb/{slug}.webp', f'artwork/{slug}.png'])} alt="{esc(name)}">
    <div>
      <h1 class="ch-name">{esc(name)}<span id="ch-fav-slot" data-slug="{slug}"></span></h1>
      <p class="ch-count" id="ep-count"></p>
      <div class="platform-links">
        {platform_html}
      </div>
    </div>
  </div>
  {about_block}
  <h2 class="section-label" data-i18n="all_episodes">Tous les épisodes</h2>
  <div class="dur-filter">
    <button class="dur-btn active" data-dur="all" onclick="filterDur(this)">Tous</button>
    <button class="dur-btn" data-dur="short" onclick="filterDur(this)">&lt; 5 min</button>
    <button class="dur-btn" data-dur="medium" onclick="filterDur(this)">5–20 min</button>
    <button class="dur-btn" data-dur="long" onclick="filterDur(this)">&gt; 20 min</button>
  </div>
  <div class="episode-list" id="ep-list">
{episodes_html}
  </div>
  <div class="toast" id="toast"></div>
</main>
<div class="embed-modal" id="embed-modal">
  <div class="embed-box">
    <div class="embed-box-header">
      <h3>Intégrer ce podcast</h3>
      <button class="embed-close" id="embed-close" aria-label="Fermer">✕</button>
    </div>
    <div class="embed-code" id="embed-code">&lt;iframe src="https://thetorahpodcast.net/embed.html?slug={slug}" width="380" height="560" frameborder="0" allow="autoplay" style="border-radius:12px"&gt;&lt;/iframe&gt;</div>
    <div class="embed-actions">
      <button class="embed-copy-btn" id="embed-copy-btn">Copier le code</button>
    </div>
    <div class="embed-preview-label">Aperçu</div>
    <div class="embed-iframe-wrap">
      <iframe src="https://thetorahpodcast.net/embed.html?slug={slug}" title="Aperçu embed" loading="lazy"></iframe>
    </div>
  </div>
</div>
<!-- The bottom player is injected by utils.js (single shared source). -->
<script src="js/utils.js"></script>
<script>
  const I18N = {{
    fr: {{
      nav_home:'Accueil', nav_rabbis:'Rabbins ▾', nav_last_classes:'Derniers cours', nav_daf_hayomi:'Daf Hayomi', nav_limud:'Limud Yomi', nav_hitat:'Hitat Yomi', nav_hayomyom:'Hayom Yom', nav_hiloula:'Hiloula', nav_paracha:'Paracha', nav_themes:'Thème', nav_favorites:'Mes favoris',
      lang_toggle:'English', subtitle:'Cours de Torah — disponibles sur vos plateformes favorites',
      all_episodes:'Tous les épisodes',
      ep_count: n => `${{n}} épisode${{n !== 1 ? 's' : ''}}`,
      listen:'Écouter', playing:'En cours…',
    }},
    en: {{
      nav_home:'Home', nav_rabbis:'Rabbis ▾', nav_last_classes:'Latest classes', nav_daf_hayomi:'Daf Hayomi', nav_limud:'Limud Yomi', nav_hitat:'Hitat Yomi', nav_hayomyom:'Hayom Yom', nav_hiloula:'Hiloula', nav_paracha:'Parasha', nav_themes:'Topics', nav_favorites:'My favorites',
      lang_toggle:'עברית', subtitle:'Torah classes — available on your favorite platforms',
      all_episodes:'All episodes',
      ep_count: n => `${{n}} episode${{n !== 1 ? 's' : ''}}`,
      listen:'Listen', playing:'Playing…',
    }},
    he: {{
      nav_home:'ראשי', nav_rabbis:'הרבנים ▾', nav_last_classes:'שיעורים אחרונים', nav_daf_hayomi:'דף היומי', nav_limud:'לימוד יומי', nav_hitat:'חת"ת', nav_hayomyom:'היום יום', nav_hiloula:'הילולה', nav_paracha:'פרשה', nav_themes:'נושא', nav_favorites:'המועדפים שלי',
      lang_toggle:'Français', subtitle:'שיעורי תורה — זמינים בפלטפורמות האהובות עליכם',
      all_episodes:'כל הפרקים',
      ep_count: n => `${{n}} פרקים`,
      listen:'האזן', playing:'מתנגן…',
    }},
  }};
  let lang = localStorage.getItem('lang') || '{default_lang}';
  function t(k) {{ const d = I18N[lang] || {{}}; return d[k] || I18N.fr[k] || k; }}
  function applyLang() {{
    document.querySelectorAll('.lang-opt').forEach(function(b){{ b.classList.toggle('active', b.dataset.lang === lang); }});
    document.documentElement.lang = lang;
    document.documentElement.dir  = lang === 'he' ? 'rtl' : 'ltr';
    document.querySelectorAll('[data-i18n]').forEach(el => {{
      el.textContent = t(el.dataset.i18n);
    }});
    const epCount = document.getElementById('ep-count');
    if (epCount) epCount.textContent = I18N[lang].ep_count({ep_count});
    const statsEl = document.getElementById('header-stats');
    if (statsEl) statsEl.textContent = lang === 'he'
      ? `{site_channels} ערוצים · {site_episodes} שיעורים · ~{site_hours} שעות`
      : `{site_channels} rabbins · {site_episodes} cours · ~{site_hours}h de Torah`;
  }}
  function filterDur(btn) {{
    document.querySelectorAll('.dur-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const filter = btn.dataset.dur;
    document.querySelectorAll('#ep-list .episode').forEach(ep => {{
      const d = parseInt(ep.dataset.dur) || 0;
      if (filter === 'all')    {{ ep.hidden = false; return; }}
      if (filter === 'short')  {{ ep.hidden = !(d > 0 && d < 300); return; }}
      if (filter === 'medium') {{ ep.hidden = !(d >= 300 && d <= 1200); return; }}
      if (filter === 'long')   {{ ep.hidden = !(d > 1200); return; }}
    }});
  }}
  // Show more / less for channel description
  (function() {{
    const about = document.getElementById('ch-about');
    const btn   = document.getElementById('show-more-btn');
    if (!about || !btn) return;
    if (about.scrollHeight > 100) {{
      about.classList.add('collapsed');
      btn.classList.add('visible');
      btn.textContent = lang === 'he' ? 'הצג עוד ▾' : (lang === 'en' ? 'Show more ▾' : 'Voir plus ▾');
      btn.addEventListener('click', () => {{
        const isCollapsed = about.classList.toggle('collapsed');
        btn.textContent = isCollapsed
          ? (lang === 'he' ? 'הצג עוד ▾' : (lang === 'en' ? 'Show more ▾' : 'Voir plus ▾'))
          : (lang === 'he' ? 'הצג פחות ▴' : (lang === 'en' ? 'Show less ▴' : 'Voir moins ▴'));
      }});
    }}
  }})();
  function setLang(l) {{ if (l === lang) return; localStorage.setItem('lang', l); location.reload(); }}
  applyLang();
  // Favorite star (localStorage only — no backend). Populated at load so it
  // reflects the current localStorage state; toggle logic lives in utils.js.
  (function() {{
    const slot = document.getElementById('ch-fav-slot');
    if (slot && typeof favStarHtml === 'function') slot.innerHTML = favStarHtml(slot.dataset.slug, 'on-channel');
  }})();
  // Floating player — the bar itself (markup, CSS, play/pause, +-15/+30, speed,
  // progress hairline, resume memory, Media Session) lives in js/utils.js.
  const playerAudio = TTPPlayer.audio();
  let currentEpId   = null;
  function loadInPlayer(btn) {{
    const epId  = btn.dataset.epId;
    document.querySelectorAll('.play-btn').forEach(b => b.classList.remove('playing'));
    if (currentEpId === epId && TTPPlayer.isPlaying()) {{
      TTPPlayer.audio().pause();
      currentEpId = null;
      return;
    }}
    currentEpId = epId;
    btn.classList.add('playing');
    TTPPlayer.load({{
      id: epId, title: btn.dataset.title, channel: '{esc(name)}',
      art: btn.dataset.thumb || '', src: btn.dataset.audio
    }});
  }}
  document.addEventListener('click', e => {{
    const playBtn = e.target.closest('.play-btn[data-ep-id]');
    if (playBtn) {{ loadInPlayer(playBtn); return; }}
    if (e.target.closest('.nav-submenu')) return;
    const caret = e.target.closest('.nav-dd-caret');
    document.querySelectorAll('.nav-dropdown.open').forEach(el => {{
      if (!caret || el !== caret.closest('.nav-dropdown')) el.classList.remove('open');
    }});
    if (caret) caret.closest('.nav-dropdown').classList.toggle('open');
  }});
  // Speed, resume position and closing are owned by TTPPlayer; the page only
  // keeps its own list buttons in sync with the bar.
  if (playerAudio) {{
    ['ended', 'pause'].forEach(ev => playerAudio.addEventListener(ev, () => {{
      if (!TTPPlayer.isPlaying()) {{
        document.querySelectorAll('.play-btn').forEach(b => b.classList.remove('playing'));
        if (playerAudio.ended) currentEpId = null;
      }}
    }}));
  }}
  const playerCloseBtn = document.getElementById('player-close');
  if (playerCloseBtn) playerCloseBtn.addEventListener('click', () => {{
    document.querySelectorAll('.play-btn').forEach(b => b.classList.remove('playing'));
    currentEpId = null;
  }});
  // GA4
  if (typeof gtag !== 'undefined') {{
    const ga4Played = {{}}, ga4Completed = {{}};
    if (playerAudio) {{
      playerAudio.addEventListener('play', () => {{
        if (currentEpId && !ga4Played[currentEpId]) {{
          ga4Played[currentEpId] = true;
          gtag('event', 'audio_play', {{ep_title: (document.getElementById('player-title') || {{}}).textContent || '', rav: '{esc(name)}'}});
        }}
      }});
      playerAudio.addEventListener('timeupdate', () => {{
        if (currentEpId && !ga4Completed[currentEpId] && playerAudio.duration > 0 && playerAudio.currentTime / playerAudio.duration >= 0.9) {{
          ga4Completed[currentEpId] = true;
          gtag('event', 'audio_complete', {{ep_title: (document.getElementById('player-title') || {{}}).textContent || '', rav: '{esc(name)}'}});
        }}
      }});
    }}
    document.querySelectorAll('.platform-btn').forEach(function(btn) {{
      btn.addEventListener('click', function() {{
        var cls = Array.from(btn.classList).find(function(c) {{ return c.startsWith('btn-') && c !== 'btn-rss'; }});
        gtag('event', 'click_platform', {{platform: cls ? cls.replace('btn-', '') : 'rss', rav: '{esc(name)}'}});
      }});
    }});
  }}
  // Toast
  function showToast(msg) {{
    const t = document.getElementById('toast');
    t.textContent = msg; t.classList.add('show');
    clearTimeout(t._timer); t._timer = setTimeout(() => t.classList.remove('show'), 2000);
  }}
  // Share
  document.addEventListener('click', e => {{
    const btn = e.target.closest('.share-btn[data-epfile]');
    if (!btn) return;
    const url = `https://thetorahpodcast.net/${{btn.dataset.epfile}}`;
    if (navigator.share) navigator.share({{title: btn.dataset.title, url}});
    else navigator.clipboard.writeText(url).then(() => showToast('Lien copié !'));
  }});
  // Embed modal
  function openEmbedModal() {{ document.getElementById('embed-modal').classList.add('open'); }}
  document.getElementById('embed-close').addEventListener('click', () => {{
    document.getElementById('embed-modal').classList.remove('open');
  }});
  document.getElementById('embed-modal').addEventListener('click', e => {{
    if (e.target === e.currentTarget) e.currentTarget.classList.remove('open');
  }});
  document.getElementById('embed-copy-btn').addEventListener('click', () => {{
    const code = document.getElementById('embed-code').textContent;
    navigator.clipboard.writeText(code).then(() => {{
      const btn = document.getElementById('embed-copy-btn');
      btn.textContent = '✓ Copié !';
      setTimeout(() => {{ btn.textContent = 'Copier le code'; }}, 2000);
    }});
  }});
</script>
<script>if('serviceWorker'in navigator)navigator.serviceWorker.register('/sw.js');</script>
</body>
</html>
"""


def render_episode_page(ep: dict, ch: dict, all_entries: list, all_channels: list) -> str:
    slug     = ch["slug"]
    pslug    = url_slug(slug)  # lowercase canonical URL slug for links to the show page
    name     = ch["podcast_author"]
    lang     = ch.get("podcast_language", "fr")
    video_id = ep.get("video_id", "")
    title    = ep.get("title", "")
    pub      = ep.get("published", "")[:10]
    audio    = ep.get("audio_url", "")
    thumb    = ep.get("thumbnail", "")
    desc     = (ep.get("description") or "").strip()
    tags     = ep.get("tags") or []
    ep_slug  = ep_filename(ep, slug)

    ep_platform_links = ep.get("platform_links", {})
    ch_platforms = ch.get("platforms", {})
    ep_platform_btns = []
    for key, (icon, label, cls) in PLATFORM_META.items():
        # Episode-specific URL first, fall back to show page
        url = ep_platform_links.get(key, "").strip() or ch_platforms.get(key, "").strip()
        if url:
            ep_platform_btns.append(
                f'<a class="platform-btn {cls}" href="{esc(url)}" target="_blank" rel="noopener">'
                f'{icon}{label}</a>'
            )
    ep_platform_html = (
        f'<div class="platform-links" style="margin-top:12px">{"".join(ep_platform_btns)}</div>'
        if ep_platform_btns else ""
    )

    others  = [e for e in all_entries if e.get("video_id") != video_id and e.get("audio_url")]
    related = sorted(others, key=lambda x: x.get("published", ""), reverse=True)[:5]

    related_parts = []
    for r in related:
        r_vid = r.get("video_id", "")
        r_thumb = r.get("thumbnail", "")
        r_thumb_tag = (
            f'<a href="{esc(ep_filename(r, slug))}"><img class="ep-thumb" src="{esc(r_thumb)}" alt="{esc(r["title"])}" loading="lazy"></a>'
            if r_thumb else '<div class="ep-thumb-ph"></div>'
        )
        related_parts.append(
            f'<article class="episode">'
            f'{r_thumb_tag}'
            f'<div class="ep-body">'
            f'<a class="ep-title" href="{esc(ep_filename(r, slug))}" style="color:inherit;text-decoration:none;display:block">{esc(r["title"])}</a>'
            f'<time class="ep-date" datetime="{r["published"][:10]}">{fmt_date(r["published"], lang)}'
            + (f' · {fmt_dur(r.get("duration_secs", 0))}' if r.get('duration_secs') else '') + '</time>'
            f'<div class="ep-actions">'
            f'<button class="play-btn" data-ep-id="{esc(r_vid)}" data-audio="{esc(r["audio_url"])}"'
            f' data-title="{esc(r["title"])}" data-thumb="{esc(r_thumb)}">'
            f'<svg viewBox="0 0 10 10" fill="currentColor" width="10" height="10">'
            f'<polygon points="2,1 9,5 2,9"/></svg> Écouter</button>'
            f'<button class="share-btn" data-epfile="{esc(ep_path(slug, r))}" data-title="{esc(r["title"])}">'
            f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" width="11" height="11">'
            f'<path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/>'
            f'<polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/></svg>'
            f' Partager</button></div>'
            f'</div></article>'
        )
    related_html = "\n".join(related_parts)

    tags_html = " ".join(f'<span class="ep-tag">{esc(t)}</span>' for t in tags) if tags else ""

    schema = {
        "@context": "https://schema.org",
        "@type": "PodcastEpisode",
        "name": title,
        "datePublished": pub,
        "url": f"{BASE_URL}/{ep_path(slug, ep)}",
        "partOfSeries": {
            "@type": "PodcastSeries",
            "name": name,
            "url": f"{BASE_URL}/{pslug}.html",
        },
        "author": {"@type": "Person", "name": name},
        "publisher": SITE_PUBLISHER,
        "description": desc[:500] if desc else f"Épisode de {name}",
    }
    if audio:
        schema["associatedMedia"] = {"@type": "MediaObject", "contentUrl": audio}
    if thumb:
        schema["image"] = thumb
    schema["inLanguage"] = lang
    _dur_iso = iso_duration(ep.get("duration_secs"))
    if _dur_iso:
        schema["duration"] = _dur_iso

    breadcrumb_schema = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Accueil", "item": f"{BASE_URL}/"},
            {"@type": "ListItem", "position": 2, "name": name, "item": f"{BASE_URL}/{pslug}.html"},
            {"@type": "ListItem", "position": 3, "name": title, "item": f"{BASE_URL}/{ep_path(slug, ep)}"},
        ],
    }
    breadcrumb_json = json.dumps(breadcrumb_schema, ensure_ascii=False, indent=2)

    submenu_links = render_nav_submenu("../")

    transcript_path = FEEDS_DIR / "transcripts" / f"{video_id}.txt"
    transcript = (
        clean_transcript(transcript_path.read_text(encoding="utf-8"))
        if transcript_path.exists() else ""
    )

    # Prefer the YouTube description only when it actually says something about
    # this episode; otherwise the transcript is the better (and unique) source.
    extract = transcript_extract(transcript) if transcript else ""
    # `extract` is already the jingle-free head of the transcript, so it is the
    # right source for the SERP snippet too (the raw transcript would put the
    # music cue back in <meta description>).
    seo_desc_src = desc if desc_text_score(desc) >= 120 else (extract or transcript or desc)
    seo_desc = (
        seo_snippet(seo_desc_src)
        if seo_desc_src
        else f"Écoutez {title} — cours de {name} sur The Torah Podcast."
    )
    og_locale = "he_IL" if lang == "he" else "fr_FR"
    og_locale_alt = "fr_FR" if lang == "he" else "he_IL"
    og_image = thumb if thumb else f"{BASE_URL}/artwork/{slug}.png"
    # The native <audio controls> widget is gone: playback happens in the shared
    # bottom bar (js/utils.js), so the controls look and behave the same on every
    # page and in every browser. `preload="metadata"` — and only on this one
    # element, never on the related list — lets the duration and the R2
    # connection be ready before the first tap.
    audio_tag = (
        f'<audio id="ep-audio" src="{esc(audio)}" preload="metadata" data-ep-id="{esc(video_id)}"'
        f' style="display:none"></audio>'
        f'<button type="button" id="ep-play-main" class="ep-play-main">'
        f'<svg viewBox="0 0 10 10" fill="currentColor" width="12" height="12" aria-hidden="true">'
        f'<polygon points="2,1 9,5 2,9"/></svg>'
        f'<span id="ep-play-label" data-i18n="ep_play">Écouter le cours</span></button>'
        if audio else ""
    )
    thumb_tag = (
        f'<img src="{esc(thumb)}" alt="{esc(title)}"'
        f' style="width:100%;max-width:480px;border-radius:10px;margin-bottom:16px;object-fit:cover">'
        if thumb else ""
    )
    # Visible lead extract: real crawlable text above the collapsed panel.
    extract_block = ""
    # The transcript's own script drives its direction, independently of the UI
    # language chosen by the visitor (see is_rtl_text).
    tr_dir = ' dir="rtl" lang="he"' if is_rtl_text(transcript) else ""
    if extract:
        extract_block = (
            '<section class="ep-extract">'
            '<h2 data-i18n="extract_title">Extrait du cours</h2>'
            f'<p{tr_dir}>{esc(extract)}</p>'
            '<p class="ep-extract-more"><a href="#transcript" data-i18n="extract_more">'
            'Lire la transcription complète ↓</a></p>'
            '</section>'
        )

    if transcript:
        # Signals that this page holds a real, machine-readable text body.
        schema["abstract"] = seo_snippet(extract or transcript, 300)
        schema["wordCount"] = len(transcript.split())
        schema["description"] = seo_desc
    schema_json = json.dumps(schema, ensure_ascii=False, indent=2)

    transcript_block = ""
    if transcript:
        transcript_words = len(transcript.split())
        transcript_block = (
            '<details class="transcript" id="transcript">'
            '<summary><span data-i18n="transcript_label">Transcription</span>'
            f'<span class="transcript-count">{transcript_words} '
            '<span data-i18n="transcript_words">mots</span></span>'
            '<button type="button" class="transcript-copy" id="transcript-copy"'
            ' data-i18n="transcript_copy">Copier</button></summary>'
            '<p class="transcript-note" data-i18n="transcript_auto">'
            'Transcription automatique — peut contenir des erreurs.</p>'
            f'<div class="transcript-body"{tr_dir}>{render_transcript_html(transcript)}</div>'
            '</details>'
        )

    desc_tag  = f'<p style="font-size:.9rem;color:#444;line-height:1.7;white-space:pre-line;margin-top:16px">{esc(desc)}</p>' if desc else ""
    breadcrumb_title = (title[:60] + "…") if len(title) > 60 else title
    # JS string literals for the metadata handed to the shared bottom player.
    title_js = js_str(title)
    name_js  = js_str(name)
    thumb_js = js_str(thumb)

    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(title)} — {esc(name)} — The Torah Podcast</title>
  <meta name="description" content="{esc(seo_desc)}">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{BASE_URL}/{ep_path(slug, ep)}">
  <link rel="alternate" hreflang="fr" href="{BASE_URL}/{ep_path(slug, ep)}">
  <link rel="alternate" hreflang="he" href="{BASE_URL}/{ep_path(slug, ep)}">
  <link rel="alternate" hreflang="x-default" href="{BASE_URL}/{ep_path(slug, ep)}">
  <meta property="og:type" content="article">
  <meta property="og:url" content="{BASE_URL}/{ep_path(slug, ep)}">
  <meta property="og:title" content="{esc(title)} — The Torah Podcast">
  <meta property="og:description" content="{esc(seo_desc)}">
  <meta property="og:image" content="{esc(og_image)}">
  <meta property="og:site_name" content="The Torah Podcast">
  <meta property="og:locale" content="{og_locale}">
  <meta property="og:locale:alternate" content="{og_locale_alt}">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{esc(title)} — The Torah Podcast">
  <meta name="twitter:description" content="{esc(seo_desc)}">
  <meta name="twitter:image" content="{esc(og_image)}">
  <link rel="manifest" href="/manifest.json">
  <meta name="theme-color" content="#1a1a2e">
  <link rel="apple-touch-icon" href="/artwork/{slug}.png">
  <script type="application/ld+json">
{schema_json}
  </script>
  <script type="application/ld+json">
{breadcrumb_json}
  </script>
{GTAG}
  <style>
{CSS}
  .ep-hero {{ background:#fff; border-radius:14px; box-shadow:0 1px 6px rgba(0,0,0,.08); padding:24px; margin-bottom:24px; }}
  .ep-hero h1 {{ font-size:1.3rem; font-weight:700; margin-bottom:8px; line-height:1.4; }}
  .ep-meta {{ font-size:.82rem; color:#888; margin-bottom:12px; }}
  .ep-tag {{ display:inline-block; background:#f0f0e8; color:#666; border-radius:20px; padding:2px 10px; font-size:.72rem; margin:2px 2px 10px 0; }}
  .breadcrumb {{ font-size:.8rem; color:#888; margin-bottom:20px; }}
  .breadcrumb a {{ color:#888; text-decoration:none; }}
  .breadcrumb a:hover {{ text-decoration:underline; }}
  .related-label {{ font-size:.75rem; font-weight:700; text-transform:uppercase; letter-spacing:.08em; color:#999; margin:28px 0 12px; }}
  .ep-play-main {{ display:inline-flex; align-items:center; justify-content:center; gap:9px; min-height:46px;
    padding:0 22px; margin-bottom:16px; background:#e87722; color:#fff; border:none; border-radius:24px;
    font-family:inherit; font-size:.92rem; font-weight:600; cursor:pointer; transition:background .15s; }}
  .ep-play-main:hover {{ background:#f2872f; }}
  .ep-play-main:focus-visible {{ outline:2px solid #1a1a2e; outline-offset:2px; }}
  .ep-play-main.is-playing {{ background:#1a1a2e; }}
  </style>
</head>
<body>
<header>
  <p class="site-brand"><a href="../">The Torah Podcast</a></p>
  <p data-i18n="subtitle">Cours de Torah — disponibles sur vos plateformes favorites</p>
  <nav class="header-nav">
    <a href="../" data-i18n="nav_home">Accueil</a>
    <div class="nav-dropdown" id="nav-dropdown">
      <a class="nav-dd-link" href="../rabbins.html" data-i18n="nav_rabbis">Rabbins</a><button class="nav-dd-caret" aria-label="Voir la liste">▾</button>
      <div class="nav-submenu">
{submenu_links}
        <a class="nav-submenu-all" href="../rabbins.html">Tous les rabbins →</a>
      </div>
    </div>
    <a href="../derniers-cours.html" data-i18n="nav_last_classes">Derniers cours</a>
    <div class="nav-dropdown">
      <a class="nav-dd-link" href="../daf-hayomi.html" data-i18n="nav_limud">Limud Yomi</a><button class="nav-dd-caret" aria-label="Limud Yomi">▾</button>
      <div class="nav-submenu" style="min-width:170px;flex-direction:column;">
        <a href="../daf-hayomi.html" data-i18n="nav_daf_hayomi">Daf Hayomi</a>
        <a href="../hitat.html" data-i18n="nav_hitat">Hitat Yomi</a>
        <a href="../hayom-yom.html" data-i18n="nav_hayomyom">Hayom Yom</a>
        <a href="../hiloula.html" data-i18n="nav_hiloula">Hiloula</a>
      </div>
    </div>
    <a href="../paracha.html" data-i18n="nav_paracha">Paracha</a>
    <a href="../themes.html" data-i18n="nav_themes">Thème</a>
    <a href="../mes-favoris.html" data-i18n="nav_favorites">Mes favoris</a>
  </nav>
  <div class="lang-switch" role="group" aria-label="Language">
    <button type="button" class="lang-opt" data-lang="fr" onclick="setLang('fr')">FR</button>
    <button type="button" class="lang-opt" data-lang="en" onclick="setLang('en')">EN</button>
    <button type="button" class="lang-opt" data-lang="he" onclick="setLang('he')">עב</button>
  </div>
</header>
<main>
  <p class="breadcrumb"><a href="../">Accueil</a> › <a href="../{pslug}.html">{esc(name)}</a> › {esc(breadcrumb_title)}</p>
  <div class="ep-hero">
    {thumb_tag}
    <h1>{esc(title)}</h1>
    <p class="ep-meta"><a href="../{pslug}.html" style="color:#888;text-decoration:none">{esc(name)}</a> · <time datetime="{pub}">{fmt_date(ep["published"], lang)}{" · " + fmt_dur(ep.get("duration_secs",0)) if ep.get("duration_secs") else ""}</time></p>
    {f'<div style="margin-bottom:8px">{tags_html}</div>' if tags_html else ''}
    {audio_tag}
    <div class="speed-bar">
      <span>Vitesse :</span>
      <button class="speed-btn active" data-speed="1">1×</button>
      <button class="speed-btn" data-speed="1.25">1.25×</button>
      <button class="speed-btn" data-speed="1.5">1.5×</button>
      <button class="speed-btn" data-speed="2">2×</button>
    </div>
    <div id="ep-share" data-epfile="{esc(ep_path(slug, ep))}" data-title="{esc(title)}"
         data-vid="{esc(video_id)}" data-ch="{esc(name)}" data-thumb="{esc(thumb)}" data-date="{pub}"
         style="margin-top:10px"></div>
    {ep_platform_html}
    {desc_tag}
    {extract_block}
    {transcript_block}
  </div>
  {f'<p class="related-label" data-i18n="related">Épisodes récents</p><div class="episode-list">{related_html}</div>' if related_html else ''}
  <div class="toast" id="toast"></div>
</main>
<script src="../js/utils.js"></script>
<script>
  const I18N = {{
    fr: {{
      nav_home:'Accueil', nav_rabbis:'Rabbins ▾', nav_last_classes:'Derniers cours', nav_daf_hayomi:'Daf Hayomi', nav_limud:'Limud Yomi', nav_hitat:'Hitat Yomi', nav_hayomyom:'Hayom Yom', nav_hiloula:'Hiloula', nav_paracha:'Paracha', nav_themes:'Thème', nav_favorites:'Mes favoris',
      lang_toggle:'English', subtitle:'Cours de Torah — disponibles sur vos plateformes favorites',
      related:'Épisodes récents', extract_title:'Extrait du cours', extract_more:'Lire la transcription complète ↓', transcript_label:'Transcription', transcript_words:'mots', transcript_auto:'Transcription automatique — peut contenir des erreurs.', transcript_copy:'Copier', transcript_copied:'Copié !', transcript_copy_error:'Copie impossible', ep_play:'Écouter le cours', ep_pause:'Mettre en pause',
    }},
    en: {{
      nav_home:'Home', nav_rabbis:'Rabbis ▾', nav_last_classes:'Latest classes', nav_daf_hayomi:'Daf Hayomi', nav_limud:'Limud Yomi', nav_hitat:'Hitat Yomi', nav_hayomyom:'Hayom Yom', nav_hiloula:'Hiloula', nav_paracha:'Parasha', nav_themes:'Topics', nav_favorites:'My favorites',
      lang_toggle:'עברית', subtitle:'Torah classes — available on your favorite platforms',
      related:'Recent episodes', extract_title:'Class excerpt', extract_more:'Read the full transcript ↓', transcript_label:'Transcript', transcript_words:'words', transcript_auto:'Automatic transcript — may contain errors.', transcript_copy:'Copy', transcript_copied:'Copied!', transcript_copy_error:'Copy failed', ep_play:'Play the class', ep_pause:'Pause',
    }},
    he: {{
      nav_home:'ראשי', nav_rabbis:'הרבנים ▾', nav_last_classes:'שיעורים אחרונים', nav_daf_hayomi:'דף היומי', nav_limud:'לימוד יומי', nav_hitat:'חת"ת', nav_hayomyom:'היום יום', nav_hiloula:'הילולה', nav_paracha:'פרשה', nav_themes:'נושא', nav_favorites:'המועדפים שלי',
      lang_toggle:'Français', subtitle:'שיעורי תורה — זמינים בפלטפורמות האהובות עליכם',
      related:'פרקים אחרונים', extract_title:'קטע מהשיעור', extract_more:'לקריאת התמליל המלא ↓', transcript_label:'תמליל', transcript_words:'מילים', transcript_auto:'תמליל אוטומטי — עלול להכיל שגיאות.', transcript_copy:'העתקה', transcript_copied:'הועתק!', transcript_copy_error:'ההעתקה נכשלה', ep_play:'האזנה לשיעור', ep_pause:'השהיה',
    }},
  }};
  let lang = localStorage.getItem('lang') || '{lang}';
  function t(k) {{ const d = I18N[lang] || {{}}; return d[k] || I18N.fr[k] || k; }}
  function applyLang() {{
    document.querySelectorAll('.lang-opt').forEach(function(b){{ b.classList.toggle('active', b.dataset.lang === lang); }});
    document.documentElement.lang = lang;
    document.documentElement.dir  = lang === 'he' ? 'rtl' : 'ltr';
    document.querySelectorAll('[data-i18n]').forEach(el => {{ el.textContent = t(el.dataset.i18n); }});
  }}
  function setLang(l) {{ if (l === lang) return; localStorage.setItem('lang', l); location.reload(); }}
  applyLang();
  document.addEventListener('click', e => {{
    if (e.target.closest('.nav-submenu')) return;
    const caret = e.target.closest('.nav-dd-caret');
    document.querySelectorAll('.nav-dropdown.open').forEach(el => {{
      if (!caret || el !== caret.closest('.nav-dropdown')) el.classList.remove('open');
    }});
    if (caret) caret.closest('.nav-dropdown').classList.toggle('open');
  }});
  // Speed: one truth, shared with the bottom bar's own control.
  let currentSpeed = TTPPlayer.speed();
  document.querySelectorAll('.speed-btn').forEach(b => {{
    b.classList.toggle('active', parseFloat(b.dataset.speed) === currentSpeed);
    b.addEventListener('click', () => {{ currentSpeed = TTPPlayer.setSpeed(parseFloat(b.dataset.speed)); }});
  }});
  // The class plays IN THE BAR, which is present on every page — so opening an
  // episode no longer replaces the bar with an isolated widget.
  const mainAudio = document.getElementById('ep-audio');
  const epMeta = {{
    id: '{video_id}', title: {title_js}, channel: {name_js}, art: {thumb_js},
    src: mainAudio ? mainAudio.getAttribute('src') : ''
  }};
  if (mainAudio) {{
    const mainBtn = document.getElementById('ep-play-main');
    const paintMain = () => {{
      if (!mainBtn) return;
      const on = !mainAudio.paused && !mainAudio.ended;
      mainBtn.classList.toggle('is-playing', on);
      const lbl = document.getElementById('ep-play-label');
      if (lbl) lbl.textContent = on ? t('ep_pause') : t('ep_play');
    }};
    if (mainBtn) mainBtn.addEventListener('click', () => {{ TTPPlayer.toggle(mainAudio, epMeta); }});
    // Any other way in (the "Reprendre à mm:ss" banner, a keyboard shortcut)
    // still surfaces the bar.
    mainAudio.addEventListener('play', () => {{ TTPPlayer.attach(mainAudio, epMeta); paintMain(); }});
    ['pause', 'ended'].forEach(ev => mainAudio.addEventListener(ev, paintMain));
    if (typeof attachResumeBanner === 'function') attachResumeBanner(mainAudio, '{video_id}');
  }}
  // Related episodes: same bar, no second widget.
  document.addEventListener('click', e => {{
    const b = e.target.closest('.play-btn[data-ep-id]');
    if (!b) return;
    if (TTPPlayer.audio() === mainAudio && mainAudio && !mainAudio.paused) mainAudio.pause();
    TTPPlayer.load({{ id: b.dataset.epId, title: b.dataset.title, channel: {name_js},
                     art: b.dataset.thumb || '', src: b.dataset.audio }});
  }});
  // Share row (WhatsApp-first) + favorite star, built from the shared lib.
  (function () {{
    const host = document.getElementById('ep-share');
    if (!host || typeof buildShareRow !== 'function') return;
    const url = `{BASE_URL}/${{host.dataset.epfile}}`;
    const meta = {{ title: host.dataset.title, ch: host.dataset.ch, url: url,
                    thumb: host.dataset.thumb, date: host.dataset.date }};
    const star = document.createElement('span');
    star.innerHTML = favEpStarHtml(host.dataset.vid, meta, 'on-channel');
    const row = buildShareRow(url, host.dataset.title);
    row.appendChild(star.firstChild);
    host.appendChild(row);
  }})();
  function showToast(msg) {{
    const t = document.getElementById('toast');
    t.textContent = msg; t.classList.add('show');
    clearTimeout(t._timer); t._timer = setTimeout(() => t.classList.remove('show'), 2000);
  }}
  document.addEventListener('click', e => {{
    const btn = e.target.closest('.share-btn[data-epfile]');
    if (!btn) return;
    const url = `{BASE_URL}/${{btn.dataset.epfile}}`;
    if (navigator.share) navigator.share({{title: btn.dataset.title, url}});
    else navigator.clipboard.writeText(url).then(() => showToast('Lien copié !'));
  }});
  (function () {{
    const btn = document.getElementById('transcript-copy');
    const body = document.querySelector('.transcript-body');
    if (!btn || !body) return;
    btn.addEventListener('click', e => {{
      // The button lives inside <summary>: without this the click would also
      // toggle the panel open/closed.
      e.preventDefault(); e.stopPropagation();
      const text = Array.from(body.querySelectorAll('p'))
        .map(p => p.textContent.trim()).join('\n\n');
      navigator.clipboard.writeText(text).then(() => {{
        btn.textContent = t('transcript_copied');
        showToast(t('transcript_copied'));
        clearTimeout(btn._timer);
        btn._timer = setTimeout(() => {{ btn.textContent = t('transcript_copy'); }}, 2000);
      }}).catch(() => showToast(t('transcript_copy_error')));
    }});
  }})();
</script>
<script>if('serviceWorker'in navigator)navigator.serviceWorker.register('/sw.js');</script>
</body>
</html>
"""


HOME_RECENTS_COUNT = 20
_HITAT_RE = re.compile(r"HITAT DU JOUR", re.IGNORECASE)


def _last_class_fields(ep: dict | None, suffix: str = "") -> dict:
    """Flat `last_*` block describing ONE episode — the most recent of a rav.

    A rav with no episode at all yields every field at None (never an invented
    date or title): the consumer must be able to degrade instead of displaying
    something false. `last_title` is copied VERBATIM and untruncated — a class
    title is never normalised nor translated.

    `last_audio_url` is carried so the app can start playback straight from the
    rav panel without a second request, exactly like `audio_url` in recents.
    """
    if not ep:
        return {
            f"last_published{suffix}": None,
            f"last_title{suffix}": None,
            f"last_video_id{suffix}": None,
            f"last_audio_url{suffix}": None,
            f"last_duration_secs{suffix}": None,
        }
    return {
        f"last_published{suffix}": ep.get("published") or None,
        f"last_title{suffix}": ep.get("title") or "",
        f"last_video_id{suffix}": ep.get("video_id") or "",
        f"last_audio_url{suffix}": ep.get("audio_url") or "",
        f"last_duration_secs{suffix}": ep.get("duration_secs") or 0,
    }


def _last_class_block(pairs: list[tuple[dict, str]]) -> dict:
    """`last_*` (all languages) + `last_*_fr` / `last_*_he` for a bilingual rav.

    `pairs` = (episode, course language) — the same `episode_lang()` used for
    count_fr/count_he, so the "last class" shown under a language filter matches
    the count shown next to it.

    The per-language blocks are emitted ONLY when the rav actually teaches in
    both languages (15 of the 22 channels are mono-language): for a mono-language
    rav the suffixed block would be a byte-for-byte copy of the unsuffixed one,
    and home.json is loaded at startup by the app AND the homepage. Consumer
    rule: if `last_*_<lang>` is absent while `count_<lang> > 0`, the unsuffixed
    `last_*` IS that language's last class.

    Contrary to `recents`, HITAT DU JOUR is NOT filtered out: this mirrors
    latestEpisode() in rabbins.html, whose "Dernier cours" sort these fields are
    meant to feed.
    """
    key = lambda pair: pair[0].get("published") or ""
    out = dict(_last_class_fields(max(pairs, key=key)[0] if pairs else None))
    for lang in ("fr", "he"):
        same_lang = [p for p in pairs if p[1] == lang]
        if same_lang and len(same_lang) != len(pairs):
            out.update(_last_class_fields(max(same_lang, key=key)[0], f"_{lang}"))
    return out


def build_home_json(
    all_data: list[tuple],
    speakers: list[dict],
    entries_cache: dict,
    site_channels: int,
    site_episodes: int,
) -> None:
    """Pre-compute the small home.json the homepage consumes instead of fetching
    the ~11 MB of per-channel entries.json client-side.

    Mirrors the semantics index.html used to compute at runtime:
      - channel display name = podcast_author (enabled channels only)
      - recents = top HOME_RECENTS_COUNT episodes across all channels, HITAT DU JOUR
        excluded, sorted by `published` desc; URL identical to epUrl(ep, slug)
      - speakers = only those with >=1 matched episode; img = most-recent matched
        thumbnail (repImg)
      - stats.channels = channels + speakers count (matches renderStats today)
      - stats.episodes = total episodes across channels
      - every channel AND speaker entry also carries its LAST class
        (`last_published` / `last_title` / `last_video_id` / `last_audio_url` /
        `last_duration_secs`, + `_fr`/`_he` variants when the rav is bilingual —
        see _last_class_block), so the app can show it and sort by it without
        pulling the 22 entries.json feeds

    Every episode also carries `lang` (the language of the CLASS, not of the
    UI — scripts/lang_detect.py) and every precomputed total is doubled by a
    per-language breakdown (`count_fr`/`count_he`, `episodes_fr`/`episodes_he`),
    without which any language filter in the UI would display wrong counts.
    """
    # Every pre-computed total is also broken down per course language, because
    # the moment the UI filters on a language a single `count` is wrong (a rav
    # with 1 648 classes shows 515 to a French speaker). count_fr + count_he ==
    # count, always — detect_lang() only ever returns "fr" or "he".
    channels_out = []
    for ch, entries in all_data:
        lang_pairs = [(ep, episode_lang(ep, ch)) for ep in entries]
        per_lang = Counter(lang for _, lang in lang_pairs)
        entry = {
            "slug": ch["slug"],
            "name": ch["podcast_author"],
            "count": len(entries),
            "count_fr": per_lang["fr"],
            "count_he": per_lang["he"],
        }
        # Last class (date/title/audio) — without it the app cannot show the
        # rav panel nor offer the "Dernier cours" sort without pulling the 22
        # entries.json feeds (~20 MB); `recents` covers 12 slugs out of 22 and
        # can't stand in for it.
        entry.update(_last_class_block(lang_pairs))
        channels_out.append(entry)

    # Recents: flatten across channels, drop HITAT DU JOUR, sort newest-first.
    flat = []
    for ch, entries in all_data:
        for ep in entries:
            if _HITAT_RE.search(ep.get("title") or ""):
                continue
            flat.append((ch, ep))
    flat.sort(key=lambda ce: ce[1].get("published", ""), reverse=True)

    recents_out = []
    for ch, ep in flat[:HOME_RECENTS_COUNT]:
        recents_out.append({
            "slug": ch["slug"],
            "ch_name": ch["podcast_author"],
            "title": ep.get("title", ""),
            "published": ep.get("published", ""),
            "thumbnail": ep.get("thumbnail", ""),
            "audio_url": ep.get("audio_url", ""),
            "video_id": ep.get("video_id", ""),
            "url": ep_path(ch["slug"], ep),
            "duration_secs": ep.get("duration_secs", 0) or 0,
            "lang": episode_lang(ep, ch),
        })

    # Speakers with >=1 matched episode; repImg = most-recent matched thumbnail.
    speakers_out = []
    by_slug = {c["slug"]: c for c, _ in all_data}
    for sp in speakers:
        matched = []
        sp_pairs = []
        sp_lang = Counter()
        for ch_slug in sp.get("from_channels", []):
            for ep in entries_cache.get(ch_slug, []):
                if speaker_matches(ep.get("title", ""), sp["title_patterns"]):
                    lang = episode_lang(ep, by_slug.get(ch_slug))
                    matched.append(ep)
                    sp_pairs.append((ep, lang))
                    sp_lang[lang] += 1
        if not matched:
            continue
        matched.sort(key=lambda x: x.get("published", ""), reverse=True)
        rep_img = next((e.get("thumbnail") for e in matched if e.get("thumbnail")), "")
        # count_fr/count_he: without them the homepage cannot tell which of the
        # 15 speakers still teach in the language the visitor selected, and its
        # "N rabbins" would contradict rabbins.html (which loads the real
        # entries and can count exactly).
        sp_entry = {
            "slug": sp["slug"], "name": sp["name"], "img": rep_img,
            "count": len(matched),
            "count_fr": sp_lang["fr"],
            "count_he": sp_lang["he"],
        }
        # Same `last_*` contract as the channels above — guests are ravs like the
        # others in the app, and their feeds/<slug>.entries.json is derived from
        # these very episodes (write_speaker_feed).
        sp_entry.update(_last_class_block(sp_pairs))
        speakers_out.append(sp_entry)

    # Spotlight "Découvrez le rav" — mirror the social "Zoom Rabbi" round-robin so
    # the homepage features the SAME rav currently promoted on Facebook/Instagram.
    # social_state.json rabbi_index points at the NEXT rav to post, so the one being
    # promoted right now is index-1 (same channels.json enabled order social_post
    # uses). Rotation cadence = whenever the social workflow advances the index.
    spotlight = None
    try:
        st = json.loads(Path("social_state.json").read_text(encoding="utf-8-sig"))
        enabled = [c for c in json.loads(CHANNELS_FILE.read_text(encoding="utf-8-sig"))
                   if c.get("enabled", True)]
        if enabled:
            n = len(enabled)
            sp_ch = enabled[(int(st.get("rabbi_index", 0)) - 1) % n]
            sp_entries = entries_cache.get(sp_ch["slug"], [])
            eps = sorted(
                [e for e in sp_entries if not _HITAT_RE.search(e.get("title") or "")],
                key=lambda e: e.get("published", ""), reverse=True,
            )[:8]
            info_path = FEEDS_DIR / f"{sp_ch['slug']}.channel_info.json"
            info = {}
            if info_path.exists():
                try:
                    info = json.loads(info_path.read_text(encoding="utf-8-sig"))
                except Exception:
                    info = {}
            if eps:
                sp_lang = Counter(episode_lang(e, sp_ch) for e in sp_entries)
                spotlight = {
                    "slug": sp_ch["slug"],
                    "name": sp_ch["podcast_author"],
                    "count": len(sp_entries),
                    "count_fr": sp_lang["fr"],
                    "count_he": sp_lang["he"],
                    "description": (info.get("description") or "")[:280],
                    "episodes": [{
                        "slug": sp_ch["slug"],
                        "ch_name": sp_ch["podcast_author"],
                        "title": e.get("title", ""),
                        "published": e.get("published", ""),
                        "thumbnail": e.get("thumbnail", ""),
                        "audio_url": e.get("audio_url", ""),
                        "video_id": e.get("video_id", ""),
                        "url": ep_path(sp_ch["slug"], e),
                        "duration_secs": e.get("duration_secs", 0) or 0,
                        "lang": episode_lang(e, sp_ch),
                    } for e in eps],
                }
    except Exception:
        spotlight = None

    home = {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stats": {
            "channels": site_channels,
            "episodes": site_episodes,
            "episodes_fr": sum(c["count_fr"] for c in channels_out),
            "episodes_he": sum(c["count_he"] for c in channels_out),
        },
        "channels": channels_out,
        "speakers": speakers_out,
        "recents": recents_out,
        "spotlight": spotlight,
    }
    Path("home.json").write_text(
        json.dumps(home, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    print(f"  home.json  ({len(recents_out)} recents, {len(channels_out)} channels, {len(speakers_out)} speakers)")


def build_search_index(all_data: list[tuple]) -> None:
    """Pre-compute a minimal full-text search index over the WHOLE catalog.

    index.html lazy-loads this file (search-index.json) the first time the user
    focuses/types in the search box, so the homepage itself stays ~15 KB
    (home.json only) at load. Each entry carries ONLY what the search UI needs —
    no descriptions or other heavy fields — to keep the payload small:
      t = episode title, c = channel/rav display name, u = episode page URL
      (identical to ep_path(slug, ep)), d = published date (YYYY-MM-DD),
      l = course language ("fr"/"he", see scripts/lang_detect.py) so the search
      UI can filter results without a second lookup. `l` costs ~10 bytes per
      entry (+5 % on the file) and is emitted for every entry rather than for
      Hebrew only, so the consumer never has to guess a default.

    Unlike home.json (which drops HITAT DU JOUR from the recents row), the search
    index intentionally covers the ENTIRE catalog, HITAT included — every episode
    of every channel is searchable. The (title, published) guard mirrors the
    episode-page generation in main() so every URL here points to a page that
    actually exists.
    """
    index = []
    for ch, entries in all_data:
        ch_name = ch["podcast_author"]
        slug = ch["slug"]
        for ep in entries:
            title = ep.get("title") or ""
            published = ep.get("published") or ""
            if not title or not published:
                continue
            index.append({
                "t": title,
                "c": ch_name,
                "u": ep_path(slug, ep),
                "d": published[:10],
                "l": episode_lang(ep, ch),
            })
    Path("search-index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8", newline="\n",
    )
    print(f"  search-index.json  ({len(index)} episodes across {len(all_data)} channels)")


def update_sitemap(slug_entries: list[tuple]):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    slugs = [s for s, _ in slug_entries]
    channel_entries = "\n".join(
        f"  <url>\n"
        f"    <loc>{BASE_URL}/{url_slug(slug)}.html</loc>\n"
        f"    <lastmod>{today}</lastmod>\n"
        f"    <changefreq>daily</changefreq>\n"
        f"    <priority>0.9</priority>\n"
        f"  </url>"
        for slug in slugs
    )
    # Episodes backed by a transcript now carry real indexable text (chantier B),
    # so they get a higher crawl priority than audio-only stubs.
    # fetch_transcripts.py writes an EMPTY .txt when YouTube has no captions,
    # so the file is a "we tried" marker, not text: 9 846 of the 27 156 files
    # are 0 byte (measured 18/08/2026). Keying on existence advertised ~10 000
    # thin pages to crawlers at the high priority reserved for real content.
    tdir = FEEDS_DIR / "transcripts"
    with_text = (
        {f.stem for f in tdir.glob("*.txt") if f.stat().st_size >= MIN_TRANSCRIPT_BYTES}
        if tdir.is_dir() else set()
    )
    episode_entries = "\n".join(
        f"  <url>\n"
        f"    <loc>{BASE_URL}/{ep_path(slug, ep)}</loc>\n"
        f"    <lastmod>{ep['published'][:10]}</lastmod>\n"
        f"    <changefreq>monthly</changefreq>\n"
        f"    <priority>{'0.7' if ep.get('video_id') in with_text else '0.5'}</priority>\n"
        f"  </url>"
        for slug, entries in slug_entries
        for ep in entries
        if ep.get("title") and ep.get("published")
    )
    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        '  <url>\n'
        f'    <loc>{BASE_URL}/</loc>\n'
        f'    <lastmod>{today}</lastmod>\n'
        '    <changefreq>daily</changefreq>\n'
        '    <priority>1.0</priority>\n'
        '  </url>\n'
        '  <url>\n'
        f'    <loc>{BASE_URL}/rabbins.html</loc>\n'
        f'    <lastmod>{today}</lastmod>\n'
        '    <changefreq>weekly</changefreq>\n'
        '    <priority>0.8</priority>\n'
        '  </url>\n'
        '  <url>\n'
        f'    <loc>{BASE_URL}/paracha.html</loc>\n'
        f'    <lastmod>{today}</lastmod>\n'
        '    <changefreq>weekly</changefreq>\n'
        '    <priority>0.7</priority>\n'
        '  </url>\n'
        '  <url>\n'
        f'    <loc>{BASE_URL}/themes.html</loc>\n'
        f'    <lastmod>{today}</lastmod>\n'
        '    <changefreq>weekly</changefreq>\n'
        '    <priority>0.7</priority>\n'
        '  </url>\n'
        '  <url>\n'
        f'    <loc>{BASE_URL}/derniers-cours.html</loc>\n'
        f'    <lastmod>{today}</lastmod>\n'
        '    <changefreq>daily</changefreq>\n'
        '    <priority>0.8</priority>\n'
        '  </url>\n'
        '  <url>\n'
        f'    <loc>{BASE_URL}/daf-hayomi.html</loc>\n'
        f'    <lastmod>{today}</lastmod>\n'
        '    <changefreq>daily</changefreq>\n'
        '    <priority>0.8</priority>\n'
        '  </url>\n'
        '  <url>\n'
        f'    <loc>{BASE_URL}/hitat.html</loc>\n'
        f'    <lastmod>{today}</lastmod>\n'
        '    <changefreq>daily</changefreq>\n'
        '    <priority>0.8</priority>\n'
        '  </url>\n'
        '  <url>\n'
        f'    <loc>{BASE_URL}/hayom-yom.html</loc>\n'
        f'    <lastmod>{today}</lastmod>\n'
        '    <changefreq>daily</changefreq>\n'
        '    <priority>0.8</priority>\n'
        '  </url>\n'
        '  <url>\n'
        f'    <loc>{BASE_URL}/bein-hametsarim.html</loc>\n'
        f'    <lastmod>{today}</lastmod>\n'
        '    <changefreq>monthly</changefreq>\n'
        '    <priority>0.8</priority>\n'
        '  </url>\n'
        '  <url>\n'
        f'    <loc>{BASE_URL}/hiloula.html</loc>\n'
        f'    <lastmod>{today}</lastmod>\n'
        '    <changefreq>weekly</changefreq>\n'
        '    <priority>0.7</priority>\n'
        '  </url>\n'
        '  <url>\n'
        f'    <loc>{BASE_URL}/politique-confidentialite.html</loc>\n'
        f'    <lastmod>{today}</lastmod>\n'
        '    <changefreq>yearly</changefreq>\n'
        '    <priority>0.3</priority>\n'
        '  </url>\n'
        f'{channel_entries}\n'
        f'{episode_entries}\n'
        '</urlset>\n'
    )
    total_eps = sum(len(e) for _, e in slug_entries)
    Path("sitemap.xml").write_text(sitemap, encoding="utf-8")
    print(f"  sitemap.xml -> {len(slugs)} channel pages + {total_eps} episode pages added")


def speaker_matches(title: str, patterns: list[str]) -> bool:
    t = title.lower()
    return any(p.lower() in t for p in patterns)


def write_speaker_feed(slug: str, episodes: list[dict]) -> None:
    """Write feeds/<speaker>.entries.json, same format as a channel's own feed.

    The episodes are the host channels' entries verbatim, so every consumer that
    resolves a rav by slug (mobile app, social_post.py, ...) works unchanged.
    Only rewrite on change: this runs hourly in CI and an identical rewrite would
    churn 15 multi-MB files in git for nothing.
    """
    path    = FEEDS_DIR / f"{slug}.entries.json"
    payload = json.dumps(episodes, ensure_ascii=False, indent=2)
    if path.exists() and path.read_text(encoding="utf-8") == payload:
        return
    path.write_text(payload, encoding="utf-8")


def render_speaker_page(
    speaker: dict,
    episodes: list[dict],
    all_channels: list[dict],
    all_speakers: list[dict],
    site_channels: int = 0,
    site_episodes: int = 0,
    site_hours: int = 0,
) -> str:
    """Generate a channel-style page for a guest speaker."""
    fake_ch = {
        "slug": speaker["slug"],
        "podcast_author": speaker["name"],
        "podcast_language": speaker.get("language", "fr"),
        "platforms": {},
        "enabled": True,
        # Speakers have no dedicated artwork/<slug>.png — represent them with the
        # thumbnail of their most recent episode that actually has one (episodes are
        # sorted newest-first). Using episodes[0] blindly broke the header image when
        # the newest episode lacked a thumbnail (fell back to a non-existent PNG →
        # missing face). Mirrors the front-end logic in rabbins.html / index.html.
        "thumbnail": next((e.get("thumbnail") for e in episodes if e.get("thumbnail")), ""),
        "speaker": True,
    }
    # Inject extra speakers into all_channels list for nav
    nav_list = list(all_channels) + [
        {"slug": s["slug"], "podcast_author": s["name"], "enabled": True}
        for s in all_speakers
    ]
    return render_page(fake_ch, episodes, nav_list, site_channels, site_episodes, site_hours)


def main():
    channels = json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
    enabled  = [ch for ch in channels if ch.get("enabled")]

    speakers = json.loads(SPEAKERS_FILE.read_text(encoding="utf-8")) if SPEAKERS_FILE.exists() else []

    # Pre-load all entries
    all_data = []
    for ch in enabled:
        entries_path = FEEDS_DIR / f"{ch['slug']}.entries.json"
        if not entries_path.exists():
            continue
        entries = json.loads(entries_path.read_text(encoding="utf-8"))
        all_data.append((ch, entries))

    site_channels = len(all_data) + len(speakers)
    site_episodes = sum(len(e) for _, e in all_data)
    site_hours    = round(site_episodes * 0.75)

    # Pre-compute per-speaker episode counts (same filter as the speaker pages below)
    # so the shared nav dropdown can show counts without recomputing per page.
    entries_cache = {ch["slug"]: entries for ch, entries in all_data}
    speaker_counts = {}
    for speaker in speakers:
        n = 0
        for ch_slug in speaker.get("from_channels", []):
            n += sum(
                1 for ep in entries_cache.get(ch_slug, [])
                if speaker_matches(ep.get("title", ""), speaker["title_patterns"])
            )
        speaker_counts[speaker["slug"]] = n

    # Build the shared "Rabbins" nav dropdown items (channels + speakers), A-Z, with counts.
    NAV_ITEMS.clear()
    NAV_ITEMS.extend(
        {"name": ch["podcast_author"], "slug": ch["slug"], "count": len(entries)}
        for ch, entries in all_data
    )
    NAV_ITEMS.extend(
        {"name": sp["name"], "slug": sp["slug"], "count": speaker_counts.get(sp["slug"], 0)}
        for sp in speakers
    )
    NAV_ITEMS.sort(key=lambda it: it["name"].lower())

    # Channel pages
    generated = []
    for ch, entries in all_data:
        slug  = ch["slug"]
        pslug = url_slug(slug)
        page  = render_page(ch, entries, enabled, site_channels, site_episodes, site_hours)
        out   = Path(f"{pslug}.html")
        out.write_text(page, encoding="utf-8")
        # Capitalised slugs (Nahal-Haim, Rav-Itshak-Sitruk) keep a redirect stub at
        # the old URL so existing links/bookmarks don't 404. Feeds stay capitalised.
        write_redirect_stub(slug, pslug)
        print(f"  {out}  ({len(entries)} episodes)")
        generated.append((slug, entries))

    # Channel episode pages
    ep_count = 0
    for slug, entries in generated:
        ch = next(c for c in enabled if c["slug"] == slug)
        ch_dir = Path(slug)
        ch_dir.mkdir(exist_ok=True)
        for ep in entries:
            if not ep.get("title") or not ep.get("published"):
                continue
            ep_page = render_episode_page(ep, ch, entries, enabled)
            (ch_dir / ep_filename(ep, slug)).write_text(ep_page, encoding="utf-8")
            ep_count += 1
    print(f"  rabbi subdirs/  ({ep_count} channel episode pages generated)")

    # Speaker pages
    for speaker in speakers:
        sp_episodes = []
        for ch_slug in speaker.get("from_channels", []):
            if ch_slug in entries_cache:
                sp_episodes += [
                    ep for ep in entries_cache[ch_slug]
                    if speaker_matches(ep.get("title", ""), speaker["title_patterns"])
                ]
        sp_episodes.sort(key=lambda x: x.get("published", ""), reverse=True)
        slug  = speaker["slug"]
        pslug = url_slug(slug)
        # Derived feed, same shape/name as a channel's, so anything that reads a
        # rav by slug (the mobile app in particular) reaches the speakers too.
        # Rewritten from the host channels every run — never edit it by hand, and
        # see scripts/feeds_util.py before globbing feeds/*.entries.json.
        write_speaker_feed(slug, sp_episodes)
        page  = render_speaker_page(speaker, sp_episodes, enabled, speakers,
                                    site_channels, site_episodes, site_hours)
        Path(f"{pslug}.html").write_text(page, encoding="utf-8")
        write_redirect_stub(slug, pslug)
        print(f"  {pslug}.html  ({len(sp_episodes)} episodes)")
        sp_dir = Path(slug)
        sp_dir.mkdir(exist_ok=True)
        fake_ch = {"slug": slug, "podcast_author": speaker["name"],
                   "podcast_language": speaker.get("language", "fr"), "platforms": {}}
        for ep in sp_episodes:
            if not ep.get("title") or not ep.get("published"):
                continue
            ep_page = render_episode_page(ep, fake_ch, sp_episodes, enabled)
            (sp_dir / ep_filename(ep, slug)).write_text(ep_page, encoding="utf-8")
            ep_count += 1

    # Pre-computed homepage data (replaces the client-side ~11 MB entries.json fan-out).
    build_home_json(all_data, speakers, entries_cache, site_channels, site_episodes)

    # Minimal full-text search index over the whole catalog (lazy-loaded by index.html
    # only on first search focus/keystroke — keeps the homepage at ~15 KB at load).
    build_search_index(all_data)

    # Pre-computed study rattachements for the mobile app (mobile/): the same
    # daf / hitat / hayom-yom / paracha / hiloula / theme buckets the study pages
    # compute in the browser, so the app doesn't have to pull ~31 MB of feeds.
    build_mobile_index(all_data)

    update_sitemap(generated)
    print(f"\nDone — {len(generated)} channel + {len(speakers)} speaker pages + {ep_count} episode pages.")


if __name__ == "__main__":
    main()
