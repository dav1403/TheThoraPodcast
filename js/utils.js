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
