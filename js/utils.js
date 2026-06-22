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
  var locale = (window.lang === 'he') ? 'he-IL' : 'fr-FR';
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
    '.carousel-section .carousel-label{font-size:.78rem;text-transform:uppercase;letter-spacing:.08em;color:#999;font-weight:600;margin-bottom:10px}' +
    '.carousel-row{display:flex;gap:12px;overflow-x:auto;padding-bottom:6px;scrollbar-width:thin;scroll-snap-type:x proximity}' +
    '.carousel-row::-webkit-scrollbar{height:4px}' +
    '.carousel-row::-webkit-scrollbar-thumb{background:#ddd;border-radius:2px}' +
    '.carousel-card{flex-shrink:0;width:150px;background:#fff;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.08);overflow:hidden;transition:transform .15s,box-shadow .15s;scroll-snap-align:start}' +
    '.carousel-card:hover{transform:translateY(-2px);box-shadow:0 4px 12px rgba(0,0,0,.12)}' +
    '.carousel-card>a{display:block}' +
    '.carousel-card img{width:100%;height:84px;object-fit:cover;display:block}' +
    '.carousel-card-body{padding:8px 9px 10px}' +
    '.carousel-card-ch{font-size:.66rem;color:#e87722;font-weight:600;text-transform:uppercase;letter-spacing:.03em;margin-bottom:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.carousel-card-title{font-size:.8rem;line-height:1.25;color:#1a1a2e;text-decoration:none;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}' +
    '.carousel-card-date{font-size:.68rem;color:#aaa;margin-top:5px}' +
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
    var href = isChannel ? (escapeHtml(ch.slug) + '.html') : epUrl(ep, ch.slug);
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

// Auto-load header stats if #header-stats exists and isn't already populated
(function loadHeaderStats() {
  var el = document.getElementById('header-stats');
  if (!el || el.textContent.trim()) return;
  fetch('channels.json').then(function(r) { return r.ok ? r.json() : []; }).then(function(channels) {
    var enabled = channels.filter(function(c) { return c.enabled; });
    return Promise.all(enabled.map(function(ch) {
      return fetch('feeds/' + ch.slug + '.entries.json')
        .then(function(r) { return r.ok ? r.json() : []; }).catch(function() { return []; });
    })).then(function(results) {
      var totalEp = results.reduce(function(s, eps) { return s + eps.length; }, 0);
      var totalH  = Math.round(totalEp * 0.75);
      var lang = window.lang || 'fr';
      el.textContent = lang === 'he'
        ? enabled.length + ' ערוצים · ' + totalEp + ' שיעורים · ~' + totalH + 'ש'
        : enabled.length + ' rabbins · ' + totalEp + ' cours · ~' + totalH + 'h de Torah';
    });
  }).catch(function() {});
})();
