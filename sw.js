const CACHE = 'ttp-v2';
const PRECACHE = [
  '/', '/index.html', '/links.html', '/parasha.html', '/themes.html',
  '/derniers-cours.html', '/daf-hayomi.html', '/channels.json'
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = e.request.url;
  if (e.request.method !== 'GET') return;
  if (url.includes('.mp3') || url.includes('googletagmanager') || url.includes('gtag')) return;

  // Cache-first only for artwork images (immutable once generated)
  if (url.includes('/artwork/') && (url.endsWith('.png') || url.endsWith('.jpg'))) {
    e.respondWith(
      caches.match(e.request).then(cached => {
        if (cached) return cached;
        return fetch(e.request).then(r => {
          if (r.ok) { const clone = r.clone(); caches.open(CACHE).then(c => c.put(e.request, clone)); }
          return r;
        });
      })
    );
    return;
  }

  // Network-first for everything else (HTML, feeds, channels.json, etc.)
  e.respondWith(
    fetch(e.request)
      .then(r => {
        if (r.ok) { const clone = r.clone(); caches.open(CACHE).then(c => c.put(e.request, clone)); }
        return r;
      })
      .catch(() => caches.match(e.request))
  );
});
