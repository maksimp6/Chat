(function () {
    "use strict";

    function cleanupLegacyServiceWorkers() {
        try {
            if (!navigator.serviceWorker || typeof navigator.serviceWorker.getRegistrations !== "function") return;
            navigator.serviceWorker.getRegistrations().then(function (registrations) {
                return Promise.all(registrations.map(function (registration) {
                    return registration.unregister();
                }));
            }).catch(function (error) {
                console.warn("[BOOT] Service worker cleanup failed:", error);
            });
        } catch (error) {
            console.warn("[BOOT] Service worker cleanup unavailable:", error);
        }
    }

    function cleanupLegacyCaches() {
        try {
            if (!window.caches || typeof window.caches.keys !== "function") return;
            window.caches.keys().then(function (keys) {
                return Promise.all(keys.filter(function (key) {
                    return /^alice-pro-/i.test(key);
                }).map(function (key) {
                    return window.caches.delete(key);
                }));
            }).catch(function (error) {
                console.warn("[BOOT] Cache cleanup failed:", error);
            });
        } catch (error) {
            console.warn("[BOOT] Cache cleanup unavailable:", error);
        }
    }

    cleanupLegacyServiceWorkers();
    cleanupLegacyCaches();
})();
