// Network first, cached shell when offline. API data is never cached: it is live and private.
const SHELL = [
  "./", "style.css", "app.js", "kinds.js", "graph2d.js", "graph3d.js", "icon.svg", "manifest.webmanifest",
  "vendor/cytoscape.min.js", "vendor/three.module.js", "vendor/three.core.js", "vendor/OrbitControls.js",
  "vendor/fonts/red-hat-display-700.woff2", "vendor/fonts/red-hat-display-900.woff2",
  "vendor/fonts/red-hat-text-400.woff2", "vendor/fonts/red-hat-text-500.woff2", "vendor/fonts/red-hat-text-700.woff2",
  "vendor/fonts/jetbrains-mono-400.woff2", "vendor/fonts/RedHat-OFL.txt", "vendor/fonts/JetBrainsMono-OFL.txt",
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
