(function () {
    "use strict";

    var attempts = 0;
    var maxAttempts = 50;

    function initEruda() {
        if (!window.eruda || typeof window.eruda.init !== "function") {
            attempts += 1;
            if (attempts < maxAttempts) setTimeout(initEruda, 100);
            else console.warn("[ERUDA] Local Eruda did not become available");
            return;
        }
        if (window.__ALICE_ERUDA_INITIALIZED__) return;
        try {
            window.eruda.init();
            window.__ALICE_ERUDA_INITIALIZED__ = true;
            window.dispatchEvent(new CustomEvent("alice:eruda-ready"));
            var params = new URLSearchParams(window.location.search);
            if (params.get("eruda") === "1" && typeof window.eruda.show === "function") {
                window.eruda.show();
            }
        } catch (error) {
            console.error("Failed to initialize local Eruda:", error);
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initEruda, { once: true });
    } else {
        initEruda();
    }
})();
