const CACHE = "betiq-v3"; // bump when shell files change: old caches are dropped on activate
const SHELL = ["/manifest.json", "/logo.svg", "/favicon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  // Network-first for API calls, cache-first for static assets
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api/")) {
    // 503 (not 200) so callers checking `res.ok` treat it as a failure
    // instead of trying to render {"error": "offline"} as data.
    e.respondWith(
      fetch(e.request).catch(() => new Response(JSON.stringify({ error: "offline" }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      }))
    );
  } else if (e.request.destination === "document") {
    // Always network-first for HTML — prevents stale chunk 404s after deploys
    e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
  } else {
    e.respondWith(
      caches.match(e.request).then((cached) => cached || fetch(e.request))
    );
  }
});

self.addEventListener("push", (e) => {
  let data = { title: "BetIQ", body: "New value bets available!", icon: "/icon-192.png", url: "/" };
  try {
    if (e.data) data = { ...data, ...JSON.parse(e.data.text()) };
  } catch {}
  e.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: data.icon,
      badge: "/badge-96.png", // one-colour: Android draws only its shape
      tag: "betiq-value",
      renotify: true,
      data: { url: data.url },
    })
  );
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = e.notification.data?.url || "/";
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      const existing = clients.find((c) => c.url.includes(url) && "focus" in c);
      if (existing) return existing.focus();
      return self.clients.openWindow(url);
    })
  );
});
