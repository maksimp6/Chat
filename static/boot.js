(function () {
  "use strict";

  var currentScript = document.currentScript;
  var config = currentScript && currentScript.dataset ? currentScript.dataset : {};

  window.__ALICE_USER_ID = window.__ALICE_USER_ID || "";
  window.__ALICE_BASE_PATH = config.basePath || "";
  window.__ALICE_STATIC_BASE = config.staticBase || "/static";
  window.__ALICE_STATIC_VERSION = config.staticVersion || "";

  var basePath = window.__ALICE_BASE_PATH || "";
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

  window.AliceBoot = Object.freeze({
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
    window.dispatchEvent(new CustomEvent("alice:diagnostic", { detail: details }));
  }

  console.info("[BOOT] hello: Alice Pro frontend boot loaded");
  window.addEventListener(
    "error",
    function (event) {
      var criticalId = criticalIdForError(event);
      if (criticalId && failedCriticalScripts.indexOf(criticalId) === -1) {
        failedCriticalScripts.push(criticalId);
        showDegradedStatus();
        emitDiagnostic("error", "critical_script_failed", "Critical script unavailable", {
          script: criticalId,
        });
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
    if (window.__ALICE_USER_ID) headers.set("X-Alice-User-ID", window.__ALICE_USER_ID);
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
                return /^alice-pro-/i.test(key);
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

  cleanupLegacyServiceWorkers();
  cleanupLegacyCaches();
})();
