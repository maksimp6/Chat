(function () {
    function bridge() {
        return window.AliceAndroid;
    }

    function nativeLog(level, tag, message) {
        try {
            if (bridge() && typeof bridge().log === "function") {
                bridge().log(level, tag, String(message).slice(0, 8000));
            }
        } catch (_) {
            // Diagnostics must never break the application.
        }
    }

    function installDiagnosticsButton() {
        if (!bridge()) return;
        var header = document.getElementById("header");
        if (!header || document.getElementById("android-diagnostics-btn")) return;

        var button = document.createElement("button");
        button.id = "android-diagnostics-btn";
        button.className = "header-btn";
        button.title = "Диагностика Android";
        button.textContent = "🩺";
        button.onclick = function () {
            try {
                bridge().openDiagnostics();
            } catch (error) {
                nativeLog("ERROR", "Diagnostics", error);
            }
        };
        header.appendChild(button);
    }

    window.addEventListener("error", function (event) {
        var target = event && event.target;
        var resource = target && (target.src || target.href);
        nativeLog(
            "ERROR",
            "WebView",
            "Unhandled web error: " + (event.message || "unknown") +
                (resource ? " [" + resource + "]" : "")
        );
    }, true);

    window.addEventListener("unhandledrejection", function (event) {
        var reason = event && event.reason;
        nativeLog(
            "ERROR",
            "WebView",
            "Unhandled promise rejection: " +
                (reason && reason.stack ? reason.stack : String(reason || "unknown"))
        );
    });

    document.addEventListener("DOMContentLoaded", installDiagnosticsButton);
    setTimeout(installDiagnosticsButton, 500);
})();
