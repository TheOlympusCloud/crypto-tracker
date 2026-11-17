// Network first, cached shell when offline. API data is never cached: it is live and private.
// Every file is rechecked with the server ("no-cache"), so an update shows on the next load.
const SHELL = [
  "./", "style.css", "app.js", "kinds.js", "fold.js", "graph2d.js", "graph3d.js", "icon.svg", "manifest.webmanifest",
  "vendor/cytoscape.min.js", "vendor/three.module.js", "vendor/three.core.js", "vendor/OrbitControls.js",
  "vendor/fonts/bricolage-grotesque-var.woff2", "vendor/fonts/BricolageGrotesque-OFL.txt",
  "vendor/fonts/red-hat-text-400.woff2", "vendor/fonts/red-hat-text-500.woff2", "vendor/fonts/red-hat-text-700.woff2",
  "vendor/fonts/jetbrains-mono-400.woff2", "vendor/fonts/RedHat-OFL.txt", "vendor/fonts/JetBrainsMono-OFL.txt",
];
const CACHE = "shell-4"; // renamed when files leave the shell, so their old copies are dropped
// A new worker takes over at once: an installed app is rarely closed long enough to swap it.
self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL.map((u) => new Request(u, { cache: "no-cache" })))).then(() => self.skipWaiting())));
self.addEventListener("activate", (e) => e.waitUntil(caches.keys().then((names) => Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)))).then(() => self.clients.claim())));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.includes("/api/")) return;
  e.respondWith(
    fetch(e.request, { cache: "no-cache" })
      .then((r) => {
        // Only a good answer is kept for offline use: never a password prompt or an error.
        if (r.ok) {
          const copy = r.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return r;
      })
      .catch(() => caches.match(e.request))
  );
});
