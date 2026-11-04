/* Family Circle service worker.
   Caches only the app shell (CSS, icons). It never caches pages or /api/ calls,
   so no location or personal data is ever stored on the device by this file. */
const CACHE = "fc-shell-v1";
const SHELL = ["/static/style.css", "/static/dashboard.css", "/static/pwa.js", "/static/icon-192.png", "/offline"];
self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) return;                       // live data: network only
  if (req.mode === "navigate") {                                       // pages: network, friendly offline page
    e.respondWith(fetch(req).catch(() => caches.match("/offline")));
    return;
  }
  if (url.pathname.startsWith("/static/")) {                           // shell files: fast, refresh in background
    e.respondWith(caches.match(req).then(hit => {
      const net = fetch(req).then(r => { if (r.ok) caches.open(CACHE).then(c => c.put(req, r.clone())); return r; }).catch(() => hit);
      return hit || net;
    }));
  }
});
