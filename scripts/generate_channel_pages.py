#!/usr/bin/env python3
"""
generate_channel_pages.py
Generates a static <slug>.html per enabled channel and updates sitemap.xml.
No external dependencies — stdlib only.
"""
import json
import html as _html
from datetime import datetime
from pathlib import Path

BASE_URL      = "https://thetorahpodcast.net"
CHANNELS_FILE = Path("channels.json")
FEEDS_DIR     = Path("feeds")

MONTHS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

def esc(s):
    return _html.escape(str(s), quote=True)

def fmt_date(iso, lang):
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if lang == "he":
            return f"{d.day}.{d.month:02d}.{d.year}"
        return f"{d.day} {MONTHS_FR[d.month - 1]} {d.year}"
    except Exception:
        return iso[:10]

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
    header h1 { font-size: 2rem; margin-bottom: 6px; }
    header h1 a { color: inherit; text-decoration: none; }
    header p { color: #aab; font-size: .95rem; margin-bottom: 14px; }
    .header-nav { display: flex; justify-content: center; flex-wrap: wrap; gap: 6px; padding: 0 16px 20px; }
    .header-nav a { color: rgba(255,255,255,.65); text-decoration: none; font-size: .8rem; padding: 5px 14px; border-radius: 20px; border: 1px solid rgba(255,255,255,.2); transition: background .15s, color .15s; }
    .header-nav a:hover { color: #fff; background: rgba(255,255,255,.1); border-color: rgba(255,255,255,.5); }
    .header-nav a.active { color: #1a1a2e; background: #fff; border-color: #fff; font-weight: 600; }
    .nav-dropdown { position: relative; display: inline-flex; }
    .nav-submenu { display: none; position: absolute; top: calc(100% + 8px); left: 50%; transform: translateX(-50%); background: #252545; border: 1px solid rgba(255,255,255,.12); border-radius: 10px; padding: 6px; min-width: 200px; z-index: 300; box-shadow: 0 8px 24px rgba(0,0,0,.4); }
    .nav-dropdown:hover .nav-submenu { display: block; }
    .nav-submenu a { display: block; color: rgba(255,255,255,.78); text-decoration: none; padding: 7px 14px; border-radius: 6px; font-size: .8rem; white-space: nowrap; border: none; }
    .nav-submenu a:hover { background: rgba(255,255,255,.1); color: #fff; }
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
    .ch-about { background: #fff; border-radius: 12px; box-shadow: 0 1px 4px rgba(0,0,0,.07); padding: 20px 24px; margin-bottom: 28px; font-size: .9rem; color: #444; line-height: 1.7; }
    .ch-about p { margin-bottom: .9em; }
    .ch-about p:last-child { margin-bottom: 0; }
    .section-label { font-size: .75rem; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; color: #999; margin-bottom: 12px; }
    .episode-list { display: flex; flex-direction: column; gap: 2px; }
    .episode { background: #fff; border-radius: 10px; display: flex; gap: 14px; padding: 13px 18px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
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
    }"""

GTAG = """\
  <!-- Google tag (gtag.js) -->
  <script async src="https://www.googletagmanager.com/gtag/js?id=G-7Z2QEN865Y"></script>
  <script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','G-7Z2QEN865Y');</script>"""


def load_channel_info(slug: str) -> dict:
    path = FEEDS_DIR / f"{slug}.channel_info.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def render_page(ch: dict, entries: list, all_channels: list) -> str:
    slug      = ch["slug"]
    name      = ch["podcast_author"]
    lang      = ch.get("podcast_language", "fr")
    platforms = ch.get("platforms", {})
    ep_count  = len(entries)

    channel_info    = load_channel_info(slug)
    yt_description  = (channel_info.get("description") or "").strip()

    if lang == "he":
        html_lang     = "he"
        dir_attr      = ' dir="rtl"'
        fallback_desc = f"האזינו לשיעורי התורה של {name}. {ep_count} פרקים זמינים בפודקאסט."
        all_eps_label = "כל הפרקים"
        ep_count_text = f"{ep_count} פרקים"
        subtitle      = "שיעורי תורה — זמינים בפלטפורמות האהובות עליכם"
        nav_labels    = ("ראשי", "הרבנים", "פרשה", "נושא")
    else:
        html_lang     = "fr"
        dir_attr      = ""
        fallback_desc = f"Écoutez tous les cours de Torah du {name}. {ep_count} épisodes disponibles sur Spotify, Apple Podcasts et Deezer."
        all_eps_label = "Tous les épisodes"
        ep_count_text = f"{ep_count} épisode{'s' if ep_count != 1 else ''}"
        subtitle      = "Cours de Torah — disponibles sur vos plateformes favorites"
        nav_labels    = ("Accueil", "Rabbins", "Paracha", "Thème")

    seo_description  = (channel_info.get("seo_description") or "").strip()
    page_description = (channel_info.get("page_description") or "").strip()
    description      = seo_description or (yt_description[:155] if yt_description else fallback_desc)

    # Platform buttons
    btns = []
    for key, (icon, label, cls) in PLATFORM_META.items():
        url = platforms.get(key, "").strip()
        if url:
            btns.append(
                f'<a class="platform-btn {cls}" href="{esc(url)}" target="_blank" rel="noopener">'
                f'{icon}{label}</a>'
            )
    rss_url = f"{BASE_URL}/feeds/{slug}.xml"
    btns.append(f'<a class="platform-btn btn-rss" href="{esc(rss_url)}" target="_blank" rel="noopener">RSS</a>')
    platform_html = "\n        ".join(btns)

    # Static episode list
    sorted_entries = sorted(entries, key=lambda x: x["published"], reverse=True)
    ep_parts = []
    for ep in sorted_entries:
        thumb     = ep.get("thumbnail", "")
        desc_raw  = (ep.get("description") or "").strip()[:200]
        audio_url = ep.get("audio_url", "")

        thumb_tag = (
            f'<img class="ep-thumb" src="{esc(thumb)}" alt="" loading="lazy">'
            if thumb else '<div class="ep-thumb-ph"></div>'
        )
        desc_tag  = f'<p class="ep-desc">{esc(desc_raw)}</p>' if desc_raw else ""
        audio_tag = (
            f'<audio class="ep-audio" controls src="{esc(audio_url)}" preload="none"></audio>'
            if audio_url else ""
        )
        ep_parts.append(
            f'    <article class="episode">\n'
            f'      {thumb_tag}\n'
            f'      <div class="ep-body">\n'
            f'        <h2 class="ep-title">{esc(ep["title"])}</h2>\n'
            f'        <time class="ep-date" datetime="{ep["published"][:10]}">{fmt_date(ep["published"], lang)}</time>\n'
            f'        {desc_tag}\n'
            f'        {audio_tag}\n'
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
        "url": f"{BASE_URL}/{slug}.html",
        "webFeed": f"{BASE_URL}/feeds/{slug}.xml",
        "image": f"{BASE_URL}/artwork/{slug}.png",
        "inLanguage": lang,
        "author": {"@type": "Person", "name": name},
        "episode": ep_schema,
    }
    schema_json = json.dumps(schema, ensure_ascii=False, indent=2)

    home, rabbis, parasha, themes = nav_labels

    if page_description:
        paras = "".join(
            f"<p>{esc(p.strip())}</p>"
            for p in page_description.splitlines()
            if p.strip()
        )
        about_block = f'<div class="ch-about">{paras}</div>'
    else:
        about_block = ""

    submenu_links = "\n".join(
        f'      <a href="{esc(c["slug"])}.html">{esc(c["podcast_author"])}</a>'
        for c in all_channels
        if c.get("enabled")
    )

    return f"""<!DOCTYPE html>
<html lang="{html_lang}"{dir_attr}>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(name)} — The Torah Podcast</title>
  <meta name="description" content="{esc(description)}">
  <link rel="canonical" href="{BASE_URL}/{slug}.html">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{BASE_URL}/{slug}.html">
  <meta property="og:title" content="{esc(name)} — The Torah Podcast">
  <meta property="og:description" content="{esc(description)}">
  <meta property="og:image" content="{BASE_URL}/artwork/{slug}.png">
  <meta property="og:site_name" content="The Torah Podcast">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{esc(name)} — The Torah Podcast">
  <meta name="twitter:description" content="{esc(description)}">
  <meta name="twitter:image" content="{BASE_URL}/artwork/{slug}.png">
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
  <h1><a href="index.html">The Thora Podcast</a></h1>
  <p>{esc(subtitle)}</p>
  <nav class="header-nav">
    <a href="index.html">{esc(home)}</a>
    <div class="nav-dropdown">
      <a href="links.html" class="active">{esc(rabbis)} ▾</a>
      <div class="nav-submenu">
{submenu_links}
      </div>
    </div>
    <a href="parasha.html">{esc(parasha)}</a>
    <a href="themes.html">{esc(themes)}</a>
  </nav>
</header>
<main>
  <div class="ch-card">
    <img class="ch-art" src="artwork/{slug}.png" alt="{esc(name)}" onerror="this.style.display='none'">
    <div>
      <h1 class="ch-name">{esc(name)}</h1>
      <p class="ch-count">{ep_count_text}</p>
      <div class="platform-links">
        {platform_html}
      </div>
    </div>
  </div>
  {about_block}
  <p class="section-label">{esc(all_eps_label)}</p>
  <div class="episode-list">
{episodes_html}
  </div>
</main>
</body>
</html>
"""


def update_sitemap(slugs: list[str]):
    channel_entries = "\n".join(
        f"  <url>\n"
        f"    <loc>{BASE_URL}/{slug}.html</loc>\n"
        f"    <changefreq>daily</changefreq>\n"
        f"    <priority>0.9</priority>\n"
        f"  </url>"
        for slug in slugs
    )
    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        '  <url>\n'
        f'    <loc>{BASE_URL}/</loc>\n'
        '    <changefreq>daily</changefreq>\n'
        '    <priority>1.0</priority>\n'
        '  </url>\n'
        '  <url>\n'
        f'    <loc>{BASE_URL}/links.html</loc>\n'
        '    <changefreq>weekly</changefreq>\n'
        '    <priority>0.8</priority>\n'
        '  </url>\n'
        '  <url>\n'
        f'    <loc>{BASE_URL}/parasha.html</loc>\n'
        '    <changefreq>weekly</changefreq>\n'
        '    <priority>0.7</priority>\n'
        '  </url>\n'
        '  <url>\n'
        f'    <loc>{BASE_URL}/themes.html</loc>\n'
        '    <changefreq>weekly</changefreq>\n'
        '    <priority>0.7</priority>\n'
        '  </url>\n'
        f'{channel_entries}\n'
        '</urlset>\n'
    )
    Path("sitemap.xml").write_text(sitemap, encoding="utf-8")
    print(f"  sitemap.xml → {len(slugs)} channel pages added")


def main():
    channels = json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
    enabled  = [ch for ch in channels if ch.get("enabled")]

    generated = []
    for ch in enabled:
        slug         = ch["slug"]
        entries_path = FEEDS_DIR / f"{slug}.entries.json"
        if not entries_path.exists():
            print(f"  Skipping {slug} — no entries file yet")
            continue
        entries = json.loads(entries_path.read_text(encoding="utf-8"))
        page    = render_page(ch, entries, enabled)
        out     = Path(f"{slug}.html")
        out.write_text(page, encoding="utf-8")
        print(f"  {out}  ({len(entries)} episodes)")
        generated.append(slug)

    update_sitemap(generated)
    print(f"\nDone — {len(generated)} channel pages generated.")


if __name__ == "__main__":
    main()
