(function () {
    "use strict";
    var key = "alice-eruda-enabled";
    var params = new URLSearchParams(window.location.search);
    if (params.get("debug") === "1" || window.location.hash === "#debug") {
        try { localStorage.setItem(key, "true"); } catch (_) {}
    } else if (params.get("debug") === "0") {
        try { localStorage.removeItem(key); } catch (_) {}
    }
    var enabled = false;
    try { enabled = localStorage.getItem(key) === "true"; } catch (_) {}
    if (!enabled) return;

    function start() {
        if (!window.eruda || typeof window.eruda.init !== "function") return;
        try {
            window.eruda.init({ useShadowDom: true, autoScale: true });
            if (typeof window.eruda.show === "function") window.eruda.show();
            window.dispatchEvent(new CustomEvent("alice-eruda-ready"));
        } catch (error) {
            if (window.console && console.error) console.error("Eruda init failed", error);
        }
    }
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start, { once: true });
    } else {
        start();
    }
})();