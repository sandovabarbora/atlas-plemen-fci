// Service worker for Atlas plemen FCI.
// Strategy (per project decision):
//   - app shell (HTML + manifest + icons): cache-first for instant offline launch
//   - breed photos (FCI illustrations, Wikipedia images): network-first with a
//     cache fallback, so fresh photos win online but cached ones work offline
//   - Wikipedia summary JSON: network-first, cached so a breed seen once works offline
const VERSION = 'atlas-v3';
const SHELL_CACHE = `${VERSION}-shell`;
const PHOTO_CACHE = `${VERSION}-photos`;

const SHELL_ASSETS = [
  './',
  './index.html',
  './manifest.webmanifest',
  './icons/icon-192.png',
  './icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

// Cache-first: same-origin shell. Network-first (cache fallback): cross-origin
// IMAGES only. Everything else (notably the Wikipedia summary JSON API) is left
// untouched so the SW never turns an API hiccup into a failure.
self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);

  if (url.origin === self.location.origin) {
    // The HTML document: NETWORK-FIRST so new deploys propagate immediately;
    // fall back to cache when offline. (Cache-first here made deploys stick.)
    const isDocument = request.mode === 'navigate'
      || url.pathname.endsWith('/')
      || url.pathname.endsWith('/index.html');
    if (isDocument) {
      event.respondWith(
        fetch(request).then((resp) => {
          const copy = resp.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy)).catch(() => {});
          return resp;
        }).catch(() => caches.match(request).then((c) => c || caches.match('./index.html')))
      );
      return;
    }
    // Other static assets (icons, manifest, sw): cache-first.
    event.respondWith(
      caches.match(request).then((cached) => cached || fetch(request).then((resp) => {
        const copy = resp.clone();
        caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy)).catch(() => {});
        return resp;
      }))
    );
    return;
  }

  // Cross-origin images (FCI illustrations, Wikimedia photos): network-first,
  // fall back to cache for offline. Non-image cross-origin requests pass through.
  if (request.destination === 'image') {
    event.respondWith(
      fetch(request).then((resp) => {
        const copy = resp.clone();
        caches.open(PHOTO_CACHE).then((cache) => cache.put(request, copy)).catch(() => {});
        return resp;
      }).catch(() => caches.match(request))
    );
  }
});
