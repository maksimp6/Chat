(function () {
  "use strict";

  var currentScript = document.currentScript;
  var config = currentScript && currentScript.dataset ? currentScript.dataset : {};

  window.__LOGOS_USER_ID = window.__LOGOS_USER_ID || "";
  window.__LOGOS_BASE_PATH = config.basePath || "";
  window.__LOGOS_STATIC_BASE = config.staticBase || "/static";
  window.__LOGOS_STATIC_VERSION = config.staticVersion || "";
  window.__LOGOS_APP_REVISION = config.appRevision || "unknown";

  var basePath = window.__LOGOS_BASE_PATH || "";
  var failedCriticalScripts = [];
  var criticalScriptNames = {
    "core_api.js": "core-api",
    "ui_runtime.js": "ui-runtime",
    "dispatcher.js": "dispatcher",
    "core.js": "core",
  };

  function criticalIdForError(event) {
    var target = event && event.target;
    if (target && target.dataset && typeof target.dataset.criticalScript === "string") {
      return target.dataset.criticalScript;
    }
    var filename = event && typeof event.filename === "string" ? event.filename : "";
    var basename = filename.split("?")[0].split("/").pop();
    return criticalScriptNames[basename] || "";
  }

  function degradedStatus() {
    return typeof document.getElementById === "function"
      ? document.getElementById("frontend-degraded-status")
      : null;
  }

  function showDegradedStatus() {
    var status = degradedStatus();
    if (status) status.hidden = false;
    var root =
      typeof document.getElementById === "function" ? document.getElementById("app-root") : null;
    if (root) root.setAttribute("data-enhancement-state", "degraded");
  }

  function markShellReady() {
    if (failedCriticalScripts.length) {
      showDegradedStatus();
      return false;
    }
    var status = degradedStatus();
    if (status) status.hidden = true;
    var root =
      typeof document.getElementById === "function" ? document.getElementById("app-root") : null;
    if (root) root.setAttribute("data-enhancement-state", "ready");
    return true;
  }

  window.LogosBoot = Object.freeze({
    markShellReady: markShellReady,
    failedCriticalScripts: function () {
      return failedCriticalScripts.slice();
    },
  });

  function emitDiagnostic(level, event, error, extra) {
    var details = Object.assign(
      {
        component: "boot",
        event: event,
        message: error && error.message ? error.message : String(error || ""),
        timestamp: new Date().toISOString(),
      },
      extra || {},
    );
    var logger = console[level] || console.error;
    logger.call(console, "[BOOT]", details);
    window.dispatchEvent(new CustomEvent("logos:diagnostic", { detail: details }));
  }

  console.info("[BOOT] hello: Logos frontend boot loaded");
  window.addEventListener(
    "error",
    function (event) {
      var criticalId = criticalIdForError(event);
      if (criticalId && failedCriticalScripts.indexOf(criticalId) === -1) {
        failedCriticalScripts.push(criticalId);
        showDegradedStatus();
        // A load failure has no error object; a runtime error keeps its own message here.
        emitDiagnostic(
          "error",
          "critical_script_failed",
          event.error || event.message || "Critical script unavailable",
          {
            script: criticalId,
            filename: event.filename || "",
            line: event.lineno || 0,
            column: event.colno || 0,
          },
        );
        return;
      }
      emitDiagnostic("error", "uncaught_error", event.error || event.message, {
        filename: event.filename || "",
        line: event.lineno || 0,
        column: event.colno || 0,
      });
    },
    true,
  );
  window.addEventListener("unhandledrejection", function (event) {
    emitDiagnostic("error", "unhandled_rejection", event.reason);
  });

  function prefixUrl(input) {
    if (!basePath || typeof input !== "string") return input;
    if (!input || input.charAt(0) !== "/" || input.indexOf("//") === 0) return input;
    if (input === basePath || input.indexOf(basePath + "/") === 0) return input;
    return basePath + input;
  }

  var originalFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var target = input;
    if (typeof input === "string") target = prefixUrl(input);
    else if (input instanceof URL) target = prefixUrl(input.toString());
    else if (typeof Request !== "undefined" && input instanceof Request) {
      var url = prefixUrl(input.url);
      if (url !== input.url) target = new Request(url, input);
    }

    init = init || {};
    var headers = new Headers(
      init.headers ||
        (typeof Request !== "undefined" && target instanceof Request ? target.headers : {}),
    );
    if (window.__LOGOS_USER_ID) headers.set("X-Logos-User-ID", window.__LOGOS_USER_ID);
    init.headers = headers;
    return originalFetch(target, init);
  };

  var NativeEventSource = window.EventSource;
  if (NativeEventSource) {
    function patchedEventSource(url, config) {
      return new NativeEventSource(prefixUrl(url), config);
    }
    patchedEventSource.prototype = NativeEventSource.prototype;
    patchedEventSource.CONNECTING = NativeEventSource.CONNECTING;
    patchedEventSource.OPEN = NativeEventSource.OPEN;
    patchedEventSource.CLOSED = NativeEventSource.CLOSED;
    window.EventSource = patchedEventSource;
  }

  function cleanupLegacyServiceWorkers() {
    try {
      if (
        !navigator.serviceWorker ||
        typeof navigator.serviceWorker.getRegistrations !== "function"
      )
        return;
      navigator.serviceWorker
        .getRegistrations()
        .then(function (registrations) {
          return Promise.all(
            registrations.map(function (registration) {
              return registration.unregister();
            }),
          );
        })
        .catch(function (error) {
          console.warn("[BOOT] Service worker cleanup failed:", error);
        });
    } catch (error) {
      console.warn("[BOOT] Service worker cleanup unavailable:", error);
    }
  }

  function cleanupLegacyCaches() {
    try {
      if (!window.caches || typeof window.caches.keys !== "function") return;
      window.caches
        .keys()
        .then(function (keys) {
          return Promise.all(
            keys
              .filter(function (key) {
                return /^logos-pro-/i.test(key);
              })
              .map(function (key) {
                return window.caches.delete(key);
              }),
          );
        })
        .catch(function (error) {
          console.warn("[BOOT] Cache cleanup failed:", error);
        });
    } catch (error) {
      console.warn("[BOOT] Cache cleanup unavailable:", error);
    }
  }

  // The page retains its loaded UI version until a safe reload. A backend checkout
  // alone must never make an old tab claim it is already running the new interface.
  var pendingUiVersion = "";
  var checkingVersion = false;
  var reloadingVersion = false;
  var loadedLabel = window.__LOGOS_APP_REVISION === "unknown"
    ? window.__LOGOS_STATIC_VERSION.slice(0, 8)
    : window.__LOGOS_APP_REVISION.slice(0, 8);

  function versionBadge(state, suffix) {
    var badge = document.getElementById("app-version");
    if (!badge) return;
    badge.setAttribute("data-update-state", state);
    badge.textContent = "Версия " + loadedLabel + (suffix ? " · " + suffix : "");
  }

  function safeToRefresh() {
    if (document.readyState === "loading" || typeof document.querySelectorAll !== "function") return false;
    if (window.LogosDispatcher && typeof window.LogosDispatcher.isBusy === "function" && window.LogosDispatcher.isBusy()) return false;
    // A reply, voice recording, settings dialog, file selection or draft must survive updates.
    if (document.querySelector(".typing") || document.querySelector(".recording")) return false;
    var modals = Array.from(document.querySelectorAll(".modal"))
      .concat(Array.from(document.querySelectorAll('[role="dialog"]')));
    for (var index = 0; index < modals.length; index += 1) {
      var modal = modals[index];
      if (!modal.hidden && modal.getAttribute("aria-hidden") !== "true" &&
          (modal.classList.contains("visible") || modal.classList.contains("open") || modal.getAttribute("aria-modal") === "true")) return false;
    }
    var fields = Array.from(document.querySelectorAll("textarea"))
      .concat(Array.from(document.querySelectorAll("input")))
      .concat(Array.from(document.querySelectorAll('[contenteditable="true"]')));
    for (var fieldIndex = 0; fieldIndex < fields.length; fieldIndex += 1) {
      var field = fields[fieldIndex];
      // Closed settings contain saved defaults, not an active unsent draft.
      if (typeof field.closest === "function" && field.closest(".modal")) continue;
      if (field.hidden || (typeof field.closest === "function" && field.closest("[hidden]"))) continue;
      if (field.type === "file" && field.files && field.files.length) return false;
      if (field.getAttribute("id") === "msg-input" && String(field.value || "").trim()) return false;
      if (field.tagName === "TEXTAREA" && String(field.value || "").trim()) return false;
      if (field.getAttribute("contenteditable") === "true" && String(field.textContent || "").trim()) return false;
    }
    return true;
  }

  function refreshIfSafe() {
    if (!pendingUiVersion || reloadingVersion) return false;
    if (!safeToRefresh()) {
      versionBadge("pending", "обновление готово, ожидаю окончания работы");
      return false;
    }
    if (!window.location || typeof window.location.reload !== "function") return false;
    reloadingVersion = true;
    versionBadge("reloading", "обновляю интерфейс");
    // Reload the current URL, preserving its selected conversation and query parameters.
    window.location.reload();
    return true;
  }

  function checkVersion() {
    if (checkingVersion || reloadingVersion || !window.__LOGOS_STATIC_VERSION) return Promise.resolve(false);
    checkingVersion = true;
    var options = { cache: "no-store", credentials: "same-origin" };
    if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
      options.signal = AbortSignal.timeout(5000);
    }
    return window.fetch("/api/version", options)
      .then(function (response) {
        if (!response.ok) throw new Error("Version unavailable");
        return response.json();
      })
      .then(function (version) {
        if (!version || !/^[0-9a-f]{64}$/.test(version.ui_version)) throw new Error("Invalid version");
        if (version.ui_version !== window.__LOGOS_STATIC_VERSION) {
          pendingUiVersion = version.ui_version;
          return refreshIfSafe();
        }
        pendingUiVersion = "";
        versionBadge("current", "");
        return false;
      })
      .catch(function () {
        versionBadge("unknown", "проверка обновления недоступна");
        return false;
      })
      .then(function (result) {
        checkingVersion = false;
        return result;
      });
  }

  window.LogosVersion = Object.freeze({ check: checkVersion, refreshIfSafe: refreshIfSafe });
  function startVersionChecks() {
    if (!window.__LOGOS_STATIC_VERSION) return;
    checkVersion();
    if (typeof window.setInterval === "function") window.setInterval(checkVersion, 30000);
  }
  if (typeof document.addEventListener === "function") {
    document.addEventListener("DOMContentLoaded", startVersionChecks);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) checkVersion();
    });
  }

  cleanupLegacyServiceWorkers();
  cleanupLegacyCaches();
})();
