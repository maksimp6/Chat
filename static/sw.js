// Retired cache-first worker: existing registrations must stop serving old HTML.
self.addEventListener("install", function (event) {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (key) { return /^logos-pro-/i.test(key); })
        .map(function (key) { return caches.delete(key); }));
    }).then(function () { return self.clients.claim(); })
      .then(function () { return self.registration.unregister(); })
  );
});

self.addEventListener("fetch", function (event) {
  // Never cache API responses, navigation or the UI dependency graph.
  event.respondWith(fetch(event.request, { cache: "no-store" }));
});

self.addEventListener("push", (event) => {
  const data = event.data ? event.data.json() : {};
  event.waitUntil(
    self.registration.showNotification(data.title || "Logos", {
      body: data.body || "Новое уведомление",
      icon: "/static/icon-192.png",
    }),
  );
});
