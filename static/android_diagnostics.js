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
    var actionRow = document.getElementById("header-actions-2") || header;
    if (!header || document.getElementById("android-diagnostics-btn")) return;

    var button = document.createElement("button");
    button.id = "android-diagnostics-btn";
    button.className = "header-btn alice-btn";
    button.type = "button";
    button.title = "Диагностика Android";
    button.textContent = "🩺";
    button.addEventListener("click", function () {
      try {
        bridge().openDiagnostics();
      } catch (error) {
        nativeLog("ERROR", "Diagnostics", error);
      }
    });
    actionRow.appendChild(button);

    if (typeof bridge().openBrowserTakeover === "function" && !document.getElementById("android-browser-btn")) {
      var browserButton = document.createElement("button");
      browserButton.id = "android-browser-btn";
      browserButton.className = "header-btn alice-btn";
      browserButton.type = "button";
      browserButton.title = "Облачный браузер";
      browserButton.setAttribute("aria-label", "Открыть облачный браузер");
      browserButton.textContent = "🌐";
      browserButton.addEventListener("click", function () {
        try {
          bridge().openBrowserTakeover();
        } catch (error) {
          nativeLog("ERROR", "BrowserTakeover", error);
        }
      });
      actionRow.appendChild(browserButton);
    }
  }

  window.addEventListener(
    "error",
    function (event) {
      var target = event && event.target;
      var resource = target && (target.src || target.href);
      nativeLog(
        "ERROR",
        "WebView",
        "Unhandled web error: " +
          (event.message || "unknown") +
          (resource ? " [" + resource + "]" : ""),
      );
    },
    true,
  );

  window.addEventListener("unhandledrejection", function (event) {
    var reason = event && event.reason;
    nativeLog(
      "ERROR",
      "WebView",
      "Unhandled promise rejection: " +
        (reason && reason.stack ? reason.stack : String(reason || "unknown")),
    );
  });

  function initDiagnostics() {
    if (document.documentElement.dataset.androidDiagnosticsInitialized === "true") return;
    document.documentElement.dataset.androidDiagnosticsInitialized = "true";
    installDiagnosticsButton();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initDiagnostics, { once: true });
  } else {
    initDiagnostics();
  }
  window.AliceCoreAPI.scheduler.defer(initDiagnostics, 500);
})();
