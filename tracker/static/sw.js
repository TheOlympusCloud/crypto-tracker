// Network first, cached shell when offline. API data is never cached: it is live and private.
const SHELL = ["./", "style.css", "app.js", "cytoscape.min.js", "icon.svg", "manifest.webmanifest"];
self.addEventListener("install", (e) => e.waitUntil(caches.open("shell").then((c) => c.addAll(SHELL))));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.includes("/api/")) return;
  e.respondWith(
    fetch(e.request)
      .then((r) => {
        const copy = r.clone();
        caches.open("shell").then((c) => c.put(e.request, copy));
        return r;
      })
      .catch(() => caches.match(e.request))
  );
});
