// Network first, cached shell when offline. API data is never cached: it is live and private.
const SHELL = [
  "./", "style.css", "app.js", "kinds.js", "graph2d.js", "graph3d.js", "icon.svg", "manifest.webmanifest",
  "vendor/cytoscape.min.js", "vendor/three.module.js", "vendor/three.core.js", "vendor/OrbitControls.js",
  "vendor/fonts/cinzel-600.woff2", "vendor/fonts/jost-400.woff2", "vendor/fonts/jost-500.woff2", "vendor/fonts/jost-600.woff2",
  "vendor/fonts/jetbrains-mono-400.woff2", "vendor/fonts/Cinzel-OFL.txt", "vendor/fonts/Jost-OFL.txt", "vendor/fonts/JetBrainsMono-OFL.txt",
];
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
