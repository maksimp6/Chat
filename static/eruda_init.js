(function () {
    "use strict";

    function initEruda() {
        if (!window.eruda || typeof window.eruda.init !== "function") return;
        try {
            window.eruda.init();
            window.dispatchEvent(new CustomEvent("alice:eruda-ready"));
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
