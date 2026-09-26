const CACHE_NAME = "alice-pro-v2";
const urlsToCache = [
  "/",
  "/static/style.css",
  "/static/core.js",
  "/static/sidebar.js",
  "/static/models.js",
  "/static/chat.js",
  "/static/voice.js",
  "/static/prompts.js",
  "/static/search.js",
  "/static/notifications.js",
  "/static/i18n.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(urlsToCache)));
});

self.addEventListener("fetch", (event) => {
  event.respondWith(
    caches.match(event.request).then((response) => response || fetch(event.request)),
  );
});

self.addEventListener("push", (event) => {
  const data = event.data ? event.data.json() : {};
  event.waitUntil(
    self.registration.showNotification(data.title || "Alice Pro", {
      body: data.body || "Новое уведомление",
      icon: "/static/icon-192.png",
    }),
  );
});
