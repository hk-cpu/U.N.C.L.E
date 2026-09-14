/* Service worker: makes cardiag installable and lets the shell open instantly.

   Only the app shell is cached. Vehicle data is never cached - a stale reading
   from a previous drive shown as if it were live would be worse than no
   reading at all, so /api/ requests always go to the network and fail loudly
   when the server is not running.
*/

const VERSION = "cardiag-v1";
const SHELL = [
  "/",
  "/app.css",
  "/app.js",
  "/manifest.webmanifest",
  "/icon-192.png",
  "/icon-512.png",
  "/icon-maskable-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(VERSION)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key !== VERSION).map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Never serve the car's data from cache.
  if (url.pathname.startsWith("/api/")) return;

  // Shell: serve from cache, then refresh it in the background so a new
  // version of the app is picked up on the next launch.
  event.respondWith(
    caches.match(request, { ignoreSearch: true }).then((cached) => {
      const live = fetch(request)
        .then((response) => {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(VERSION).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => cached);

      return cached || live;
    })
  );
});
