// Cat Gallery — service worker
// Cache strategy:
//   app shell (HTML/CSS/JS/icons/manifest) → cache-first, refreshed on activate
//   /api/*                                 → network-only (always fresh)
//   /uploads/*, /thumbs/*                  → passthrough (HTTP cache headers handle it)

const VERSION = 'cat-gallery-v1';
const SHELL = [
  '/',
  '/static/index.html',
  '/static/style.css',
  '/static/app.js',
  '/static/manifest.json',
  '/static/icon.svg',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/apple-touch-icon.png',
];

self.addEventListener('install', (evt) => {
  evt.waitUntil(
    caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (evt) => {
  evt.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (evt) => {
  const req = evt.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // API & user-uploaded media must never come from the SW cache.
  if (url.pathname.startsWith('/api/')) return;
  if (url.pathname.startsWith('/uploads/')) return;
  if (url.pathname.startsWith('/thumbs/')) return;
  if (url.pathname.startsWith('/trash-files/')) return;
  if (url.pathname.startsWith('/trash-thumbs/')) return;

  // HTML navigation (clicking a link, typing a URL, refreshing) → network-first.
  // Otherwise navigating between /, /disk, /trash kept serving the user a
  // stale cached page even after I shipped a new version. Cache stays as the
  // offline fallback only.
  if (req.mode === 'navigate' || req.destination === 'document') {
    evt.respondWith(
      fetch(req).then((resp) => {
        if (resp.ok) {
          const copy = resp.clone();
          caches.open(VERSION).then((c) => c.put(req, copy)).catch(() => {});
        }
        return resp;
      }).catch(() => caches.match(req))
    );
    return;
  }

  // Static assets (JS / CSS / icons) → cache-first stale-while-revalidate.
  // Eventual consistency is fine here since they're fingerprinted by VERSION
  // and the new SW wipes the old cache on activate.
  evt.respondWith(
    caches.match(req).then((cached) => {
      const fetchPromise = fetch(req).then((resp) => {
        if (resp.ok) {
          const copy = resp.clone();
          caches.open(VERSION).then((c) => c.put(req, copy)).catch(() => {});
        }
        return resp;
      }).catch(() => cached);
      return cached || fetchPromise;
    })
  );
});
