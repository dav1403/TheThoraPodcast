/**
 * utils.js — shared utilities for all static pages.
 * Requires `window.lang` ('fr' | 'he') to be set by the page before use.
 */

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function slugify(title, maxLen = 70) {
  return title.toLowerCase().normalize('NFD')
    .replace(/\p{Mn}/gu, '')
    .replace(/[^a-z0-9א-תװ-״]+/gu, '-')
    .replace(/^-+|-+$/g, '').replace(/-+/g, '-')
    .slice(0, maxLen).replace(/-+$/, '');
}

function epUrl(ep, chSlug) {
  var s = slugify(ep.title || '');
  var prefix = chSlug + '-';
  if (s.startsWith(prefix)) s = s.slice(prefix.length) || (ep.video_id || 'episode');
  return chSlug + '/' + s + '-' + (ep.published || '').slice(0, 10) + '.html';
}

function formatDate(iso) {
  var locale = window.lang === 'he' ? 'he-IL' : (window.lang === 'en' ? 'en-US' : 'fr-FR');
  return new Date(iso).toLocaleDateString(locale, { day: 'numeric', month: 'long', year: 'numeric' });
}

function formatDuration(secs) {
  if (!secs || secs <= 0) return '';
  var h = Math.floor(secs / 3600);
  var m = Math.floor((secs % 3600) / 60);
  if (h > 0) return h + 'h' + (m > 0 ? String(m).padStart(2, '0') : '');
  return m + ' min';
}

function playIcon() {
  return '<svg viewBox="0 0 10 10" fill="currentColor"><polygon points="2,1 9,5 2,9"/></svg>';
}

function pauseIcon() {
  return '<svg viewBox="0 0 10 10" fill="currentColor"><rect x="1.5" y="1" width="2.5" height="8"/><rect x="6" y="1" width="2.5" height="8"/></svg>';
}

function shareIcon() {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" width="11" height="11">'
    + '<path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/>'
    + '<polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/></svg>';
}

// Duration filter — shared by all static pages
var _activeDur = 'all';
function filterDurAll(btn) {
  document.querySelectorAll('.dur-filter .dur-btn').forEach(function(b) { b.classList.remove('active'); });
  btn.classList.add('active');
  _activeDur = btn.dataset.dur;
  document.querySelectorAll('[data-ep-dur]').forEach(function(el) {
    var d = parseInt(el.dataset.epDur) || 0;
    if (_activeDur === 'all')    { el.hidden = false; return; }
    if (_activeDur === 'short')  { el.hidden = !(d > 0 && d < 300); return; }
    if (_activeDur === 'medium') { el.hidden = !(d >= 300 && d <= 1200); return; }
    if (_activeDur === 'long')   { el.hidden = !(d > 1200); return; }
  });
}
function setDur(btn) { filterDurAll(btn); }

// ─── Reusable horizontal carousel ────────────────────────────────────────────
// Drop-in carousel matching the homepage "recents" row. Injects its CSS once so
// it works on any page (static or generated) without per-page styling.
function _injectCarouselCSS() {
  if (document.getElementById('carousel-css')) return;
  var s = document.createElement('style');
  s.id = 'carousel-css';
  s.textContent =
    '.carousel-section{margin:0 0 28px}' +
    '.carousel-section .carousel-label{font-size:.78rem;text-transform:uppercase;letter-spacing:.08em;color:var(--color-text-faint, #999);font-weight:600;margin-bottom:10px}' +
    '.carousel-row{display:flex;gap:12px;overflow-x:auto;padding-bottom:6px;scrollbar-width:thin;scroll-snap-type:x proximity}' +
    '.carousel-row::-webkit-scrollbar{height:4px}' +
    '.carousel-row::-webkit-scrollbar-thumb{background:var(--color-border, #ddd);border-radius:2px}' +
    '.carousel-card{flex-shrink:0;width:150px;background:var(--color-surface, #fff);border-radius:10px;box-shadow:0 1px 4px var(--shadow-card, rgba(0,0,0,.08));overflow:hidden;transition:transform .15s,box-shadow .15s;scroll-snap-align:start}' +
    '.carousel-card:hover{transform:translateY(-2px);box-shadow:0 4px 12px rgba(0,0,0,.12)}' +
    '.carousel-card>a{display:block}' +
    '.carousel-card img{width:100%;height:84px;object-fit:cover;display:block}' +
    '.carousel-card-body{padding:8px 9px 10px}' +
    '.carousel-card-ch{font-size:.66rem;color:var(--color-accent, #e87722);font-weight:600;text-transform:uppercase;letter-spacing:.03em;margin-bottom:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.carousel-card-title{font-size:.8rem;line-height:1.25;color:var(--color-inverse-bg, #1a1a2e);text-decoration:none;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}' +
    '.carousel-card-date{font-size:.68rem;color:var(--color-text-disabled, #aaa);margin-top:5px}' +
    '.carousel-card.is-channel{width:128px;text-align:center}' +
    '.carousel-card.is-channel img{height:128px}' +
    '.carousel-card.is-channel .carousel-card-title{-webkit-line-clamp:2;font-weight:600}';
  document.head.appendChild(s);
}

// buildCarousel(label, items) -> HTMLElement | null
//   items: array of { ep, ch } for episode cards, or { ch } for channel ("discover") cards.
//   ch = { slug, name }. Returns null when there are no items.
function buildCarousel(label, items) {
  if (!items || !items.length) return null;
  _injectCarouselCSS();
  var cards = items.map(function(it) {
    var ch = it.ch, ep = it.ep;
    var isChannel = !ep;
    // Pre-computed indexes (home.json recents, latest.json episodes) already
    // carry the episode page URL the generator really wrote; epUrl() only
    // re-derives it from the title when the row comes straight from a feed.
    var href = isChannel ? (escapeHtml(ch.slug) + '.html') : escapeHtml(ep.url || epUrl(ep, ch.slug));
    var img  = (ep && ep.thumbnail) ? escapeHtml(ep.thumbnail) : ('artwork/' + escapeHtml(ch.slug) + '.png');
    var title = escapeHtml(isChannel ? (ch.name || ch.slug) : ep.title);
    return '<div class="carousel-card' + (isChannel ? ' is-channel' : '') + '" data-slug="' + escapeHtml(ch.slug) + '">' +
        '<a href="' + href + '"><img src="' + img + '" alt="" loading="lazy" onerror="this.src=\'artwork/' + escapeHtml(ch.slug) + '.png\'"></a>' +
        '<div class="carousel-card-body">' +
          (isChannel ? '' : '<div class="carousel-card-ch">' + escapeHtml(ch.name || '') + '</div>') +
          '<a class="carousel-card-title" href="' + href + '">' + title + '</a>' +
          (ep && ep.published ? '<div class="carousel-card-date">' + formatDate(ep.published) + '</div>' : '') +
        '</div>' +
      '</div>';
  }).join('');
  var section = document.createElement('div');
  section.className = 'carousel-section';
  section.innerHTML = '<div class="carousel-label">' + escapeHtml(label) + '</div><div class="carousel-row">' + cards + '</div>';
  return section;
}

// ─── Tiny i18n helper (fr/he) reading window.lang ────────────────────────────
// Standalone so pages/generated pages can label shared UI without their own I18N.
function _t2(fr, he) { return (window.lang === 'he') ? he : fr; }
// 3-language helper (fr | en | he). Falls back to fr for any other value.
function _t3(fr, en, he) { return window.lang === 'he' ? he : (window.lang === 'en' ? en : fr); }

// ─── Preferences module (single place for every persisted user setting) ──────
// Before this, every page read localStorage by hand ('lang', 'playbackSpeed',
// 'ttp_favorite_*'…). TTPPrefs is the one typed door: it validates values,
// falls back safely when localStorage is unavailable (private mode / iframe),
// and notifies listeners. Add new preferences here, never inline in a page.
//
// Keys owned here:
//   lang             — UI language of the SITE ('fr' | 'en' | 'he')
//   ttp_course_lang  — language SPOKEN in the classes ('all' | 'fr' | 'he')
// The two are deliberately independent: an Israeli may read the site in Hebrew
// and still want the French shiurim, and a French speaker may keep the site in
// French while listening only to Hebrew classes.
window.TTPPrefs = (function () {
  var DEFS = {
    lang:            { key: 'lang',            values: ['fr', 'en', 'he'],  def: 'fr'  },
    courseLang:      { key: 'ttp_course_lang', values: ['all', 'fr', 'he'], def: 'all' },
  };
  var listeners = {};

  function read(name) {
    var d = DEFS[name];
    var v = null;
    try { v = localStorage.getItem(d.key); } catch (_) {}
    return (d.values.indexOf(v) !== -1) ? v : d.def;
  }
  function write(name, value) {
    var d = DEFS[name];
    if (d.values.indexOf(value) === -1) return read(name);
    try { localStorage.setItem(d.key, value); } catch (_) {}
    (listeners[name] || []).forEach(function (fn) { try { fn(value); } catch (_) {} });
    return value;
  }
  return {
    get:  read,
    set:  write,
    on:   function (name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
    // Shorthands — the two settings read all over the site.
    uiLang:     function () { return read('lang'); },
    courseLang: function () { return read('courseLang'); },   // 'all' | 'fr' | 'he'
  };
})();

// ─── Course language: detection + filtering ──────────────────────────────────
// Mirrors scripts/lang_detect.py EXACTLY (dominant script of the title, channel
// `podcast_language` as the tie-break for letter-less titles). Baked values are
// preferred whenever present, so this fallback only ever runs on the pages that
// read `feeds/<slug>.entries.json`, which carries no per-episode language:
//   • home.json                → ep.lang
//   • search-index.json        → ep.l
//   • search-fts docs rows     → row[5]
//   • mobile/*.json rows       → row[7]
// Only 'fr' and 'he' ever come out — same contract as the Python side.
function _courseScriptCounts(text) {
  var hebrew = 0, latin = 0, s = String(text || '');
  for (var i = 0; i < s.length; i++) {
    var cp = s.charCodeAt(i);
    if (cp >= 0x0590 && cp <= 0x05FF) hebrew++;
    else if (cp < 0x0590 && /\p{L}/u.test(s[i])) latin++;
  }
  return [hebrew, latin];
}

// detectCourseLang(title, fallback) — the JS twin of lang_detect.detect_lang().
function detectCourseLang(title, fallback) {
  var c = _courseScriptCounts(title);
  if (c[0] === 0 && c[1] === 0) return (fallback === 'he') ? 'he' : 'fr';
  return c[0] > c[1] ? 'he' : 'fr';
}

// The language of one episode. `ch` is the channel record (for its
// `podcast_language` fallback) and may be omitted.
function episodeCourseLang(ep, ch) {
  if (!ep) return 'fr';
  if (ep.lang === 'fr' || ep.lang === 'he') return ep.lang;
  if (ep.l === 'fr' || ep.l === 'he') return ep.l;
  var fb = (ch && (ch.podcast_language || ch.lang)) || 'fr';
  return detectCourseLang(ep.title || ep.t || '', fb);
}

// True when the episode must be shown under the current preference.
function courseLangKeeps(ep, ch) {
  var pref = window.TTPPrefs.courseLang();
  return pref === 'all' || episodeCourseLang(ep, ch) === pref;
}

// Filter a raw `feeds/<slug>.entries.json` array. No-op when the pref is 'all',
// so the default path costs nothing.
function filterEntriesByCourseLang(entries, ch) {
  if (window.TTPPrefs.courseLang() === 'all') return entries || [];
  return (entries || []).filter(function (ep) { return courseLangKeeps(ep, ch); });
}

// One-stop fetch used by every page that reads a channel feed: fetches and
// applies the course-language filter, so a page only has to swap its fetch call
// and every list, count and chip downstream follows automatically.
function fetchChannelEntries(ch) {
  var slug = (typeof ch === 'string') ? ch : (ch && ch.slug);
  var chRec = (typeof ch === 'string') ? null : ch;
  return fetch('feeds/' + slug + '.entries.json')
    .then(function (r) { return r.ok ? r.json() : []; })
    .catch(function () { return []; })
    .then(function (entries) { return filterEntriesByCourseLang(entries, chRec); });
}

// Pick the count matching the active preference out of a precomputed record
// carrying `<base>`, `<base>_fr` and `<base>_he` (home.json stats/channels/
// spotlight, mobile manifest totals). Falls back to the total when the
// per-language figure is missing (older artefacts).
function courseLangCount(rec, base) {
  if (!rec) return 0;
  base = base || 'count';
  var pref = window.TTPPrefs.courseLang();
  if (pref === 'all') return rec[base] || 0;
  var v = rec[base + '_' + pref];
  return (typeof v === 'number') ? v : (rec[base] || 0);
}

// Last class of a rav, honouring the course-language preference, out of a
// home.json `channels[]` / `speakers[]` record. Returns null when he has no
// class at all (or none in the selected language) — never an invented date.
//
// ⚠️ The suffixed `last_*_fr` / `last_*_he` blocks exist ONLY for the ravs who
// really teach in both languages (build_home_json / _last_class_block): for a
// mono-language rav the suffixed copy would be byte-for-byte identical, so it
// is not emitted. Hence the documented consumer rule applied here: when
// `last_*_<lang>` is missing while `count_<lang> > 0`, the unsuffixed block IS
// that language's last class. Skipping this rule would silently show a French
// class to a visitor filtering on Hebrew.
function courseLangLast(rec) {
  if (!rec) return null;
  var pref = window.TTPPrefs.courseLang();
  var sfx = '';
  if (pref !== 'all') {
    if (!courseLangCount(rec, 'count')) return null;
    if (rec['last_published_' + pref] !== undefined) sfx = '_' + pref;
  }
  if (!rec['last_published' + sfx]) return null;
  return {
    published:     rec['last_published' + sfx],
    title:         rec['last_title' + sfx] || '',
    video_id:      rec['last_video_id' + sfx] || '',
    audio_url:     rec['last_audio_url' + sfx] || '',
    duration_secs: rec['last_duration_secs' + sfx] || 0,
  };
}

// ─── Language controls live in the MENU, not in the header banner ────────────
// Asked for by David (28/08/2026): both language selectors must sit in the site
// menu, not in the header. Same trick as TTPPlayer: the existing `.lang-switch`
// node is MOVED (appendChild), never re-created — so the per-page inline
// `onclick="setLang('fr')"` handlers keep working untouched, on the ~52
// generated pages AND on the hand-written ones, with no regeneration needed.
//
// Two menus exist on this site and BOTH get the controls:
//   • desktop → `.header-nav` (a `.nav-langs` block appended at its end)
//   • mobile  → the slide-in `.mnav-panel` (see _mnavLangSection below), which
//     cannot host the moved node because `.header-nav` is display:none there;
//     it gets its own buttons that simply .click() the real ones.
function _langMenuUiLabel()     { return _t3('Langue', 'Language', 'שפת האתר'); }
function _langMenuCourseLabel() { return _clT('label'); }
function _settingsLabel()       { return _t3('Réglages', 'Settings', 'הגדרות'); }

// The settings page lives at the site root, but this file is also loaded from
// episode pages one directory down (`../js/utils.js`) — hence the absolute path.
var TTP_SETTINGS_URL = '/reglages.html';
function _onSettingsPage() {
  return /\/reglages\.html$/.test(location.pathname);
}

function _injectLangMenuCSS() {
  if (document.getElementById('langmenu-css')) return;
  var s = document.createElement('style');
  s.id = 'langmenu-css';
  s.textContent =
    // Own full-width row at the bottom of the (wrapping flex) desktop nav,
    // separated by a rule so it never reads as one more navigation link.
    '.nav-langs{flex-basis:100%;width:100%;display:flex;flex-direction:column;align-items:center;' +
      'gap:6px;margin-top:12px;padding-top:10px;border-top:1px solid rgba(255,255,255,.14)}' +
    '.nav-lang-row{display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:8px}' +
    '.nav-lang-label{font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;' +
      'color:var(--color-inverse-scrim, rgba(255,255,255,.5));font-weight:600}' +
    '.nav-langs .lang-switch{margin:0}' +
    '.nav-langs .courselang-switch{margin:0}' +
    // The course switch carries its own label; inside the menu the shared
    // `.nav-lang-label` provides it, so hide the duplicate.
    '.nav-langs .courselang-label{display:none}' +
    // ⚙️ entry point to the full settings page, last row of the block.
    '.nav-settings-link{display:inline-flex;align-items:center;gap:6px;text-decoration:none;' +
      'font-size:.75rem;font-weight:600;color:var(--color-on-inverse-dim, rgba(255,255,255,.6));padding:5px 14px;' +
      'border:1px solid var(--color-inverse-border, rgba(255,255,255,.2));border-radius:20px;transition:color .15s,background .15s,border-color .15s}' +
    '.nav-settings-link:hover{color:var(--color-on-inverse, #fff);background:var(--color-inverse-hover, rgba(255,255,255,.1));border-color:var(--color-inverse-scrim, rgba(255,255,255,.5))}' +
    '.nav-settings-link[aria-current=page]{color:var(--color-inverse-bg, #1a1a2e);background:var(--color-on-inverse, #fff);border-color:var(--color-on-inverse, #fff)}';
  document.head.appendChild(s);
}

// Ensures the `.nav-langs` block exists at the end of the desktop nav and that
// the UI-language switch has been moved into it. Returns the block, or null on
// pages without a nav (embed.html…), where callers keep the old header layout.
function _langMenuBox() {
  var header = document.querySelector('header');
  var nav = header && header.querySelector('.header-nav');
  if (!nav) return null;
  _injectLangMenuCSS();
  var box = nav.querySelector('.nav-langs');
  if (!box) {
    box = document.createElement('div');
    box.className = 'nav-langs';
    nav.appendChild(box);
  }
  var uiRow = box.querySelector('[data-lang-row="ui"]');
  var sw = document.querySelector('.lang-switch');
  if (!uiRow && sw) {
    uiRow = document.createElement('div');
    uiRow.className = 'nav-lang-row';
    uiRow.setAttribute('data-lang-row', 'ui');
    var lbl = document.createElement('span');
    lbl.className = 'nav-lang-label';
    lbl.textContent = _langMenuUiLabel();
    uiRow.appendChild(lbl);
    box.insertBefore(uiRow, box.firstChild);
  }
  // MOVE (not clone): keeps the inline handlers and any page listener alive.
  if (uiRow && sw && sw.parentNode !== uiRow) uiRow.appendChild(sw);
  return box;
}

// ─── ⚙️ Settings page link (auto-injected, no page ever edited) ──────────────
// reglages.html gathers the same two preferences on a full-size surface. It is
// reachable from every page — hand-written or generated — because the link is
// appended here, next to the language controls, exactly like the course switch.
function _gearIcon() {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
    'width="13" height="13" aria-hidden="true" focusable="false">' +
    '<circle cx="12" cy="12" r="3"/>' +
    '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 ' +
      '1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 ' +
      '1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a' +
      '1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 ' +
      '0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 ' +
      '0 0 19.4 9v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>';
}

function _settingsLinkEl(cls) {
  var a = document.createElement('a');
  a.className = cls;
  a.href = TTP_SETTINGS_URL;
  a.innerHTML = _gearIcon() + '<span></span>';
  a.querySelector('span').textContent = _settingsLabel();
  if (_onSettingsPage()) a.setAttribute('aria-current', 'page');
  return a;
}

// Idempotent: the last row of the `.nav-langs` block is the settings link.
function _appendSettingsRow(box) {
  if (!box || box.querySelector('[data-lang-row="settings"]')) return;
  var row = document.createElement('div');
  row.className = 'nav-lang-row';
  row.setAttribute('data-lang-row', 'settings');
  row.appendChild(_settingsLinkEl('nav-settings-link'));
  box.appendChild(row);
}

// ─── Course-language control (auto-injected into every page menu) ────────────
// Sits under the UI-language switch, inside the menu. Deliberately worded so it
// can't be read as "language of the site": it carries its own label and spells
// the languages out ("Français"/"Hébreu"), where the site switch shows FR/EN/עב.
var COURSE_LANG_I18N = {
  fr: { label: 'Langue des cours', all: 'Toutes', fr: 'Français', he: 'Hébreu',
        empty: 'Aucun cours dans cette langue ici.', reset: 'Voir toutes les langues' },
  en: { label: 'Language of the classes', all: 'All', fr: 'French', he: 'Hebrew',
        empty: 'No class in this language here.', reset: 'Show all languages' },
  he: { label: 'שפת השיעורים', all: 'הכול', fr: 'צרפתית', he: 'עברית',
        empty: 'אין שיעורים בשפה זו כאן.', reset: 'הצג את כל השפות' },
};
function _clT(k) {
  var d = COURSE_LANG_I18N[window.TTPPrefs.uiLang()] || COURSE_LANG_I18N.fr;
  return d[k] || COURSE_LANG_I18N.fr[k] || k;
}

// Switching the preference reloads: every page computes its lists and counters
// at load from the filtered data, so a reload is both the simplest and the only
// way to guarantee nothing (chips, totals, hero, carousels) is left stale.
function setCourseLang(v) {
  if (v === window.TTPPrefs.courseLang()) return;
  window.TTPPrefs.set('courseLang', v);
  location.reload();
}

function _injectCourseLangCSS() {
  if (document.getElementById('courselang-css')) return;
  var s = document.createElement('style');
  s.id = 'courselang-css';
  s.textContent =
    '.courselang-switch{display:flex;width:fit-content;align-items:center;flex-wrap:wrap;justify-content:center;' +
      'gap:4px;margin:0 auto 14px;padding:4px 10px;border:1px solid rgba(255,255,255,.18);' +
      'border-radius:20px;background:var(--color-inverse-tint, rgba(255,255,255,.05))}' +
    '.courselang-label{font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;' +
      'color:var(--color-inverse-scrim, rgba(255,255,255,.5));font-weight:600;margin-inline-end:4px}' +
    '.courselang-opt{color:rgba(255,255,255,.62);font-size:.75rem;font-weight:600;padding:4px 11px;' +
      'border-radius:16px;border:none;background:none;cursor:pointer;font-family:inherit;' +
      'letter-spacing:.02em;transition:background .15s,color .15s}' +
    '.courselang-opt:hover{color:var(--color-on-inverse, #fff)}' +
    '.courselang-opt.active{background:var(--color-accent, #e87722);color:var(--color-on-accent, #fff)}' +
    '.courselang-empty{max-width:520px;margin:26px auto;padding:18px 20px;text-align:center;' +
      'border:1px dashed #d8d8e0;border-radius:12px;color:var(--color-text-muted, #666);font-size:.9rem}' +
    '.courselang-empty button{margin-top:10px;display:inline-block;border:none;cursor:pointer;' +
      'background:var(--color-accent, #e87722);color:var(--color-on-accent, #fff);font-family:inherit;font-weight:600;font-size:.82rem;' +
      'padding:8px 16px;border-radius:20px}';
  document.head.appendChild(s);
}

// Markup for the "this filter emptied the page" state — never leave a blank
// screen: always offer the way back to "Toutes".
function courseLangEmptyHtml() {
  _injectCourseLangCSS();
  return '<div class="courselang-empty">' + escapeHtml(_clT('empty')) +
    '<br><button type="button" onclick="setCourseLang(\'all\')">' +
    escapeHtml(_clT('reset')) + '</button></div>';
}
// True when the current page has an active (non-'all') course-language filter —
// pages use it to decide between "no result" and "no result *in this language*".
function courseLangFiltering() { return window.TTPPrefs.courseLang() !== 'all'; }

// Factory — a fresh, fully wired course-language switch. Split out of
// _buildCourseLangSwitch so the same control can be built more than once
// (desktop menu + mobile slide-in panel are two separate DOM subtrees).
function _courseLangSwitchEl() {
  _injectCourseLangCSS();
  var cur = window.TTPPrefs.courseLang();
  var box = document.createElement('div');
  box.className = 'courselang-switch';
  box.setAttribute('role', 'group');
  box.setAttribute('aria-label', _clT('label'));
  box.innerHTML = '<span class="courselang-label">' + escapeHtml(_clT('label')) + '</span>' +
    ['all', 'fr', 'he'].map(function (v) {
      return '<button type="button" class="courselang-opt' + (v === cur ? ' active' : '') +
        '" data-course-lang="' + v + '" aria-pressed="' + (v === cur) + '">' +
        escapeHtml(_clT(v)) + '</button>';
    }).join('');
  box.addEventListener('click', function (e) {
    var b = e.target.closest('[data-course-lang]');
    if (b) setCourseLang(b.getAttribute('data-course-lang'));
  });
  return box;
}

function _buildCourseLangSwitch() {
  var menu = _langMenuBox();                                     // moves .lang-switch too
  if (menu) {
    if (!menu.querySelector('[data-lang-row="course"]')) {        // idempotent
      var row = document.createElement('div');
      row.className = 'nav-lang-row';
      row.setAttribute('data-lang-row', 'course');
      var lbl = document.createElement('span');
      lbl.className = 'nav-lang-label';
      lbl.textContent = _langMenuCourseLabel();
      row.appendChild(lbl);
      row.appendChild(_courseLangSwitchEl());
      menu.appendChild(row);
    }
    _appendSettingsRow(menu);                                     // always last
    return;
  }
  // Fallback for pages with no nav (embed.html…): keep the historical header
  // placement rather than dropping the control entirely.
  if (document.querySelector('.courselang-switch')) return;      // idempotent
  var anchor = document.querySelector('header .lang-switch');
  var header = document.querySelector('header');
  if (!anchor && !header) return;
  var box = _courseLangSwitchEl();
  // `.courselang-switch` is display:flex (block-level), so inserting it right
  // after the inline-flex UI-language switch puts it on its own centered row.
  if (anchor && anchor.parentNode) anchor.insertAdjacentElement('afterend', box);
  else header.appendChild(box);
}

// Generated channel pages bake their episode list into static HTML, with the
// course language on each row (`data-ep-lang`, see generate_channel_pages.py).
// Most channels are single-language and nothing happens; the mixed ones
// (Rav-benizri, Nahal-Haim) get their other-language classes hidden, and the
// on-page counter — any [data-ep-count] element — is rewritten to match.
// No-op on pages without the attribute (older, not-yet-regenerated pages).
function _applyCourseLangToStaticList() {
  var pref = window.TTPPrefs.courseLang();
  var rows = document.querySelectorAll('[data-ep-lang]');
  if (!rows.length) return;
  var shown = 0;
  Array.prototype.forEach.call(rows, function (el) {
    var keep = (pref === 'all') || (el.getAttribute('data-ep-lang') === pref);
    el.hidden = !keep;
    if (keep) shown++;
  });
  Array.prototype.forEach.call(document.querySelectorAll('[data-ep-count]'), function (el) {
    el.textContent = String(shown);
  });
  if (shown === 0) {
    var list = rows[0].parentNode;
    if (list && !list.querySelector('.courselang-empty')) {
      list.insertAdjacentHTML('beforeend', courseLangEmptyHtml());
    }
  }
}

(function initCourseLangSwitch() {
  var run = function () { _buildCourseLangSwitch(); _applyCourseLangToStaticList(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);
  else run();
})();

// ─── Share row (WhatsApp-first) ──────────────────────────────────────────────
// A row of share buttons for an episode/page: WhatsApp (lead), Telegram, email,
// and copy-link. 100% client-side, no third-party tracking script — just plain
// share URLs + clipboard. Fires a GA4 `share_click` event when gtag is present.
function _injectShareCSS() {
  if (document.getElementById('share-css')) return;
  var s = document.createElement('style');
  s.id = 'share-css';
  s.textContent =
    '.share-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0}' +
    '.share-row .share-lbl{font-size:.75rem;color:var(--color-text-subtle, #888);margin-inline-end:2px}' +
    '.share-btn2{display:inline-flex;align-items:center;gap:6px;border:none;cursor:pointer;' +
      'font-family:inherit;font-size:.8rem;font-weight:600;color:var(--color-surface, #fff);border-radius:999px;' +
      'padding:7px 13px;text-decoration:none;line-height:1;transition:transform .12s ease,opacity .12s ease}' +
    '.share-btn2:hover{transform:translateY(-1px);opacity:.92}' +
    '.share-btn2 svg{width:15px;height:15px;display:block}' +
    '.share-wa{background:#25d366}.share-tg{background:#2aabee}' +
    '.share-em{background:#6b7280}.share-cp{background:var(--color-inverse-bg, #1a1a2e)}';
  document.head.appendChild(s);
}

function _waIcon()  { return '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12.04 2C6.58 2 2.13 6.45 2.13 11.9c0 2.1.55 4.05 1.6 5.8L2 22l4.45-1.17a9.9 9.9 0 0 0 5.6 1.7h.01c5.46 0 9.9-4.45 9.9-9.9C21.96 6.45 17.5 2 12.04 2Zm5.8 14.16c-.24.68-1.4 1.3-1.94 1.34-.5.05-1.13.07-1.82-.11-.42-.13-.96-.31-1.65-.6-2.9-1.26-4.8-4.19-4.95-4.39-.14-.19-1.18-1.57-1.18-3 0-1.42.75-2.12 1.01-2.41.27-.29.58-.36.78-.36.19 0 .39 0 .56.01.18.01.42-.07.66.5.24.58.83 2 .9 2.15.07.14.12.31.02.5-.09.19-.14.31-.28.48-.14.16-.29.36-.42.49-.14.14-.28.29-.12.56.16.27.72 1.18 1.55 1.92 1.06.95 1.96 1.24 2.24 1.38.27.14.43.12.59-.07.16-.19.68-.79.86-1.06.18-.27.36-.22.61-.13.24.09 1.55.73 1.82.86.27.14.45.2.51.31.07.11.07.64-.17 1.32Z"/></svg>'; }
function _tgIcon()  { return '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M21.94 4.5 2.9 11.84c-1.3.52-1.29 1.25-.24 1.57l4.88 1.52 1.88 5.94c.23.63.11.88.77.88.51 0 .74-.24 1.02-.51l2.44-2.37 4.94 3.65c.91.5 1.56.24 1.79-.84l3.24-15.28c.33-1.32-.5-1.92-1.35-1.53Zm-3.9 3.03-8.98 5.66-.55 3.62-.9-3.5 9.53-6c.44-.28.85-.13.53.22Z"/></svg>'; }
function _emIcon()  { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></svg>'; }
function _cpIcon()  { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>'; }

// Fire a lightweight GA4 event if gtag is loaded (no-op otherwise).
function _shareEvent(target, url) {
  try { if (typeof gtag === 'function') gtag('event', 'share_click', { method: target, item_id: url }); } catch (_) {}
}

// Small transient toast, self-contained (creates #ttp-toast if absent).
function shareToast(msg) {
  var el = document.getElementById('ttp-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'ttp-toast';
    el.style.cssText = 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:var(--color-inverse-bg, #1a1a2e);' +
      'color:var(--color-surface, #fff);padding:10px 18px;border-radius:999px;font-size:.85rem;z-index:9999;opacity:0;' +
      'transition:opacity .2s ease;pointer-events:none;box-shadow:0 4px 16px rgba(0,0,0,.28)';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.opacity = '1';
  clearTimeout(el._t);
  el._t = setTimeout(function () { el.style.opacity = '0'; }, 2000);
}

// Copy a URL to the clipboard, with a legacy fallback, then toast + GA4.
function shareCopyLink(url) {
  var done = function () { shareToast(_t3('Lien copié !', 'Link copied!', 'הקישור הועתק!')); _shareEvent('copy', url); };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(url).then(done, function () { _shareFallbackCopy(url, done); });
  } else { _shareFallbackCopy(url, done); }
}
function _shareFallbackCopy(url, done) {
  try {
    var ta = document.createElement('textarea');
    ta.value = url; ta.style.cssText = 'position:fixed;top:-1000px';
    document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta); done();
  } catch (_) { shareToast(url); }
}

// buildShareRow(url, title, opts) -> HTMLElement
//   url   : absolute episode/page URL to share
//   title : text prepended to the shared message
//   opts  : { compact:true } hides the "Partager" label (for tight card rows)
// WhatsApp leads (David's audience is WhatsApp-first); Telegram + email optional.
function buildShareRow(url, title, opts) {
  _injectShareCSS();
  opts = opts || {};
  var msg = (title ? title + ' — ' : '') + url;
  var wa = 'https://wa.me/?text=' + encodeURIComponent(msg);
  var tg = 'https://t.me/share/url?url=' + encodeURIComponent(url) + '&text=' + encodeURIComponent(title || '');
  var subject = title || _t3('Un cours de Torah', 'A Torah class', 'שיעור תורה');
  var em = 'mailto:?subject=' + encodeURIComponent(subject) + '&body=' + encodeURIComponent(msg);

  var row = document.createElement('div');
  row.className = 'share-row';
  var lbl = opts.compact ? '' : '<span class="share-lbl">' + _t3('Partager', 'Share', 'שיתוף') + '</span>';
  row.innerHTML = lbl +
    '<a class="share-btn2 share-wa" target="_blank" rel="noopener" href="' + wa + '" data-share="whatsapp">' +
      _waIcon() + '<span>WhatsApp</span></a>' +
    '<a class="share-btn2 share-tg" target="_blank" rel="noopener" href="' + tg + '" data-share="telegram">' +
      _tgIcon() + '<span>Telegram</span></a>' +
    '<a class="share-btn2 share-em" href="' + em + '" data-share="email">' +
      _emIcon() + '<span>Email</span></a>' +
    '<button type="button" class="share-btn2 share-cp" data-share="copy">' +
      _cpIcon() + '<span>' + _t3('Copier le lien', 'Copy link', 'העתק קישור') + '</span></button>';

  row.querySelector('.share-wa').addEventListener('click', function () { _shareEvent('whatsapp', url); });
  row.querySelector('.share-tg').addEventListener('click', function () { _shareEvent('telegram', url); });
  row.querySelector('.share-em').addEventListener('click', function () { _shareEvent('email', url); });
  row.querySelector('.share-cp').addEventListener('click', function () { shareCopyLink(url); });
  return row;
}

// ─── Resume playback ("Continue where you left off") ──────────────────────────
// The player already persists position under `resume_<epId>` (see index.html /
// generate_channel_pages.py). These helpers read that back for a "Reprendre" UI.
function getResumePosition(epId) {
  var raw = '0';
  try { raw = localStorage.getItem('resume_' + epId) || '0'; } catch (_) {}
  var v = parseInt(raw, 10);
  return (v > 5) ? v : 0;  // ignore <5s (matches the write threshold)
}
function formatMmSs(secs) {
  secs = Math.max(0, Math.floor(secs || 0));
  var m = Math.floor(secs / 60), s = secs % 60;
  return m + ':' + (s < 10 ? '0' : '') + s;
}
// Attach a "Reprendre à mm:ss" banner just above an <audio> element if a saved
// position exists. Clicking it seeks + plays. Non-destructive if none saved.
function attachResumeBanner(audioEl, epId) {
  if (!audioEl || !epId) return;
  var pos = getResumePosition(epId);
  if (!pos) return;
  if (audioEl.previousElementSibling && audioEl.previousElementSibling.classList &&
      audioEl.previousElementSibling.classList.contains('resume-banner')) return; // idempotent
  if (!document.getElementById('resume-css')) {
    var st = document.createElement('style');
    st.id = 'resume-css';
    st.textContent = '.resume-banner{display:inline-flex;align-items:center;gap:7px;background:#fff3e6;' +
      'color:#c65a00;border:1px solid #ffd8ad;border-radius:8px;padding:7px 12px;font-size:.82rem;' +
      'font-weight:600;cursor:pointer;margin-bottom:10px;font-family:inherit}' +
      '.resume-banner:hover{background:#ffe8d1}.resume-banner svg{width:14px;height:14px}';
    document.head.appendChild(st);
  }
  var b = document.createElement('button');
  b.type = 'button';
  b.className = 'resume-banner';
  b.innerHTML = '<svg viewBox="0 0 10 10" fill="currentColor"><polygon points="2,1 9,5 2,9"/></svg>' +
    _t3('Reprendre à ', 'Resume at ', 'המשך מ-') + formatMmSs(pos);
  b.addEventListener('click', function () {
    var seek = function () { try { audioEl.currentTime = pos; } catch (_) {} audioEl.play(); };
    if (audioEl.readyState >= 1) seek();
    else audioEl.addEventListener('loadedmetadata', seek, { once: true });
    b.remove();
  });
  audioEl.parentNode.insertBefore(b, audioEl);
}

// ─── Favorite EPISODES (localStorage only — no backend, no account) ──────────
// Distinct from favorite RAVS above. Stores rich objects so a "Mes favoris"
// page can list them without re-fetching the catalog.
//   { id, title, ch, url, thumb, date }
var FAV_EPS_KEY = 'ttp_favorite_episodes';

function getFavoriteEpisodes() {
  try {
    var v = JSON.parse(localStorage.getItem(FAV_EPS_KEY) || '[]');
    return Array.isArray(v) ? v : [];
  } catch (_) { return []; }
}
function isFavoriteEpisode(id) {
  return getFavoriteEpisodes().some(function (e) { return e && e.id === id; });
}
// Toggle by id. `meta` (title/ch/url/thumb/date) is stored when adding.
// Returns true if it is now favorited.
function toggleFavoriteEpisode(id, meta) {
  var favs = getFavoriteEpisodes();
  var i = favs.findIndex(function (e) { return e && e.id === id; });
  var nowFav;
  if (i === -1) { favs.unshift(Object.assign({ id: id }, meta || {})); nowFav = true; }
  else { favs.splice(i, 1); nowFav = false; }
  try { localStorage.setItem(FAV_EPS_KEY, JSON.stringify(favs)); } catch (_) {}
  return nowFav;
}

// Markup for an episode favorite-star. `meta` is JSON-embedded so a click can
// persist the full record. Reuses the shared star CSS/SVG from favStar above.
function favEpStarHtml(id, meta, variant) {
  _injectFavCSS();
  var fav = isFavoriteEpisode(id);
  var label = fav ? _t3('Retirer des favoris', 'Remove from favorites', 'הסר מהמועדפים') : _t3('Ajouter aux favoris', 'Add to favorites', 'הוסף למועדפים');
  var m = escapeHtml(JSON.stringify(meta || {}));
  return '<span class="fav-star fav-ep' + (variant ? ' ' + variant : '') + (fav ? ' is-fav' : '') +
    '" role="button" tabindex="0" aria-pressed="' + fav + '" aria-label="' + label +
    '" title="' + label + '" data-fav-ep="' + escapeHtml(id) + '" data-fav-meta="' + m +
    '" onclick="favEpToggle(event,this)" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){favEpToggle(event,this)}">' +
    starSvg(fav) + '</span>';
}

function favEpToggle(evt, el) {
  if (evt) { evt.preventDefault(); evt.stopPropagation(); }
  var id = el.getAttribute('data-fav-ep');
  var meta = {};
  try { meta = JSON.parse(el.getAttribute('data-fav-meta') || '{}'); } catch (_) {}
  var nowFav = toggleFavoriteEpisode(id, meta);
  var label = nowFav ? _t3('Retirer des favoris', 'Remove from favorites', 'הסר מהמועדפים') : _t3('Ajouter aux favoris', 'Add to favorites', 'הוסף למועדפים');
  document.querySelectorAll('.fav-ep').forEach(function (s) {
    if (s.getAttribute('data-fav-ep') !== id) return;
    s.classList.toggle('is-fav', nowFav);
    s.setAttribute('aria-pressed', nowFav);
    s.setAttribute('aria-label', label);
    s.setAttribute('title', label);
    s.innerHTML = starSvg(nowFav);
  });
  document.dispatchEvent(new CustomEvent('favepchange', { detail: { id: id, fav: nowFav } }));
}

// ─── Favorite ravs (localStorage only — no backend, no account) ──────────────
// Persisted as a JSON array of channel/speaker slugs under a single key.
var FAV_RAVS_KEY = 'ttp_favorite_ravs';

function getFavoriteRavs() {
  try {
    var v = JSON.parse(localStorage.getItem(FAV_RAVS_KEY) || '[]');
    return Array.isArray(v) ? v : [];
  } catch (_) { return []; }
}

function isFavoriteRav(slug) {
  return getFavoriteRavs().indexOf(slug) !== -1;
}

// Toggle a slug's favorite state. Returns true if it is now favorited.
function toggleFavoriteRav(slug) {
  var favs = getFavoriteRavs();
  var i = favs.indexOf(slug);
  if (i === -1) favs.push(slug); else favs.splice(i, 1);
  try { localStorage.setItem(FAV_RAVS_KEY, JSON.stringify(favs)); } catch (_) {}
  return i === -1;
}

function starSvg(filled) {
  var pts = '12,2 15.09,8.26 22,9.27 17,14.14 18.18,21.02 12,17.77 5.82,21.02 7,14.14 2,9.27 8.91,8.26';
  return filled
    ? '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><polygon points="' + pts + '"/></svg>'
    : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><polygon points="' + pts + '"/></svg>';
}

// Inject the shared favorite-star CSS once, so any page (static or generated)
// renders stars consistently without duplicating styles.
function _injectFavCSS() {
  if (document.getElementById('fav-css')) return;
  var s = document.createElement('style');
  s.id = 'fav-css';
  s.textContent =
    '.fav-star{display:inline-flex;align-items:center;justify-content:center;cursor:pointer;color:#c4c4cc;width:1.5em;height:1.5em;transition:transform .12s ease,color .12s ease;-webkit-tap-highlight-color:transparent}' +
    '.fav-star svg{width:100%;height:100%;display:block}' +
    '.fav-star:hover{transform:scale(1.15);color:#f5b301}' +
    '.fav-star.is-fav{color:#f5b301}' +
    '.fav-star:focus-visible{outline:2px solid #f5b301;outline-offset:2px;border-radius:4px}' +
    '.fav-star.on-bubble{position:absolute;top:-3px;right:-3px;width:26px;height:26px;padding:4px;background:rgba(255,255,255,.94);border-radius:50%;box-shadow:0 1px 4px rgba(0,0,0,.22)}' +
    '.fav-star.on-channel{vertical-align:middle;margin-inline-start:8px;width:1.5em;height:1.5em}';
  document.head.appendChild(s);
}

// Build the markup for a favorite-star toggle bound to a given slug.
// `variant` is an optional extra class (e.g. 'on-bubble', 'on-channel').
function favStarHtml(slug, variant) {
  _injectFavCSS();
  var fav = isFavoriteRav(slug);
  var label = fav ? 'Retirer des favoris' : 'Ajouter aux favoris';
  return '<span class="fav-star' + (variant ? ' ' + variant : '') + (fav ? ' is-fav' : '') +
    '" role="button" tabindex="0" aria-pressed="' + fav + '" aria-label="' + label +
    '" title="' + label + '" data-fav-slug="' + escapeHtml(slug) +
    '" onclick="favStarToggle(event,this)" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){favStarToggle(event,this)}">' +
    starSvg(fav) + '</span>';
}

// Click/keyboard handler for a favorite star. Toggles the slug, updates every
// star for that slug on the page, and fires a `favchange` event so views
// (e.g. the "Mes favoris" filter) can react.
function favStarToggle(evt, el) {
  if (evt) { evt.preventDefault(); evt.stopPropagation(); }
  var slug = el.getAttribute('data-fav-slug');
  var nowFav = toggleFavoriteRav(slug);
  var label = nowFav ? 'Retirer des favoris' : 'Ajouter aux favoris';
  document.querySelectorAll('.fav-star').forEach(function(s) {
    if (s.getAttribute('data-fav-slug') !== slug) return;
    s.classList.toggle('is-fav', nowFav);
    s.setAttribute('aria-pressed', nowFav);
    s.setAttribute('aria-label', label);
    s.setAttribute('title', label);
    s.innerHTML = starSvg(nowFav);
  });
  document.dispatchEvent(new CustomEvent('favchange', { detail: { slug: slug, fav: nowFav } }));
}

// ─── Mobile slide-in navigation (hamburger) ─────────────────────────────────
// A real off-canvas menu for phones/tablets. Instead of hard-coding a nav into
// every page, this clones the page's existing `.header-nav` into a slide-in
// panel, so any page (static or CI-generated) gets a coherent mobile menu from
// the single shared file — no per-page markup and no regeneration drift.
function _injectMobileNavCSS() {
  if (document.getElementById('mnav-css')) return;
  var s = document.createElement('style');
  s.id = 'mnav-css';
  s.textContent =
    '.mnav-toggle{display:none}.mnav-overlay{display:none}.mnav-panel{display:none}' +
    // Panel interior (rendered only on mobile, where the panel is shown)
    '.mnav-head{display:flex;align-items:center;justify-content:space-between;padding:18px 18px 12px;border-bottom:1px solid var(--color-inverse-border-faint, rgba(255,255,255,.12))}' +
    '.mnav-title{font-size:1.05rem;font-weight:600}' +
    '.mnav-close{background:none;border:none;color:rgba(255,255,255,.72);font-size:1.5rem;line-height:1;cursor:pointer;padding:4px 8px;font-family:inherit}' +
    '.mnav-close:hover{color:var(--color-on-inverse, #fff)}' +
    '.mnav-list{padding:8px 0 40px}' +
    '.mnav-item{display:block;color:rgba(255,255,255,.82);text-decoration:none;font-size:1rem;padding:13px 20px;border:none;background:none;width:100%;text-align:left;font-family:inherit;cursor:pointer;box-sizing:border-box}' +
    '[dir=rtl] .mnav-item{text-align:right}' +
    '.mnav-item:hover{background:rgba(255,255,255,.06);color:var(--color-on-inverse, #fff)}' +
    '.mnav-item.active{color:var(--color-on-inverse, #fff);font-weight:600;box-shadow:inset 3px 0 0 var(--color-accent, #e87722)}' +
    '[dir=rtl] .mnav-item.active{box-shadow:inset -3px 0 0 var(--color-accent, #e87722)}' +
    '.mnav-group{border-bottom:1px solid var(--color-inverse-tint, rgba(255,255,255,.05))}' +
    '.mnav-grouphead{display:flex;align-items:center}' +
    '.mnav-grouplink{flex:1}' +
    '.mnav-acc{background:none;border:none;color:var(--color-on-inverse-dim, rgba(255,255,255,.6));font-size:1rem;padding:13px 20px;cursor:pointer;transition:transform .2s;font-family:inherit}' +
    '.mnav-group.open .mnav-acc{transform:rotate(180deg);color:var(--color-on-inverse, #fff)}' +
    '.mnav-sub{max-height:0;overflow:hidden;transition:max-height .28s ease;background:rgba(0,0,0,.22)}' +
    '.mnav-group.open .mnav-sub{max-height:1400px}' +
    '.mnav-subitem{display:block;color:rgba(255,255,255,.66);text-decoration:none;font-size:.9rem;padding:11px 20px 11px 34px}' +
    '[dir=rtl] .mnav-subitem{padding:11px 34px 11px 20px}' +
    '.mnav-subitem:hover{background:rgba(255,255,255,.06);color:var(--color-on-inverse, #fff)}' +
    // Language block, pinned at the bottom of the panel behind a rule
    '.mnav-langs{margin-top:8px;padding:12px 20px 8px;border-top:1px solid rgba(255,255,255,.14)}' +
    '.mnav-lang-label{display:block;font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;' +
      'color:var(--color-inverse-scrim, rgba(255,255,255,.5));font-weight:600;margin:10px 0 8px}' +
    '.mnav-lang-row{display:flex;flex-wrap:wrap;gap:6px}' +
    '.mnav-lang-opt{min-height:44px;padding:10px 16px;border-radius:22px;font-family:inherit;' +
      'border:1px solid var(--color-inverse-border, rgba(255,255,255,.2));background:var(--color-inverse-tint, rgba(255,255,255,.05));' +
      'color:var(--color-on-inverse-soft, rgba(255,255,255,.78));font-size:.85rem;font-weight:600;cursor:pointer}' +
    '.mnav-lang-opt.active{background:var(--color-accent, #e87722);border-color:var(--color-accent, #e87722);color:var(--color-on-accent, #fff)}' +
    '.mnav-settings-link{display:inline-flex;align-items:center;gap:8px;text-decoration:none;' +
      'margin-top:18px;min-height:44px;padding:10px 16px;border-radius:22px;' +
      'border:1px solid var(--color-inverse-border, rgba(255,255,255,.2));background:var(--color-inverse-tint, rgba(255,255,255,.05));' +
      'color:var(--color-on-inverse-soft, rgba(255,255,255,.78));font-size:.85rem;font-weight:600}' +
    '.mnav-settings-link:hover{color:var(--color-on-inverse, #fff);background:var(--color-inverse-hover, rgba(255,255,255,.1))}' +
    '.mnav-settings-link[aria-current=page]{background:var(--color-accent, #e87722);border-color:var(--color-accent, #e87722);color:var(--color-on-accent, #fff)}' +
    // Show the mobile menu, hide the desktop nav, at tablet width and below
    '@media(max-width:768px){' +
      'header{position:relative}' +
      '.header-nav{display:none!important}' +
      '.mnav-toggle{display:flex;flex-direction:column;justify-content:center;gap:5px;position:absolute;top:16px;left:14px;width:42px;height:42px;padding:10px;background:rgba(255,255,255,.08);border:1px solid var(--color-inverse-border, rgba(255,255,255,.2));border-radius:10px;cursor:pointer;z-index:120}' +
      '[dir=rtl] .mnav-toggle{left:auto;right:14px}' +
      '.mnav-toggle span{display:block;height:2px;width:100%;background:var(--color-on-inverse, #fff);border-radius:2px}' +
      '.mnav-overlay{display:block;position:fixed;inset:0;background:var(--shadow-overlay, rgba(0,0,0,.5));opacity:0;visibility:hidden;transition:opacity .25s ease;z-index:998}' +
      '.mnav-overlay.open{opacity:1;visibility:visible}' +
      '.mnav-panel{display:flex;flex-direction:column;position:fixed;top:0;right:0;height:100%;width:min(84vw,320px);background:#14142a;color:var(--color-on-inverse, #fff);transform:translateX(100%);transition:transform .25s ease;z-index:999;box-shadow:-6px 0 24px rgba(0,0,0,.4);overflow-y:auto;-webkit-overflow-scrolling:touch}' +
      '[dir=rtl] .mnav-panel{right:auto;left:0;transform:translateX(-100%);box-shadow:6px 0 24px rgba(0,0,0,.4)}' +
      '.mnav-panel.open{transform:translateX(0)}' +
    '}' +
    'body.mnav-lock{overflow:hidden}';
  document.head.appendChild(s);
}

// Language block for the slide-in mobile menu. The real `.lang-switch` lives in
// `.header-nav`, which is display:none on mobile, so we cannot move it here:
// instead each button simply .click()s its original, which fires the page's own
// `onclick="setLang(...)"` (localStorage + location.reload) unchanged.
function _mnavLangSection() {
  var wrap = document.createElement('div');
  wrap.className = 'mnav-langs';

  function row(labelText, ariaLabel) {
    var lbl = document.createElement('span');
    lbl.className = 'mnav-lang-label';
    lbl.textContent = labelText;
    var r = document.createElement('div');
    r.className = 'mnav-lang-row';
    r.setAttribute('role', 'group');
    r.setAttribute('aria-label', ariaLabel || labelText);
    wrap.appendChild(lbl);
    wrap.appendChild(r);
    return r;
  }
  function opt(text, isActive, ariaLabel, onClick) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'mnav-lang-opt' + (isActive ? ' active' : '');
    b.textContent = text;
    b.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    if (ariaLabel) b.setAttribute('aria-label', ariaLabel);
    b.addEventListener('click', onClick);
    return b;
  }

  var sw = document.querySelector('.lang-switch');
  if (sw) {
    // Read the active language from TTPPrefs, not from the `.active` class:
    // that class is set by each page's own applyLang(), whose timing varies.
    var cur = window.TTPPrefs.uiLang();
    var uiRow = row(_langMenuUiLabel(), _t3('Langue du site', 'Site language', 'שפת האתר'));
    Array.prototype.forEach.call(sw.querySelectorAll('[data-lang]'), function (orig) {
      var v = orig.getAttribute('data-lang');
      uiRow.appendChild(opt(orig.textContent.trim(), v === cur, null, function () {
        orig.click();
      }));
    });
  }

  var curCourse = window.TTPPrefs.courseLang();
  var cRow = row(_langMenuCourseLabel(), _clT('label'));
  ['all', 'fr', 'he'].forEach(function (v) {
    cRow.appendChild(opt(_clT(v), v === curCourse, null, function () { setCourseLang(v); }));
  });

  // Same ⚙️ entry point as the desktop menu, at the very bottom of the panel.
  wrap.appendChild(_settingsLinkEl('mnav-settings-link'));
  return wrap;
}

function _buildMobileNav() {
  var header = document.querySelector('header');
  var nav = header && header.querySelector('.header-nav');
  if (!header || !nav) return;
  if (header.querySelector('.mnav-toggle')) return; // idempotent

  _injectMobileNavCSS();
  var isHe = document.documentElement.lang === 'he';

  var toggle = document.createElement('button');
  toggle.className = 'mnav-toggle';
  toggle.setAttribute('aria-label', isHe ? 'תפריט' : 'Menu');
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', 'mnav-panel');
  toggle.innerHTML = '<span></span><span></span><span></span>';
  header.appendChild(toggle);

  var overlay = document.createElement('div');
  overlay.className = 'mnav-overlay';

  var panel = document.createElement('nav');
  panel.className = 'mnav-panel';
  panel.id = 'mnav-panel';
  panel.setAttribute('aria-label', isHe ? 'ניווט' : 'Navigation');

  var head = document.createElement('div');
  head.className = 'mnav-head';
  var title = document.createElement('span');
  title.className = 'mnav-title';
  title.textContent = isHe ? 'תפריט' : 'Menu';
  var close = document.createElement('button');
  close.className = 'mnav-close';
  close.setAttribute('aria-label', isHe ? 'סגור' : 'Fermer');
  close.innerHTML = '&#10005;';
  head.appendChild(title);
  head.appendChild(close);
  panel.appendChild(head);

  var list = document.createElement('div');
  list.className = 'mnav-list';
  panel.appendChild(list);

  Array.prototype.forEach.call(nav.children, function(node) {
    if (node.classList && node.classList.contains('nav-dropdown')) {
      var ddLink = node.querySelector('.nav-dd-link');
      var submenu = node.querySelector('.nav-submenu');
      var group = document.createElement('div');
      group.className = 'mnav-group';
      var row = document.createElement('div');
      row.className = 'mnav-grouphead';
      var a = document.createElement('a');
      a.className = 'mnav-item mnav-grouplink';
      a.href = ddLink ? ddLink.getAttribute('href') : '#';
      a.textContent = ddLink ? ddLink.textContent.replace(/\s*[▾▼]\s*$/, '').trim() : '';
      var acc = document.createElement('button');
      acc.className = 'mnav-acc';
      acc.setAttribute('aria-label', a.textContent);
      acc.setAttribute('aria-expanded', 'false');
      acc.innerHTML = '&#9662;';
      row.appendChild(a);
      row.appendChild(acc);
      group.appendChild(row);
      var sub = document.createElement('div');
      sub.className = 'mnav-sub';
      // Some submenus (e.g. the homepage "Rabbins" list) are filled by JS after
      // load, so mirror the live source each time the accordion opens rather
      // than snapshotting an empty list at build time.
      var syncSub = function() {
        var links = submenu ? submenu.querySelectorAll('a') : [];
        if (sub.children.length === links.length) return;
        sub.textContent = '';
        Array.prototype.forEach.call(links, function(sa) {
          var sc = document.createElement('a');
          sc.className = 'mnav-subitem';
          sc.href = sa.getAttribute('href');
          sc.textContent = sa.textContent.trim();
          sub.appendChild(sc);
        });
      };
      syncSub();
      group.appendChild(sub);
      acc.addEventListener('click', function() {
        syncSub();
        var open = group.classList.toggle('open');
        acc.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
      list.appendChild(group);
    } else if (node.tagName === 'A') {
      var c = document.createElement('a');
      c.className = 'mnav-item';
      c.href = node.getAttribute('href');
      c.textContent = node.textContent.trim();
      if (node.classList.contains('active')) c.classList.add('active');
      list.appendChild(c);
    } else if (node.classList && node.classList.contains('lang-btn')) {
      var lb = document.createElement('button');
      lb.className = 'mnav-item mnav-lang';
      lb.textContent = node.textContent.trim();
      lb.addEventListener('click', function() {
        if (typeof window.toggleLang === 'function') window.toggleLang();
      });
      list.appendChild(lb);
    }
  });

  // Language selectors last, behind a separating rule (David, 28/08/2026:
  // they belong to the menu, not to the header banner).
  try { list.appendChild(_mnavLangSection()); } catch (_) {}

  document.body.appendChild(overlay);
  document.body.appendChild(panel);

  function openMenu() {
    panel.classList.add('open');
    overlay.classList.add('open');
    document.body.classList.add('mnav-lock');
    toggle.setAttribute('aria-expanded', 'true');
    close.focus();
  }
  function closeMenu() {
    panel.classList.remove('open');
    overlay.classList.remove('open');
    document.body.classList.remove('mnav-lock');
    toggle.setAttribute('aria-expanded', 'false');
  }
  toggle.addEventListener('click', openMenu);
  close.addEventListener('click', closeMenu);
  overlay.addEventListener('click', closeMenu);
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && panel.classList.contains('open')) closeMenu();
  });
  list.addEventListener('click', function(e) {
    if (e.target.closest('a.mnav-item, a.mnav-subitem')) closeMenu();
  });
}

(function initMobileNav() {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _buildMobileNav);
  } else {
    _buildMobileNav();
  }
})();

// Auto-load header stats if #header-stats exists and isn't already populated
(function loadHeaderStats() {
  var el = document.getElementById('header-stats');
  if (!el || el.textContent.trim()) return;
  fetch('channels.json').then(function(r) { return r.ok ? r.json() : []; }).then(function(channels) {
    var enabled = channels.filter(function(c) { return c.enabled; });
    // Entries come back already filtered by the course-language preference, so
    // the header total never claims 31 624 classes while only 5 099 are shown.
    return Promise.all(enabled.map(fetchChannelEntries)).then(function(results) {
      var totalEp = results.reduce(function(s, eps) { return s + eps.length; }, 0);
      // Under a filter, a channel with nothing left in that language is not
      // counted either — otherwise "22 rabbins · 5 099 cours" would be a lie.
      var nCh = results.filter(function(eps) { return eps.length > 0; }).length;
      var totalH  = Math.round(totalEp * 0.75);
      var lang = window.TTPPrefs.uiLang();
      el.textContent = lang === 'he'
        ? nCh + ' ערוצים · ' + totalEp + ' שיעורים · ~' + totalH + ' שעות'
        : (lang === 'en' ? nCh + ' rabbis · ' + totalEp + ' classes · ~' + totalH + 'h of Torah'
                         : nCh + ' rabbins · ' + totalEp + ' cours · ~' + totalH + 'h de Torah');
    });
  }).catch(function() {});
})();

// ─── Persistent bottom player — SINGLE SOURCE for every page ─────────────────
// Before this block the bar existed in three unsynchronised copies (index.html,
// the generated-page template in scripts/generate_channel_pages.py, and a few
// hand-written pages), each with its own CSS. The markup, the CSS and the
// controls now live here only, following the same auto-injection pattern as
// _buildCourseLangSwitch()/_injectCourseLangCSS() above.
//
// Two ways in:
//   • TTPPlayer.load({id,title,channel,art,src})  → plays in the bar's own <audio>
//   • TTPPlayer.attach(audioEl, meta)             → the bar drives an existing
//     <audio> (episode pages), so their resume/speed/GA listeners stay live.
//
// Legacy pages that still ship their own `<div id="player">` are UPGRADED in
// place: the existing #player-audio / #player-title / #player-channel /
// #player-art / #player-close nodes are MOVED into the new layout (never
// recreated), so every listener those pages registered keeps working. Their
// old `#speed-cycle-btn` is kept in the DOM but hidden — the bar owns speed now.
//
// The native `<audio controls>` widget is gone: it looked and behaved
// differently in every browser (Chrome's ⋮ menu vs Safari's AirPlay), ate twice
// the width of the title, and duplicated the speed control.
var TTP_NOW_KEY = 'ttp_now_playing';
var TTP_NOW_MAX_AGE = 7 * 24 * 3600 * 1000;   // a restored bar older than a week is noise

function _ttpFmtTime(s) {
  s = Math.max(0, Math.floor(s || 0));
  var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  var mm = (h > 0 && m < 10) ? '0' + m : String(m);
  return (h > 0 ? h + ':' : '') + mm + ':' + (x < 10 ? '0' : '') + x;
}

// Circular-arrow skip icon with the offset written inside it (±15 / +30).
function _ttpSkipIcon(back, n) {
  var arrow = back ? 'M12 5V2L8 6l4 4V7a5.5 5.5 0 1 1-5.5 5.5'
                   : 'M12 5V2l4 4-4 4V7a5.5 5.5 0 1 0 5.5 5.5';
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' +
    '<path d="' + arrow + '"/>' +
    '<text x="12" y="16.2" text-anchor="middle" font-size="7.6" font-weight="700" ' +
    'fill="currentColor" stroke="none">' + n + '</text></svg>';
}

window.TTPPlayer = (function () {
  var SPEEDS = [1, 1.25, 1.5, 2];
  var root = null, cur = null, meta = {}, built = false;
  var elTitle, elCh, elArt, elTime, elFill, elSeek, elPlay, elSpeed, elClose;
  var pendingSeek = 0, restoredPos = 0, lastNowWrite = 0, msBound = null;

  function speed() {
    var v = parseFloat(localStorage.getItem('playbackSpeed') || '1');
    return (SPEEDS.indexOf(v) === -1) ? 1 : v;
  }

  function _injectCSS() {
    if (document.getElementById('ttp-player-css')) return;
    var s = document.createElement('style');
    s.id = 'ttp-player-css';
    s.textContent =
      '#player{position:fixed;bottom:0;left:0;right:0;display:block;padding:0;gap:0;' +
        'background:var(--color-inverse-bg, #1a1a2e);color:var(--color-surface, #fff);z-index:200;box-shadow:0 -2px 16px rgba(0,0,0,.28);' +
        'transform:translateY(0);transition:transform .25s ease;font-family:inherit}' +
      '#player.hidden{transform:translateY(110%)}' +
      // 2 px hairline at the very top of the bar (mirrors the app mini-player),
      // with an invisible taller strip around it so it stays tappable.
      '#player-seek{position:relative;height:2px;background:rgba(255,255,255,.16);cursor:pointer;' +
        'touch-action:none;outline:none}' +
      '#player-seek::before{content:"";position:absolute;left:0;right:0;top:-9px;bottom:-7px}' +
      '#player-seek:focus-visible{box-shadow:0 0 0 2px var(--color-accent, #e87722)}' +
      '#player-bar-fill{height:100%;width:0;background:var(--color-accent, #e87722);pointer-events:none}' +
      '.ttp-pbody{display:flex;align-items:center;gap:10px;flex-wrap:nowrap;' +
        'padding:7px 12px;padding-bottom:calc(7px + env(safe-area-inset-bottom,0px))}' +
      '#player-art{width:42px;height:42px;border-radius:6px;object-fit:cover;background:var(--color-text-secondary, #333);' +
        'flex:0 0 auto;display:block}' +
      // The title takes what is left — the controls no longer get twice its width.
      '#player-info{flex:1 1 auto;min-width:0}' +
      '#player-title{font-size:.82rem;font-weight:600;line-height:1.25;white-space:normal;' +
        'display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;' +
        'text-overflow:clip}' +
      '#player-sub{display:flex;align-items:center;gap:6px;margin-top:2px;font-size:.68rem;' +
        'color:#a3a3bb;line-height:1.3;min-width:0}' +
      '#player-channel{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}' +
      '#player-time{flex:0 0 auto;font-variant-numeric:tabular-nums;white-space:nowrap}' +
      '#player-audio{display:none!important}' +
      '#player #speed-cycle-btn{display:none!important}' +   // legacy duplicate, kept for old page JS
      '.ttp-ctrls{display:flex;align-items:center;gap:1px;flex:0 0 auto}' +
      '.ttp-btn{display:inline-flex;align-items:center;justify-content:center;background:none;' +
        'border:none;color:rgba(255,255,255,.82);font-family:inherit;cursor:pointer;padding:0;' +
        'height:44px;border-radius:10px;-webkit-tap-highlight-color:transparent;' +
        'transition:background .12s,color .12s}' +
      '.ttp-btn:hover{background:var(--color-inverse-hover, rgba(255,255,255,.1));color:var(--color-surface, #fff)}' +
      '.ttp-btn:focus-visible{outline:2px solid var(--color-accent, #e87722);outline-offset:-2px}' +
      '.ttp-skip{width:38px}.ttp-skip svg{width:22px;height:22px;display:block}' +
      '#ttp-speed-btn{width:42px;font-size:.72rem;font-weight:700;letter-spacing:.01em}' +
      '#ttp-speed-btn.active{color:var(--color-accent, #e87722)}' +
      '#player-close{width:34px;font-size:1.05rem;line-height:1;color:#8a8aa5}' +
      '#player-close:hover{color:var(--color-surface, #fff)}' +
      // Round, high-contrast play/pause — the one control everybody looks for.
      '#ttp-play-btn{width:46px;height:46px;border-radius:50%;background:var(--color-accent, #e87722);color:var(--color-on-accent, #fff);' +
        'flex:0 0 auto;margin:0 2px}' +
      '#ttp-play-btn:hover{background:#f2872f;color:var(--color-surface, #fff)}' +
      '#ttp-play-btn svg{width:15px;height:15px;display:block;margin-inline-start:1px}' +
      '#ttp-play-btn.is-playing svg{margin-inline-start:0}' +
      // 414 px and below: the artwork is the first thing that can go.
      '@media(max-width:480px){#player-art{display:none}.ttp-pbody{gap:8px;padding-left:10px;padding-right:8px}}' +
      '@media(max-width:360px){.ttp-skip{width:34px}#ttp-speed-btn{width:38px}#player-close{width:30px}}';
    document.head.appendChild(s);
  }

  // Reuse a legacy node when the page shipped one, otherwise create it.
  function _slot(id, tag, cls) {
    var el = document.getElementById(id);
    if (!el) { el = document.createElement(tag); el.id = id; }
    if (cls) el.className = cls;
    return el;
  }

  function ensure() {
    if (built) return root;
    built = true;
    _injectCSS();
    root = document.getElementById('player');
    var isNew = !root;
    if (isNew) {
      root = document.createElement('div');
      root.id = 'player';
      root.className = 'hidden';
    }
    root.setAttribute('role', 'region');
    root.setAttribute('aria-label', _t3('Lecteur', 'Player', 'נגן'));

    elSeek = _slot('player-seek', 'div');
    elSeek.setAttribute('role', 'slider');
    elSeek.setAttribute('tabindex', '0');
    elSeek.setAttribute('aria-label', _t3('Progression du cours', 'Class progress', 'התקדמות השיעור'));
    elSeek.setAttribute('aria-valuemin', '0');
    elSeek.setAttribute('aria-valuemax', '100');
    elSeek.setAttribute('aria-valuenow', '0');
    elFill = _slot('player-bar-fill', 'div');
    elSeek.appendChild(elFill);

    var body = document.createElement('div');
    body.className = 'ttp-pbody';

    elArt = _slot('player-art', 'img');
    elArt.setAttribute('alt', '');
    // No `src=""` placeholder: an empty src makes the browser re-request the page.
    if (!elArt.getAttribute('src')) elArt.style.visibility = 'hidden';
    elTitle = _slot('player-title', 'div');
    elCh = _slot('player-channel', 'span');
    elTime = _slot('player-time', 'span');
    var sub = document.createElement('div');
    sub.id = 'player-sub';
    sub.appendChild(elCh);
    sub.appendChild(elTime);
    var info = _slot('player-info', 'div');
    info.textContent = '';
    info.appendChild(elTitle);
    info.appendChild(sub);

    var back = document.createElement('button');
    back.type = 'button'; back.id = 'ttp-back-btn'; back.className = 'ttp-btn ttp-skip';
    back.innerHTML = _ttpSkipIcon(true, 15);
    var fwd = document.createElement('button');
    fwd.type = 'button'; fwd.id = 'ttp-fwd-btn'; fwd.className = 'ttp-btn ttp-skip';
    fwd.innerHTML = _ttpSkipIcon(false, 30);
    elPlay = document.createElement('button');
    elPlay.type = 'button'; elPlay.id = 'ttp-play-btn'; elPlay.className = 'ttp-btn';
    elPlay.innerHTML = playIcon();
    elSpeed = document.createElement('button');
    elSpeed.type = 'button'; elSpeed.id = 'ttp-speed-btn'; elSpeed.className = 'ttp-btn';
    elClose = _slot('player-close', 'button', 'ttp-btn');
    elClose.type = 'button';
    elClose.textContent = '✕';

    // aria-label on every control (a title= tooltip never shows on a phone).
    back.setAttribute('aria-label', _t3('Reculer de 15 secondes', 'Back 15 seconds', 'אחורה 15 שניות'));
    fwd.setAttribute('aria-label', _t3('Avancer de 30 secondes', 'Forward 30 seconds', 'קדימה 30 שניות'));
    elSpeed.setAttribute('aria-label', _t3('Vitesse de lecture', 'Playback speed', 'מהירות השמעה'));
    elClose.setAttribute('aria-label', _t3('Fermer le lecteur', 'Close the player', 'סגירת הנגן'));
    back.title = back.getAttribute('aria-label');
    fwd.title = fwd.getAttribute('aria-label');
    elSpeed.title = elSpeed.getAttribute('aria-label');
    elClose.title = elClose.getAttribute('aria-label');

    var ctrls = document.createElement('div');
    ctrls.className = 'ttp-ctrls';
    ctrls.appendChild(back);
    ctrls.appendChild(elPlay);
    ctrls.appendChild(fwd);
    ctrls.appendChild(elSpeed);
    ctrls.appendChild(elClose);

    body.appendChild(elArt);
    body.appendChild(info);
    body.appendChild(ctrls);

    // The bar's own <audio>, unless the page already shipped one (legacy pages
    // hold a live reference to it, so it must be moved and never rebuilt).
    var ownAudio = document.getElementById('player-audio');
    if (!ownAudio) {
      ownAudio = document.createElement('audio');
      ownAudio.id = 'player-audio';
      ownAudio.preload = 'none';
    }
    ownAudio.removeAttribute('controls');

    var legacySpeed = document.getElementById('speed-cycle-btn');

    root.textContent = '';
    root.appendChild(elSeek);
    root.appendChild(body);
    root.appendChild(ownAudio);
    if (legacySpeed) root.appendChild(legacySpeed);   // kept alive, hidden by CSS
    if (isNew) document.body.appendChild(root);

    // ── wiring ───────────────────────────────────────────────────────────────
    elPlay.addEventListener('click', function () {
      if (!cur) return;
      if (cur.paused) { _armResume(); cur.play(); } else cur.pause();
    });
    back.addEventListener('click', function () { skip(-15); });
    fwd.addEventListener('click', function () { skip(30); });
    elSpeed.addEventListener('click', function () {
      var i = SPEEDS.indexOf(speed());
      setSpeed(SPEEDS[(i + 1) % SPEEDS.length]);
    });
    elClose.addEventListener('click', function () {
      if (cur) cur.pause();
      hide();
    });
    var seeking = false;
    function seekAt(clientX) {
      if (!cur || !isFinite(cur.duration) || cur.duration <= 0) return;
      var r = elSeek.getBoundingClientRect();
      var p = (clientX - r.left) / (r.width || 1);
      if (document.documentElement.dir === 'rtl') p = 1 - p;
      try { cur.currentTime = Math.min(cur.duration, Math.max(0, p * cur.duration)); } catch (_) {}
    }
    elSeek.addEventListener('pointerdown', function (e) {
      seeking = true;
      try { elSeek.setPointerCapture(e.pointerId); } catch (_) {}
      seekAt(e.clientX); e.preventDefault();
    });
    elSeek.addEventListener('pointermove', function (e) { if (seeking) seekAt(e.clientX); });
    elSeek.addEventListener('pointerup', function () { seeking = false; });
    elSeek.addEventListener('pointercancel', function () { seeking = false; });
    elSeek.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowRight') { skip(15); e.preventDefault(); }
      else if (e.key === 'ArrowLeft') { skip(-15); e.preventDefault(); }
    });

    // Legacy pages toggle `#player.hidden` themselves — watch the class so the
    // body padding stays correct whoever showed or hid the bar.
    try {
      new MutationObserver(_syncPad).observe(root, { attributes: true, attributeFilter: ['class'] });
    } catch (_) {}
    window.addEventListener('resize', _syncPad);

    bind(ownAudio);
    _renderSpeed();
    _restore();
    return root;
  }

  // Bind the bar to whichever <audio> is actually playing on this page.
  function bind(audio) {
    if (!audio || cur === audio) return;
    cur = audio;
    cur.playbackRate = speed();
    ['play', 'playing', 'pause', 'ended'].forEach(function (ev) {
      cur.addEventListener(ev, _renderPlay);
    });
    cur.addEventListener('loadedmetadata', function () {
      if (pendingSeek > 0) {
        try { cur.currentTime = pendingSeek; } catch (_) {}
        pendingSeek = 0;
      }
      _renderTime();
    });
    cur.addEventListener('durationchange', _renderTime);
    cur.addEventListener('timeupdate', function () {
      restoredPos = 0;
      _renderTime();
      // Per-episode position (read back by getResumePosition/attachResumeBanner).
      // Legacy pages write the same key themselves — same value, harmless.
      if (meta.id && cur.currentTime > 5) {
        try { localStorage.setItem('resume_' + meta.id, Math.floor(cur.currentTime)); } catch (_) {}
      }
      _persistNow();
    });
    cur.addEventListener('ended', function () {
      restoredPos = 0;
      if (meta.id) { try { localStorage.removeItem('resume_' + meta.id); } catch (_) {} }
      _clearNow();
    });
    _bindMediaSession(cur);
    _renderPlay();
    _renderTime();
  }

  function skip(d) {
    if (!cur) return;
    _armResume();
    var max = isFinite(cur.duration) && cur.duration > 0 ? cur.duration : Infinity;
    try { cur.currentTime = Math.min(max, Math.max(0, cur.currentTime + d)); } catch (_) {}
  }

  // A bar restored from `ttp_now_playing` has not loaded its audio yet: the
  // first play/skip must land on the saved position.
  function _armResume() {
    if (restoredPos > 0 && cur && cur.readyState < 1) pendingSeek = restoredPos;
    restoredPos = 0;
  }

  function setSpeed(v) {
    if (SPEEDS.indexOf(v) === -1) v = 1;
    try { localStorage.setItem('playbackSpeed', v); } catch (_) {}
    if (cur) cur.playbackRate = v;
    // Episode pages also carry a `.speed-bar`; keep it in sync so there is only
    // ever one truth for the speed.
    document.querySelectorAll('.speed-btn[data-speed]').forEach(function (b) {
      b.classList.toggle('active', parseFloat(b.dataset.speed) === v);
    });
    document.querySelectorAll('.ep-audio, #ep-audio').forEach(function (a) { a.playbackRate = v; });
    _renderSpeed();
    return v;
  }

  function _renderSpeed() {
    if (!elSpeed) return;
    var v = speed();
    elSpeed.textContent = v + '×';
    elSpeed.classList.toggle('active', v !== 1);
  }

  function _renderPlay() {
    if (!elPlay) return;
    var playing = cur && !cur.paused && !cur.ended;
    elPlay.innerHTML = playing ? pauseIcon() : playIcon();
    elPlay.classList.toggle('is-playing', !!playing);
    var lbl = playing ? _t3('Pause', 'Pause', 'השהיה')
      : (restoredPos > 0
          ? _t3('Reprendre à ', 'Resume at ', 'המשך מ-') + _ttpFmtTime(restoredPos)
          : _t3('Lecture', 'Play', 'נגן'));
    elPlay.setAttribute('aria-label', lbl);
    elPlay.title = lbl;
  }

  function _renderTime() {
    if (!elTime) return;
    if (restoredPos > 0) {
      elTime.textContent = _t3('Reprendre à ', 'Resume at ', 'המשך מ-') + _ttpFmtTime(restoredPos);
      _setFill(0);
      return;
    }
    if (!cur) return;
    var d = isFinite(cur.duration) ? cur.duration : 0;
    elTime.textContent = _ttpFmtTime(cur.currentTime) + (d > 0 ? ' / ' + _ttpFmtTime(d) : '');
    _setFill(d > 0 ? (cur.currentTime / d) : 0);
  }

  function _setFill(p) {
    p = Math.min(1, Math.max(0, p || 0));
    if (elFill) elFill.style.width = (p * 100).toFixed(2) + '%';
    if (elSeek) elSeek.setAttribute('aria-valuenow', String(Math.round(p * 100)));
  }

  // The bar used to sit on top of the last list item (and, on an iPhone, under
  // the gesture bar): reserve its height at the bottom of the page.
  function _syncPad() {
    if (!root) return;
    var on = !root.classList.contains('hidden');
    document.body.style.paddingBottom = on ? (root.offsetHeight + 'px') : '';
  }

  function show() { ensure(); root.classList.remove('hidden'); _syncPad(); }
  function hide() {
    if (!root) return;
    root.classList.add('hidden');
    _syncPad();
    _clearNow();
  }

  // ── "what was playing" memory ────────────────────────────────────────────
  // Multi-page site: navigating ALWAYS stops the sound (no SPA, no shared audio
  // context). What survives is the STATE — the bar comes back paused on the next
  // page with an explicit "Reprendre à mm:ss". It is not continuous playback.
  function _persistNow() {
    if (!cur) return;
    var now = Date.now();
    if (now - lastNowWrite < 4000) return;
    lastNowWrite = now;
    if (cur.currentTime <= 5) return;
    // Pages that drive the bar through their own legacy code never call load(),
    // so fall back to what the bar is actually displaying.
    var src = meta.src || cur.currentSrc || cur.src || '';
    if (!src) return;
    try {
      localStorage.setItem(TTP_NOW_KEY, JSON.stringify({
        id: meta.id || src,
        title: meta.title || (elTitle ? elTitle.textContent : ''),
        ch: meta.channel || (elCh ? elCh.textContent : ''),
        art: meta.art || (elArt ? (elArt.getAttribute('src') || '') : ''),
        src: src, pos: Math.floor(cur.currentTime), t: now
      }));
    } catch (_) {}
  }
  function _clearNow() { try { localStorage.removeItem(TTP_NOW_KEY); } catch (_) {} }

  function _restore() {
    var rec = null;
    try { rec = JSON.parse(localStorage.getItem(TTP_NOW_KEY) || 'null'); } catch (_) {}
    if (!rec || !rec.src || !rec.id) return;
    if (!rec.t || (Date.now() - rec.t) > TTP_NOW_MAX_AGE) { _clearNow(); return; }
    if (cur && (cur.src || cur.currentSrc)) return;   // the page already loaded something
    meta = { id: rec.id, title: rec.title, channel: rec.ch, art: rec.art, src: rec.src };
    restoredPos = rec.pos || 0;
    _paint();
    cur.preload = 'none';         // restoring costs no request until the user hits play
    cur.src = rec.src;
    show();
    _renderPlay();
    _renderTime();
  }

  function _paint() {
    if (elTitle) elTitle.textContent = meta.title || '';
    if (elCh) elCh.textContent = meta.channel || '';
    if (elArt) {
      if (meta.art) { elArt.src = meta.art; elArt.style.visibility = ''; }
      else { elArt.removeAttribute('src'); elArt.style.visibility = 'hidden'; }
    }
  }

  // ── Media Session — follows the element actually in use ───────────────────
  // It used to hard-target #player-audio, so it was dead on episode pages.
  function _bindMediaSession(audio) {
    if (!('mediaSession' in navigator) || !audio || msBound === audio) return;
    msBound = audio;
    var ms = navigator.mediaSession;
    function update() {
      try {
        ms.metadata = new MediaMetadata({
          title: (meta.title || (elTitle && elTitle.textContent) || 'The Torah Podcast'),
          artist: (meta.channel || (elCh && elCh.textContent) || 'The Torah Podcast'),
          album: 'The Torah Podcast',
          artwork: [
            { src: meta.art || (elArt && elArt.getAttribute('src')) || '/favicon.png', sizes: '512x512', type: 'image/png' },
            { src: '/favicon.png', sizes: '1024x1024', type: 'image/png' }
          ]
        });
      } catch (_) {}
    }
    audio.addEventListener('play', function () { update(); try { ms.playbackState = 'playing'; } catch (_) {} });
    audio.addEventListener('playing', function () { update(); try { ms.playbackState = 'playing'; } catch (_) {} });
    audio.addEventListener('loadedmetadata', update);
    audio.addEventListener('pause', function () { try { ms.playbackState = 'paused'; } catch (_) {} });
    function set(action, fn) { try { ms.setActionHandler(action, fn); } catch (_) {} }
    set('play', function () { audio.play(); });
    set('pause', function () { audio.pause(); });
    set('seekbackward', function (d) { skip(-((d && d.seekOffset) || 15)); });
    set('seekforward', function (d) { skip((d && d.seekOffset) || 30); });
    set('seekto', function (d) { if (d && d.seekTime != null) { try { audio.currentTime = d.seekTime; } catch (_) {} } });
    audio.addEventListener('timeupdate', function () {
      if (!('setPositionState' in ms)) return;
      if (!isFinite(audio.duration) || audio.duration <= 0) return;
      try {
        ms.setPositionState({
          duration: audio.duration,
          playbackRate: audio.playbackRate || 1,
          position: Math.min(audio.currentTime, audio.duration)
        });
      } catch (_) {}
    });
  }

  // ── public API ───────────────────────────────────────────────────────────
  // load(): plays `m.src` in the bar's own <audio>.
  //   m = { id, title, channel, art, src, position }
  function load(m) {
    ensure();
    var own = document.getElementById('player-audio');
    bind(own);
    meta = m || {};
    restoredPos = 0;
    _paint();
    if (meta.src && own.src !== meta.src) {
      own.preload = 'metadata';
      own.src = meta.src;
      var saved = (meta.position != null) ? meta.position : getResumePosition(meta.id);
      pendingSeek = saved > 5 ? saved : 0;
    }
    own.playbackRate = speed();
    show();
    _renderTime();
    var p = own.play();
    if (p && p.catch) p.catch(function () { _renderPlay(); });
    _renderPlay();
    return own;
  }

  // attach(): the bar drives an <audio> that already lives in the page
  // (episode pages), so their own listeners keep running untouched.
  function attach(audioEl, m) {
    ensure();
    bind(audioEl);
    meta = m || {};
    restoredPos = 0;
    _paint();
    audioEl.playbackRate = speed();
    show();
    _renderTime();
    _renderPlay();
    return audioEl;
  }

  function toggle(audioEl, m) {
    ensure();
    if (cur === audioEl && !audioEl.paused) { audioEl.pause(); return false; }
    attach(audioEl, m);
    var p = audioEl.play();
    if (p && p.catch) p.catch(function () { _renderPlay(); });
    return true;
  }

  return {
    ensure: ensure, load: load, attach: attach, toggle: toggle,
    show: show, hide: hide, skip: skip,
    setSpeed: setSpeed, speed: speed,
    audio: function () { return cur; },
    el: function () { return root; },
    meta: function () { return meta; },
    isPlaying: function () { return !!(cur && !cur.paused && !cur.ended); }
  };
})();

(function initTTPPlayer() {
  var run = function () { try { window.TTPPlayer.ensure(); } catch (_) {} };
  // Wait for the full document, never just for <body>: several pages load
  // utils.js ABOVE their own legacy `<div id="player">`, and building the bar
  // mid-parse would miss it and leave the page with two bars.
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);
  else run();
})();
