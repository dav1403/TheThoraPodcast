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
    '.mnav-head{display:flex;align-items:center;justify-content:space-between;padding:18px 18px 12px;border-bottom:1px solid rgba(255,255,255,.12)}' +
    '.mnav-title{font-size:1.05rem;font-weight:600}' +
    '.mnav-close{background:none;border:none;color:rgba(255,255,255,.72);font-size:1.5rem;line-height:1;cursor:pointer;padding:4px 8px;font-family:inherit}' +
    '.mnav-close:hover{color:#fff}' +
    '.mnav-list{padding:8px 0 40px}' +
    '.mnav-item{display:block;color:rgba(255,255,255,.82);text-decoration:none;font-size:1rem;padding:13px 20px;border:none;background:none;width:100%;text-align:left;font-family:inherit;cursor:pointer;box-sizing:border-box}' +
    '[dir=rtl] .mnav-item{text-align:right}' +
    '.mnav-item:hover{background:rgba(255,255,255,.06);color:#fff}' +
    '.mnav-item.active{color:#fff;font-weight:600;box-shadow:inset 3px 0 0 #e87722}' +
    '[dir=rtl] .mnav-item.active{box-shadow:inset -3px 0 0 #e87722}' +
    '.mnav-group{border-bottom:1px solid rgba(255,255,255,.05)}' +
    '.mnav-grouphead{display:flex;align-items:center}' +
    '.mnav-grouplink{flex:1}' +
    '.mnav-acc{background:none;border:none;color:rgba(255,255,255,.6);font-size:1rem;padding:13px 20px;cursor:pointer;transition:transform .2s;font-family:inherit}' +
    '.mnav-group.open .mnav-acc{transform:rotate(180deg);color:#fff}' +
    '.mnav-sub{max-height:0;overflow:hidden;transition:max-height .28s ease;background:rgba(0,0,0,.22)}' +
    '.mnav-group.open .mnav-sub{max-height:1400px}' +
    '.mnav-subitem{display:block;color:rgba(255,255,255,.66);text-decoration:none;font-size:.9rem;padding:11px 20px 11px 34px}' +
    '[dir=rtl] .mnav-subitem{padding:11px 34px 11px 20px}' +
    '.mnav-subitem:hover{background:rgba(255,255,255,.06);color:#fff}' +
    // Show the mobile menu, hide the desktop nav, at tablet width and below
    '@media(max-width:768px){' +
      'header{position:relative}' +
      '.header-nav{display:none!important}' +
      '.mnav-toggle{display:flex;flex-direction:column;justify-content:center;gap:5px;position:absolute;top:16px;right:14px;width:42px;height:42px;padding:10px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.2);border-radius:10px;cursor:pointer;z-index:120}' +
      '[dir=rtl] .mnav-toggle{right:auto;left:14px}' +
      '.mnav-toggle span{display:block;height:2px;width:100%;background:#fff;border-radius:2px}' +
      '.mnav-overlay{display:block;position:fixed;inset:0;background:rgba(0,0,0,.5);opacity:0;visibility:hidden;transition:opacity .25s ease;z-index:998}' +
      '.mnav-overlay.open{opacity:1;visibility:visible}' +
      '.mnav-panel{display:flex;flex-direction:column;position:fixed;top:0;right:0;height:100%;width:min(84vw,320px);background:#14142a;color:#fff;transform:translateX(100%);transition:transform .25s ease;z-index:999;box-shadow:-6px 0 24px rgba(0,0,0,.4);overflow-y:auto;-webkit-overflow-scrolling:touch}' +
      '[dir=rtl] .mnav-panel{right:auto;left:0;transform:translateX(-100%);box-shadow:6px 0 24px rgba(0,0,0,.4)}' +
      '.mnav-panel.open{transform:translateX(0)}' +
    '}' +
    'body.mnav-lock{overflow:hidden}';
  document.head.appendChild(s);
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
      if (submenu) {
        Array.prototype.forEach.call(submenu.querySelectorAll('a'), function(sa) {
          var sc = document.createElement('a');
          sc.className = 'mnav-subitem';
          sc.href = sa.getAttribute('href');
          sc.textContent = sa.textContent.trim();
          sub.appendChild(sc);
        });
      }
      group.appendChild(sub);
      acc.addEventListener('click', function() {
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
    return Promise.all(enabled.map(function(ch) {
      return fetch('feeds/' + ch.slug + '.entries.json')
        .then(function(r) { return r.ok ? r.json() : []; }).catch(function() { return []; });
    })).then(function(results) {
      var totalEp = results.reduce(function(s, eps) { return s + eps.length; }, 0);
      var totalH  = Math.round(totalEp * 0.75);
      var lang = window.lang || 'fr';
      el.textContent = lang === 'he'
        ? enabled.length + ' ערוצים · ' + totalEp + ' שיעורים · ~' + totalH + ' שעות'
        : enabled.length + ' rabbins · ' + totalEp + ' cours · ~' + totalH + 'h de Torah';
    });
  }).catch(function() {});
})();
