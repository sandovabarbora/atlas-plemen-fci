// Service worker for Atlas plemen FCI.
// Strategy (per project decision):
//   - app shell (HTML + manifest + icons): cache-first for instant offline launch
//   - breed photos (FCI illustrations, Wikipedia images): network-first with a
//     cache fallback, so fresh photos win online but cached ones work offline
//   - Wikipedia summary JSON: network-first, cached so a breed seen once works offline
const VERSION = 'atlas-v1';
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

function isPhoto(url) {
  return /\.(jpg|jpeg|png|webp|gif)$/i.test(url.pathname)
    || url.hostname.endsWith('fci.be')
    || url.hostname.endsWith('wikimedia.org')
    || url.hostname.endsWith('wikipedia.org');
}

// Cache-first: shell. Network-first with cache fallback: photos and wiki data.
self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);

  // Same-origin shell assets: cache-first.
  if (url.origin === self.location.origin) {
    event.respondWith(
      caches.match(request).then((cached) => cached || fetch(request).then((resp) => {
        const copy = resp.clone();
        caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy)).catch(() => {});
        return resp;
      }).catch(() => caches.match('./index.html')))
    );
    return;
  }

  // Cross-origin photos / wiki: network-first, fall back to cache.
  if (isPhoto(url)) {
    event.respondWith(
      fetch(request).then((resp) => {
        const copy = resp.clone();
        caches.open(PHOTO_CACHE).then((cache) => cache.put(request, copy)).catch(() => {});
        return resp;
      }).catch(() => caches.match(request))
    );
  }
});
