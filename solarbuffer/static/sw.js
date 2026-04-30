const CACHE = 'solarbuffer-v1';

const PRECACHE = [
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/apple-touch-icon.png',
  '/static/manifest.webmanifest',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(PRECACHE))
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  // Altijd netwerk voor POST/mutaties
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);

  // Altijd netwerk voor live data en acties
  const networkOnly = [
    '/p1_status', '/device_status', '/scan_devices',
    '/toggle_shelly', '/set_brightness', '/boost',
    '/set_theme', '/set_schedule', '/config/import', '/config/export',
    '/audit_log',
  ];
  if (networkOnly.some(p => url.pathname.startsWith(p))) return;

  // Statische bestanden: cache first, update op achtergrond
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.open(CACHE).then(cache =>
        cache.match(event.request).then(cached => {
          const fromNetwork = fetch(event.request).then(response => {
            cache.put(event.request, response.clone());
            return response;
          });
          return cached || fromNetwork;
        })
      )
    );
    return;
  }

  // HTML pagina's: netwerk first, cache als fallback
  event.respondWith(
    fetch(event.request)
      .then(response => {
        const clone = response.clone();
        caches.open(CACHE).then(cache => cache.put(event.request, clone));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
