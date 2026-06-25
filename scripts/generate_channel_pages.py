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
from datetime import datetime
from pathlib import Path

BASE_URL       = "https://thetorahpodcast.net"
CHANNELS_FILE  = Path("channels.json")
SPEAKERS_FILE  = Path("speakers.json")
FEEDS_DIR      = Path("feeds")

MONTHS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

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
    .nav-dd-link:hover {{ color: #fff; background: rgba(255,255,255,.1); border-color: rgba(255,255,255,.5); }}
    .nav-dd-caret {{ color: rgba(255,255,255,.65); font-size: .8rem; padding: 5px 10px 5px 6px; border-radius: 0 20px 20px 0; border: 1px solid rgba(255,255,255,.2); border-left: none; background: none; cursor: pointer; font-family: inherit; transition: background .15s, color .15s; }}
    .nav-dd-caret:hover, .nav-dropdown.open .nav-dd-caret {{ color: #fff; background: rgba(255,255,255,.1); border-color: rgba(255,255,255,.5); }}
    .nav-dd-link.active {{ color: #1a1a2e; background: #fff; border-color: #fff; font-weight: 600; }}
    .nav-dd-link.active + .nav-dd-caret {{ color: #1a1a2e; background: #fff; border-color: #fff; }}
    .nav-submenu { display: none; position: absolute; top: calc(100% + 8px); left: 50%; transform: translateX(-50%); background: #252545; border: 1px solid rgba(255,255,255,.12); border-radius: 12px; padding: 10px; z-index: 300; box-shadow: 0 8px 28px rgba(0,0,0,.5); flex-wrap: wrap; gap: 4px; min-width: 340px; max-width: 92vw; }
    .nav-dropdown.open .nav-submenu { display: flex; }
    .nav-submenu a { color: rgba(255,255,255,.78); text-decoration: none; padding: 6px 12px; border-radius: 20px; font-size: .78rem; white-space: nowrap; border: 1px solid rgba(255,255,255,.15); transition: background .12s, color .12s; }
    .nav-submenu a:hover { background: rgba(255,255,255,.12); color: #fff; border-color: rgba(255,255,255,.35); }
    .nav-submenu .nav-submenu-all { color: rgba(255,255,255,.5); border-style: dashed; font-size: .74rem; margin-top: 4px; width: 100%; text-align: center; }
    .lang-btn { color: rgba(255,255,255,.65); font-size: .8rem; padding: 5px 14px; border-radius: 20px; border: 1px solid rgba(255,255,255,.2); background: none; cursor: pointer; font-family: inherit; transition: background .15s, color .15s; }
    .lang-btn:hover { color: #fff; background: rgba(255,255,255,.1); border-color: rgba(255,255,255,.5); }
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
    .site-footer {{ background: #1a1a2e; color: rgba(255,255,255,.5); text-align: center; padding: 20px 16px; font-size: .78rem; margin-top: 40px; }}
    .site-footer a {{ color: rgba(255,255,255,.65); text-decoration: none; margin: 0 8px; }}
    .site-footer a:hover {{ color: #fff; }}
    .toast { position:fixed; bottom:20px; left:50%; transform:translateX(-50%); background:#1a1a2e; color:#fff; padding:8px 18px; border-radius:20px; font-size:.82rem; z-index:500; opacity:0; transition:opacity .2s; pointer-events:none; white-space:nowrap; box-shadow:0 4px 14px rgba(0,0,0,.3); }
    .toast.show { opacity:1; }
    details.transcript { margin-top:20px; border:1px solid #e8e8e8; border-radius:10px; overflow:hidden; }
    details.transcript summary { padding:10px 16px; font-size:.78rem; font-weight:600; color:#555; cursor:pointer; background:#fafafa; list-style:none; display:flex; align-items:center; gap:6px; }
    details.transcript summary::-webkit-details-marker { display:none; }
    details.transcript summary::before { content:'▶'; font-size:.6rem; color:#999; transition:transform .2s; }
    details.transcript[open] summary::before { transform:rotate(90deg); }
    details.transcript summary:hover { background:#f5f5f0; }
    .transcript-body { padding:14px 18px; font-size:.82rem; color:#555; line-height:1.75; word-break:break-word; max-height:400px; overflow-y:auto; }
    .play-btn { display:inline-flex; align-items:center; gap:6px; background:#1a1a2e; color:#fff; border:none; border-radius:20px; padding:5px 13px; font-size:.76rem; cursor:pointer; transition:background .15s; font-family:inherit; }
    .play-btn:hover { background:#2d2d50; }
    .play-btn.playing { background:#e87722; }
    .play-btn svg { width:10px; height:10px; flex-shrink:0; }
    #player { position:fixed; bottom:0; left:0; right:0; background:#1a1a2e; color:#fff; display:flex; align-items:center; gap:12px; padding:10px 16px; box-shadow:0 -2px 16px rgba(0,0,0,.25); z-index:200; transform:translateY(0); transition:transform .25s ease; }
    #player.hidden { transform:translateY(100%); }
    #player-art { width:44px; height:44px; border-radius:6px; object-fit:cover; background:#333; flex-shrink:0; }
    #player-info { min-width:0; flex:1; }
    #player-title { font-size:.82rem; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    #player-channel { font-size:.68rem; color:#99a; }
    #player-audio { flex:2; min-width:120px; height:32px; accent-color:#e87722; }
    #speed-cycle-btn {{ background:none; border:1px solid rgba(255,255,255,.2); color:rgba(255,255,255,.5); border-radius:4px; padding:3px 9px; font-size:.68rem; cursor:pointer; font-family:inherit; white-space:nowrap; flex-shrink:0; transition:background .12s,color .12s; }}
    #speed-cycle-btn:hover {{ background:rgba(255,255,255,.15); color:#fff; border-color:rgba(255,255,255,.4); }}
    #speed-cycle-btn.active {{ color:#e87722; border-color:#e87722; }}
    #player-close {{ background:none; border:none; color:#778; font-size:1.1rem; cursor:pointer; padding:4px 6px; flex-shrink:0; line-height:1; }}
    #player-close:hover {{ color:#fff; }}
    @media (max-width:500px) {{ #player-art {{ display:none; }} }}
    .btn-embed {{ background:#f5f5f0; color:#555; border:1px solid #ddd; }}
    .btn-embed:hover {{ background:#eee; border-color:#bbb; }}
    .embed-modal {{ display:none; position:fixed; inset:0; z-index:500; align-items:center; justify-content:center; background:rgba(0,0,0,.45); padding:16px; }}
    .embed-modal.open {{ display:flex; }}
    .embed-box {{ background:#fff; border-radius:16px; padding:24px; max-width:460px; width:100%; box-shadow:0 12px 48px rgba(0,0,0,.3); }}
    .embed-box-header {{ display:flex; align-items:center; justify-content:space-between; margin-bottom:14px; }}
    .embed-box-header h3 {{ font-size:.95rem; font-weight:700; }}
    .embed-close {{ background:none; border:none; color:#aaa; font-size:1.3rem; cursor:pointer; line-height:1; padding:0; }}
    .embed-close:hover {{ color:#333; }}
    .embed-code {{ background:#f5f5f0; border-radius:8px; padding:12px; font-family:monospace; font-size:.72rem; color:#333; white-space:pre-wrap; word-break:break-all; margin-bottom:12px; border:1px solid #e0e0e0; user-select:all; cursor:text; }}
    .embed-actions {{ display:flex; gap:8px; align-items:center; }}
    .embed-copy-btn {{ background:#1a1a2e; color:#fff; border:none; border-radius:8px; padding:8px 18px; font-size:.82rem; cursor:pointer; font-family:inherit; transition:background .15s; }}
    .embed-copy-btn:hover {{ background:#2d2d50; }}
    .embed-preview-label {{ font-size:.72rem; color:#999; margin:14px 0 6px; font-weight:600; text-transform:uppercase; letter-spacing:.04em; }}
    .embed-iframe-wrap {{ border:1px solid #e0e0e0; border-radius:10px; overflow:hidden; height:200px; }}
    .embed-iframe-wrap iframe {{ width:100%; height:100%; border:none; transform:scale(.6); transform-origin:top left; width:167%; height:167%; pointer-events:none; }}"""

GTAG = """\
  <link rel="preconnect" href="https://www.googletagmanager.com">
  <link rel="preconnect" href="https://pub-a5fae25ce5124edebe0bf7393f72823c.r2.dev" crossorigin>
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
            f'    <article class="episode" data-dur="{dur_secs}">\n'
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
        "url": f"{BASE_URL}/{slug}.html",
        "webFeed": f"{BASE_URL}/feeds/{slug}.xml",
        "image": f"{BASE_URL}/artwork/{slug}.png",
        "inLanguage": ["fr", "he"],
        "author": {"@type": "Person", "name": name},
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

    submenu_links = "\n".join(
        f'      <a href="{esc(c["slug"])}.html">{esc(c["podcast_author"])}</a>'
        for c in all_channels
        if c.get("enabled")
    )

    return f"""<!DOCTYPE html>
<html lang="{default_lang}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(name)} — Cours de Torah en podcast — The Torah Podcast</title>
  <meta name="description" content="{esc(description)}">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{BASE_URL}/{slug}.html">
  <link rel="alternate" hreflang="fr" href="{BASE_URL}/{slug}.html">
  <link rel="alternate" hreflang="he" href="{BASE_URL}/{slug}.html">
  <link rel="alternate" hreflang="x-default" href="{BASE_URL}/{slug}.html">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{BASE_URL}/{slug}.html">
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
      </div>
    </div>
    <a href="paracha.html" data-i18n="nav_paracha">Paracha</a>
    <a href="themes.html" data-i18n="nav_themes">Thème</a>
    <button class="lang-btn" onclick="toggleLang()" data-i18n="lang_toggle">עברית</button>
  </nav>
</header>
<main>
  <div class="ch-card">
    <img class="ch-art" src="{esc(ch.get('thumbnail') or f'artwork/{slug}.png')}" alt="{esc(name)}" onerror="this.src='artwork/{slug}.png'">
    <div>
      <h1 class="ch-name">{esc(name)}</h1>
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
<div id="player" class="hidden">
  <img id="player-art" src="" alt="">
  <div id="player-info">
    <div id="player-title"></div>
    <div id="player-channel"></div>
  </div>
  <audio id="player-audio" controls></audio>
  <button id="speed-cycle-btn" title="Vitesse de lecture">1×</button>
  <button id="player-close" title="Fermer">✕</button>
</div>
<script src="js/utils.js"></script>
<script>
  const I18N = {{
    fr: {{
      nav_home:'Accueil', nav_rabbis:'Rabbins ▾', nav_last_classes:'Derniers cours', nav_daf_hayomi:'Daf Hayomi', nav_limud:'Limud Yomi', nav_hitat:'Hitat Yomi', nav_paracha:'Paracha', nav_themes:'Thème',
      lang_toggle:'עברית', subtitle:'Cours de Torah — disponibles sur vos plateformes favorites',
      all_episodes:'Tous les épisodes',
      ep_count: n => `${{n}} épisode${{n !== 1 ? 's' : ''}}`,
      listen:'Écouter', playing:'En cours…',
    }},
    he: {{
      nav_home:'ראשי', nav_rabbis:'הרבנים ▾', nav_last_classes:'שיעורים אחרונים', nav_daf_hayomi:'דף היומי', nav_limud:'לימוד יומי', nav_hitat:'חת"ת', nav_paracha:'פרשה', nav_themes:'נושא',
      lang_toggle:'Français', subtitle:'שיעורי תורה — זמינים בפלטפורמות האהובות עליכם',
      all_episodes:'כל הפרקים',
      ep_count: n => `${{n}} פרקים`,
      listen:'האזן', playing:'מתנגן…',
    }},
  }};
  let lang = localStorage.getItem('lang') || '{default_lang}';
  function t(k) {{ const d = I18N[lang] || {{}}; return d[k] || I18N.fr[k] || k; }}
  function applyLang() {{
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
      btn.textContent = lang === 'he' ? 'הצג עוד ▾' : 'Voir plus ▾';
      btn.addEventListener('click', () => {{
        const isCollapsed = about.classList.toggle('collapsed');
        btn.textContent = isCollapsed
          ? (lang === 'he' ? 'הצג עוד ▾' : 'Voir plus ▾')
          : (lang === 'he' ? 'הצג פחות ▴' : 'Voir moins ▴');
      }});
    }}
  }})();
  function toggleLang() {{
    localStorage.setItem('lang', lang === 'fr' ? 'he' : 'fr');
    location.reload();
  }}
  applyLang();
  // Floating player
  const playerEl    = document.getElementById('player');
  const playerAudio = document.getElementById('player-audio');
  const playerTitle = document.getElementById('player-title');
  const playerCh    = document.getElementById('player-channel');
  const playerArt   = document.getElementById('player-art');
  let currentEpId   = null;
  function loadInPlayer(btn) {{
    const epId  = btn.dataset.epId;
    const audio = btn.dataset.audio;
    const title = btn.dataset.title;
    const thumb = btn.dataset.thumb || '';
    document.querySelectorAll('.play-btn').forEach(b => b.classList.remove('playing'));
    if (currentEpId === epId && !playerAudio.paused) {{
      playerAudio.pause();
      currentEpId = null;
      return;
    }}
    currentEpId = epId;
    btn.classList.add('playing');
    playerTitle.textContent = title;
    playerCh.textContent    = '{esc(name)}';
    playerArt.src           = thumb;
    playerEl.classList.remove('hidden');
    if (playerAudio.src !== audio) {{
      playerAudio.src = audio;
      const saved = parseInt(localStorage.getItem('resume_' + epId) || '0');
      if (saved > 5) playerAudio.addEventListener('loadedmetadata', () => {{ playerAudio.currentTime = saved; }}, {{once:true}});
    }}
    playerAudio.playbackRate = parseFloat(localStorage.getItem('playbackSpeed') || '1');
    playerAudio.play();
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
  // Player speed — single cycle button
  const SPEEDS = [1, 1.25, 1.5, 2];
  let currentSpeed = parseFloat(localStorage.getItem('playbackSpeed') || '1');
  const speedCycleBtn = document.getElementById('speed-cycle-btn');
  function updateSpeedBtn() {{
    speedCycleBtn.textContent = currentSpeed + '×';
    speedCycleBtn.classList.toggle('active', currentSpeed !== 1);
  }}
  updateSpeedBtn();
  speedCycleBtn.addEventListener('click', () => {{
    const idx = SPEEDS.indexOf(currentSpeed);
    currentSpeed = SPEEDS[idx === -1 ? 1 : (idx + 1) % SPEEDS.length];
    localStorage.setItem('playbackSpeed', currentSpeed);
    if (playerAudio) playerAudio.playbackRate = currentSpeed;
    updateSpeedBtn();
  }});
  // Resume save & ended
  if (playerAudio) {{
    playerAudio.addEventListener('timeupdate', () => {{
      if (currentEpId && playerAudio.currentTime > 5)
        localStorage.setItem('resume_' + currentEpId, Math.floor(playerAudio.currentTime));
    }});
    playerAudio.addEventListener('ended', () => {{
      if (currentEpId) localStorage.removeItem('resume_' + currentEpId);
      document.querySelectorAll('.play-btn').forEach(b => b.classList.remove('playing'));
      currentEpId = null;
    }});
  }}
  // Player close
  document.getElementById('player-close').addEventListener('click', () => {{
    playerAudio.pause();
    playerEl.classList.add('hidden');
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
          gtag('event', 'audio_play', {{ep_title: playerTitle.textContent, rav: '{esc(name)}'}});
        }}
      }});
      playerAudio.addEventListener('timeupdate', () => {{
        if (currentEpId && !ga4Completed[currentEpId] && playerAudio.duration > 0 && playerAudio.currentTime / playerAudio.duration >= 0.9) {{
          ga4Completed[currentEpId] = true;
          gtag('event', 'audio_complete', {{ep_title: playerTitle.textContent, rav: '{esc(name)}'}});
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
            f'<div class="ep-actions"><audio class="ep-audio" controls src="{esc(r["audio_url"])}" preload="none" data-ep-id="{esc(r_vid)}"></audio>'
            f'<button class="share-btn" data-vid="{esc(r_vid)}" data-slug="{esc(slug)}" data-title="{esc(r["title"])}">'
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
            "url": f"{BASE_URL}/{slug}.html",
        },
        "author": {"@type": "Person", "name": name},
        "description": desc[:500] if desc else f"Épisode de {name}",
    }
    if audio:
        schema["associatedMedia"] = {"@type": "MediaObject", "contentUrl": audio}
    if thumb:
        schema["image"] = thumb
    schema_json = json.dumps(schema, ensure_ascii=False, indent=2)

    breadcrumb_schema = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Accueil", "item": f"{BASE_URL}/"},
            {"@type": "ListItem", "position": 2, "name": name, "item": f"{BASE_URL}/{slug}.html"},
            {"@type": "ListItem", "position": 3, "name": title, "item": f"{BASE_URL}/{ep_path(slug, ep)}"},
        ],
    }
    breadcrumb_json = json.dumps(breadcrumb_schema, ensure_ascii=False, indent=2)

    submenu_links = "\n".join(
        f'      <a href="../{esc(c["slug"])}.html">{esc(c["podcast_author"])}</a>'
        for c in all_channels
        if c.get("enabled")
    )

    transcript_path = FEEDS_DIR / "transcripts" / f"{video_id}.txt"
    transcript = transcript_path.read_text(encoding="utf-8").strip() if transcript_path.exists() else ""

    seo_desc_src = desc or transcript
    seo_desc = seo_desc_src[:155] if seo_desc_src else f"Écoutez {title} — cours de {name} sur The Torah Podcast."
    og_locale = "he_IL" if lang == "he" else "fr_FR"
    og_locale_alt = "fr_FR" if lang == "he" else "he_IL"
    og_image = thumb if thumb else f"{BASE_URL}/artwork/{slug}.png"
    audio_tag = (
        f'<audio id="ep-audio" controls src="{esc(audio)}" preload="none" data-ep-id="{esc(video_id)}"'
        f' style="width:100%;accent-color:#e87722;margin-bottom:16px"></audio>'
        if audio else ""
    )
    thumb_tag = (
        f'<img src="{esc(thumb)}" alt="{esc(title)}"'
        f' style="width:100%;max-width:480px;border-radius:10px;margin-bottom:16px;object-fit:cover">'
        if thumb else ""
    )
    desc_tag  = f'<p style="font-size:.9rem;color:#444;line-height:1.7;white-space:pre-line;margin-top:16px">{esc(desc)}</p>' if desc else ""
    breadcrumb_title = (title[:60] + "…") if len(title) > 60 else title

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
      </div>
    </div>
    <a href="../paracha.html" data-i18n="nav_paracha">Paracha</a>
    <a href="../themes.html" data-i18n="nav_themes">Thème</a>
    <button class="lang-btn" onclick="toggleLang()" data-i18n="lang_toggle">עברית</button>
  </nav>
</header>
<main>
  <p class="breadcrumb"><a href="../">Accueil</a> › <a href="../{slug}.html">{esc(name)}</a> › {esc(breadcrumb_title)}</p>
  <div class="ep-hero">
    {thumb_tag}
    <h1>{esc(title)}</h1>
    <p class="ep-meta"><a href="../{slug}.html" style="color:#888;text-decoration:none">{esc(name)}</a> · <time datetime="{pub}">{fmt_date(ep["published"], lang)}{" · " + fmt_dur(ep.get("duration_secs",0)) if ep.get("duration_secs") else ""}</time></p>
    {f'<div style="margin-bottom:8px">{tags_html}</div>' if tags_html else ''}
    {audio_tag}
    <div class="speed-bar">
      <span>Vitesse :</span>
      <button class="speed-btn active" data-speed="1">1×</button>
      <button class="speed-btn" data-speed="1.25">1.25×</button>
      <button class="speed-btn" data-speed="1.5">1.5×</button>
      <button class="speed-btn" data-speed="2">2×</button>
    </div>
    <button class="share-btn" data-epfile="{esc(ep_path(slug, ep))}" data-title="{esc(title)}" style="margin-top:8px">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" width="13" height="13">
        <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/>
        <polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/>
      </svg> Partager cet épisode
    </button>
    {ep_platform_html}
    {desc_tag}
    {f'<details class="transcript"><summary data-i18n="transcript_label">Transcription</summary><div class="transcript-body">{esc(transcript)}</div></details>' if transcript else ''}
  </div>
  {f'<p class="related-label" data-i18n="related">Épisodes récents</p><div class="episode-list">{related_html}</div>' if related_html else ''}
  <div class="toast" id="toast"></div>
</main>
<script src="../js/utils.js"></script>
<script>
  const I18N = {{
    fr: {{
      nav_home:'Accueil', nav_rabbis:'Rabbins ▾', nav_last_classes:'Derniers cours', nav_daf_hayomi:'Daf Hayomi', nav_limud:'Limud Yomi', nav_hitat:'Hitat Yomi', nav_paracha:'Paracha', nav_themes:'Thème',
      lang_toggle:'עברית', subtitle:'Cours de Torah — disponibles sur vos plateformes favorites',
      related:'Épisodes récents', transcript_label:'Transcription',
    }},
    he: {{
      nav_home:'ראשי', nav_rabbis:'הרבנים ▾', nav_last_classes:'שיעורים אחרונים', nav_daf_hayomi:'דף היומי', nav_limud:'לימוד יומי', nav_hitat:'חת"ת', nav_paracha:'פרשה', nav_themes:'נושא',
      lang_toggle:'Français', subtitle:'שיעורי תורה — זמינים בפלטפורמות האהובות עליכם',
      related:'פרקים אחרונים', transcript_label:'תמליל',
    }},
  }};
  let lang = localStorage.getItem('lang') || '{lang}';
  function t(k) {{ const d = I18N[lang] || {{}}; return d[k] || I18N.fr[k] || k; }}
  function applyLang() {{
    document.documentElement.lang = lang;
    document.documentElement.dir  = lang === 'he' ? 'rtl' : 'ltr';
    document.querySelectorAll('[data-i18n]').forEach(el => {{ el.textContent = t(el.dataset.i18n); }});
  }}
  function toggleLang() {{
    localStorage.setItem('lang', lang === 'fr' ? 'he' : 'fr');
    location.reload();
  }}
  applyLang();
  document.addEventListener('click', e => {{
    if (e.target.closest('.nav-submenu')) return;
    const caret = e.target.closest('.nav-dd-caret');
    document.querySelectorAll('.nav-dropdown.open').forEach(el => {{
      if (!caret || el !== caret.closest('.nav-dropdown')) el.classList.remove('open');
    }});
    if (caret) caret.closest('.nav-dropdown').classList.toggle('open');
  }});
  let currentSpeed = parseFloat(localStorage.getItem('playbackSpeed') || '1');
  function applySpeed(rate) {{
    currentSpeed = rate;
    localStorage.setItem('playbackSpeed', rate);
    document.querySelectorAll('.ep-audio, #ep-audio').forEach(a => {{ a.playbackRate = rate; }});
    document.querySelectorAll('.speed-btn').forEach(b => b.classList.toggle('active', parseFloat(b.dataset.speed) === rate));
  }}
  document.querySelectorAll('.speed-btn').forEach(b => {{
    b.classList.toggle('active', parseFloat(b.dataset.speed) === currentSpeed);
    b.addEventListener('click', () => applySpeed(parseFloat(b.dataset.speed)));
  }});
  const mainAudio = document.getElementById('ep-audio');
  if (mainAudio) {{
    const saved = parseInt(localStorage.getItem('resume_{video_id}') || '0');
    if (saved > 5) mainAudio.addEventListener('loadedmetadata', () => {{ mainAudio.currentTime = saved; }}, {{once:true}});
    mainAudio.addEventListener('play', () => {{ mainAudio.playbackRate = currentSpeed; }});
    mainAudio.addEventListener('timeupdate', () => {{
      if (mainAudio.currentTime > 5) localStorage.setItem('resume_{video_id}', Math.floor(mainAudio.currentTime));
    }});
    mainAudio.addEventListener('ended', () => {{ localStorage.removeItem('resume_{video_id}'); }});
  }}
  document.querySelectorAll('.ep-audio[data-ep-id]').forEach(audio => {{
    const epId = audio.dataset.epId;
    const saved = parseInt(localStorage.getItem('resume_' + epId) || '0');
    if (saved > 5) audio.addEventListener('loadedmetadata', () => {{ audio.currentTime = saved; }}, {{once:true}});
    audio.addEventListener('play', () => {{ audio.playbackRate = currentSpeed; }});
    audio.addEventListener('timeupdate', () => {{
      if (audio.currentTime > 5) localStorage.setItem('resume_' + epId, Math.floor(audio.currentTime));
    }});
    audio.addEventListener('ended', () => {{ localStorage.removeItem('resume_' + epId); }});
  }});
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
</script>
<script>if('serviceWorker'in navigator)navigator.serviceWorker.register('/sw.js');</script>
</body>
</html>
"""


def update_sitemap(slug_entries: list[tuple]):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    slugs = [s for s, _ in slug_entries]
    channel_entries = "\n".join(
        f"  <url>\n"
        f"    <loc>{BASE_URL}/{slug}.html</loc>\n"
        f"    <lastmod>{today}</lastmod>\n"
        f"    <changefreq>daily</changefreq>\n"
        f"    <priority>0.9</priority>\n"
        f"  </url>"
        for slug in slugs
    )
    episode_entries = "\n".join(
        f"  <url>\n"
        f"    <loc>{BASE_URL}/{ep_path(slug, ep)}</loc>\n"
        f"    <lastmod>{ep['published'][:10]}</lastmod>\n"
        f"    <changefreq>never</changefreq>\n"
        f"    <priority>0.6</priority>\n"
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
        "thumbnail": episodes[0].get("thumbnail", "") if episodes else "",
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

    # Channel pages
    generated = []
    for ch, entries in all_data:
        slug = ch["slug"]
        page = render_page(ch, entries, enabled, site_channels, site_episodes, site_hours)
        out  = Path(f"{slug}.html")
        out.write_text(page, encoding="utf-8")
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
    entries_cache = {ch["slug"]: entries for ch, entries in all_data}
    for speaker in speakers:
        sp_episodes = []
        for ch_slug in speaker.get("from_channels", []):
            if ch_slug in entries_cache:
                sp_episodes += [
                    ep for ep in entries_cache[ch_slug]
                    if speaker_matches(ep.get("title", ""), speaker["title_patterns"])
                ]
        sp_episodes.sort(key=lambda x: x.get("published", ""), reverse=True)
        slug = speaker["slug"]
        page = render_speaker_page(speaker, sp_episodes, enabled, speakers,
                                   site_channels, site_episodes, site_hours)
        Path(f"{slug}.html").write_text(page, encoding="utf-8")
        print(f"  {slug}.html  ({len(sp_episodes)} episodes)")
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

    update_sitemap(generated)
    print(f"\nDone — {len(generated)} channel + {len(speakers)} speaker pages + {ep_count} episode pages.")


if __name__ == "__main__":
    main()
