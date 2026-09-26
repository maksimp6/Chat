(function () {
    "use strict";

    var attempts = 0;
    var maxAttempts = 5;

    function showIfRequested() {
        try {
            var params = new URLSearchParams(window.location.search);
            if (params.get("eruda") === "1" && window.eruda && typeof window.eruda.show === "function") {
                window.eruda.show();
            }
        } catch (error) {
            console.warn("[ERUDA] Debug show failed:", error);
        }
    }

    function loadEruda() {
        if (window.__ALICE_ERUDA_INITIALIZED__) return;
        if (window.eruda && typeof window.eruda.init === "function") {
            try {
                window.eruda.init();
                window.__ALICE_ERUDA_INITIALIZED__ = true;
                window.dispatchEvent(new CustomEvent("alice:eruda-ready"));
                showIfRequested();
            } catch (error) {
                console.error("Failed to initialize local Eruda:", error);
            }
            return;
        }

        var existing = document.getElementById("alice-eruda-loader");
        if (existing) return;

        var script = document.createElement("script");
        script.id = "alice-eruda-loader";
        script.async = true;
        script.src = (window.__ALICE_STATIC_BASE || "/static") + "/eruda.js?v=" + encodeURIComponent(window.__ALICE_STATIC_VERSION || "1");
        script.onload = function () {
            loadEruda();
        };
        script.onerror = function () {
            attempts += 1;
            if (attempts < maxAttempts) window.AliceCoreAPI.scheduler.defer(loadEruda, attempts * 250);
            else console.warn("[ERUDA] Local Eruda bundle failed to load");
        };
        document.head.appendChild(script);
    }

    function start() {
        // Let the application finish its critical DOM startup first.
        window.AliceCoreAPI.scheduler.defer(loadEruda, 0);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start, { once: true });
    } else {
        start();
    }
})();
