// Cat Gallery — service worker
//
// Cache strategy:
//   App shell (HTML / CSS / JS / manifest / icons) → pre-cached on install,
//                                                    cache-first w/ background revalidate
//   Navigations (/, /disk, /trash)                 → network-first, fall back to cached
//                                                    index.html when offline
//   /api/*                                         → network-first, no cache fallback
//   /static/*                                      → stale-while-revalidate
//   /thumbs/*                                      → cache-first with LRU size cap
//   /uploads/*, /trash-files/*, /trash-thumbs/*    → passthrough (HTTP cache handles it,
//                                                    originals are big & range-served)
//
// To force clients to drop old caches, bump CACHE_VERSION below.

const CACHE_VERSION = 'v4';
const SHELL_CACHE   = `cat-shell-${CACHE_VERSION}`;
const STATIC_CACHE  = `cat-static-${CACHE_VERSION}`;
const THUMBS_CACHE  = `cat-thumbs-${CACHE_VERSION}`;
const KNOWN_CACHES  = new Set([SHELL_CACHE, STATIC_CACHE, THUMBS_CACHE]);

// Cap the thumbnail cache so a long-running install doesn't grow without bound.
const THUMBS_MAX_ENTRIES = 300;

const APP_SHELL = [
  '/',
  '/static/index.html',
  '/static/style.css',
  '/static/app.js',
  '/static/manifest.json',
  '/static/icon.svg',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/icon-512-maskable.png',
  '/static/apple-touch-icon.png',
];

// ---------- install ----------
self.addEventListener('install', (evt) => {
  evt.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    // addAll is atomic — if any item fails the whole install fails. Use individual
    // puts so a single missing optional asset doesn't break the whole SW.
    await Promise.all(APP_SHELL.map(async (url) => {
      try {
        const resp = await fetch(url, { cache: 'reload' });
        if (resp.ok) await cache.put(url, resp);
      } catch (_) { /* ignore: best-effort precache */ }
    }));
    await self.skipWaiting();
  })());
});

// ---------- activate: cleanup ----------
self.addEventListener('activate', (evt) => {
  evt.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(
      keys.filter((k) => !KNOWN_CACHES.has(k)).map((k) => caches.delete(k))
    );
    await self.clients.claim();
  })());
});

// ---------- fetch ----------
self.addEventListener('fetch', (evt) => {
  const req = evt.request;

  // Never touch non-GET (uploads, deletes, meta patches, etc.).
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const path = url.pathname;

  // Originals & trash media — let the browser/HTTP cache deal with them.
  // These are large and frequently range-requested (video scrubbing).
  if (path.startsWith('/uploads/'))      return;
  if (path.startsWith('/trash-files/'))  return;
  if (path.startsWith('/trash-thumbs/')) return;

  // API: network-first, never serve stale.
  if (path.startsWith('/api/')) {
    evt.respondWith(networkOnly(req));
    return;
  }

  // Thumbnails: cache-first w/ size cap. Cheap to keep, expensive to refetch.
  if (path.startsWith('/thumbs/')) {
    evt.respondWith(cacheFirstCapped(req, THUMBS_CACHE, THUMBS_MAX_ENTRIES));
    return;
  }

  // HTML navigations (clicking links, refreshing, typing URLs): network-first.
  // Falls back to cached index.html when offline so the app still boots.
  if (req.mode === 'navigate' || req.destination === 'document') {
    evt.respondWith(navigationHandler(req));
    return;
  }

  // /static/* and other same-origin GETs: stale-while-revalidate.
  if (path.startsWith('/static/')) {
    evt.respondWith(staleWhileRevalidate(req, STATIC_CACHE));
    return;
  }

  // Anything else same-origin: try cache then network.
  evt.respondWith(staleWhileRevalidate(req, STATIC_CACHE));
});

// ---------- strategies ----------

async function networkOnly(req) {
  try {
    return await fetch(req);
  } catch (err) {
    return new Response(
      JSON.stringify({ error: 'offline' }),
      { status: 503, headers: { 'Content-Type': 'application/json' } }
    );
  }
}

async function navigationHandler(req) {
  try {
    const resp = await fetch(req);
    if (resp.ok) {
      const copy = resp.clone();
      caches.open(SHELL_CACHE).then((c) => c.put(req, copy)).catch(() => {});
    }
    return resp;
  } catch (_) {
    const cached = await caches.match(req);
    if (cached) return cached;
    // Final fallback: the pre-cached index shell.
    const shell = await caches.match('/static/index.html')
               || await caches.match('/');
    if (shell) return shell;
    return new Response('Offline', { status: 503, statusText: 'Offline' });
  }
}

async function staleWhileRevalidate(req, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(req);
  const fetchPromise = fetch(req).then((resp) => {
    if (resp && resp.ok) cache.put(req, resp.clone()).catch(() => {});
    return resp;
  }).catch(() => cached);
  return cached || fetchPromise;
}

async function cacheFirstCapped(req, cacheName, maxEntries) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(req);
  if (cached) return cached;
  try {
    const resp = await fetch(req);
    if (resp && resp.ok) {
      await cache.put(req, resp.clone());
      // Fire-and-forget trim.
      trimCache(cacheName, maxEntries);
    }
    return resp;
  } catch (err) {
    return cached || new Response('', { status: 504, statusText: 'Gateway Timeout' });
  }
}

async function trimCache(cacheName, maxEntries) {
  try {
    const cache = await caches.open(cacheName);
    const keys = await cache.keys();
    const overflow = keys.length - maxEntries;
    if (overflow <= 0) return;
    // FIFO eviction — Cache Storage doesn't expose access time.
    for (let i = 0; i < overflow; i++) {
      await cache.delete(keys[i]);
    }
  } catch (_) { /* ignore */ }
}

// Allow the page to nudge the SW to activate immediately after an update.
self.addEventListener('message', (evt) => {
  if (evt.data === 'SKIP_WAITING' || (evt.data && evt.data.type === 'SKIP_WAITING')) {
    self.skipWaiting();
  }
});
