// Cache only an offline launcher notice, never study data, API responses or credentials.
const OFFLINE_CACHE = 'framework-distiller-offline-v1';
const OFFLINE_FILES = ['/offline.html', '/style.css', '/icon-192.png'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(OFFLINE_CACHE).then(cache => cache.addAll(OFFLINE_FILES)));
  self.skipWaiting();
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key.startsWith('framework-distiller-offline-') && key !== OFFLINE_CACHE)
      .map(key => caches.delete(key))
  )));
  self.clients.claim();
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.mode !== 'navigate' || url.origin !== self.location.origin || url.pathname !== '/') return;
  event.respondWith(fetch(event.request).catch(() => caches.match('/offline.html')));
});
