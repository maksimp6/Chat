/* ExecutionTrace export actions for the existing Trace Viewer. */
(function () {
  "use strict";

  var currentTrace = null;
  var buttonId = "logos-trace-download-btn";
  var uploadButtonId = "logos-trace-upload-btn";

  function safeName(value) {
    return (
      window.LogosWikifunctions.replaceRegex("[^a-zA-Z0-9._-]+", "_", String(value || "trace"), "g")
        .slice(0, 80) || "trace"
    );
  }

  function redact(value, seen) {
    if (value === null || typeof value !== "object") return value;
    seen = seen || new WeakSet();
    if (seen.has(value)) return "[Circular]";
    seen.add(value);
    var sensitive =
      /(^|_)(api[_-]?key|authorization|token|secret|password|cookie|credential|private[_-]?key)(_|$)/i;
    var result;
    if (Array.isArray(value)) {
      result = value.map(function (item) {
        return redact(item, seen);
      });
    } else {
      result = {};
      Object.keys(value).forEach(function (key) {
        if (!sensitive.test(key)) result[key] = redact(value[key], seen);
      });
    }
    seen.delete(value);
    return result;
  }

  function buildPayload() {
    if (!currentTrace) throw new Error("Трейс недоступен");
    return JSON.stringify(redact(currentTrace), null, 2);
  }

  function traceFilename() {
    return (
      safeName(
        currentTrace && (currentTrace.trace_id || currentTrace.id || currentTrace.invocation_id),
      ) + ".json"
    );
  }

  function setButtonState(button, text, disabled) {
    if (!button) return;
    button.disabled = !!disabled;
    button.textContent = text;
  }

  function defer(callback, delayMs) {
    if (window.LogosCoreAPI && window.LogosCoreAPI.scheduler) {
      return window.LogosCoreAPI.scheduler.defer(callback, delayMs);
    }
    throw new Error("Core scheduler unavailable");
  }

  function download() {
    var button = document.getElementById(buttonId);
    setButtonState(button, "⏳", true);
    var payload;
    try {
      payload = buildPayload();
      var blob = new Blob([payload], { type: "application/json;charset=utf-8" });
      var url = URL.createObjectURL(blob);
      var link = document.createElement("a");
      link.href = url;
      link.download = traceFilename();
      link.style.display = "none";
      document.body.appendChild(link);
      link.click();
      link.remove();
      defer(function () {
        URL.revokeObjectURL(url);
      }, 1000);
      setButtonState(button, "✓ JSON", true);
      defer(function () {
        setButtonState(button, "⇩ JSON", false);
      }, 1200);
    } catch (error) {
      setButtonState(button, "⚠ JSON", true);
      defer(function () {
        setButtonState(button, "⇩ JSON", false);
      }, 1600);
      LogosToast.error("Не удалось подготовить трейс");
    }
  }

  function uploadToFileManager() {
    var payload;
    try {
      payload = buildPayload();
    } catch (error) {
      LogosToast.error("Не удалось подготовить трейс");
      return;
    }

    var blob = new Blob([payload], { type: "application/json" });

    var button = document.getElementById(uploadButtonId);
    if (button) {
      button.disabled = true;
      button.textContent = "⏳ Загрузка...";
    }

    // Sent in pieces that survive a broken connection (static/upload.js): on a slow link a trace of any size arrives.
    Promise.resolve()
      .then(function () {
        return window.LogosUpload.send(blob, {
          name: traceFilename(),
          kind: "file",
          purpose: "assistants",
          onProgress: function (received, size) {
            var currentButton = document.getElementById(uploadButtonId);
            if (currentButton && size > 0) currentButton.textContent = "⏳ " + Math.floor((received * 100) / size) + "%";
          },
        });
      })
      .then(function () {
        LogosToast.show("Трейс загружен в файловый менеджер.");
      })
      .catch(function (error) {
        var reason = error && error.body && error.body.error === "completion_uncertain"
          ? "сервер не ответил вовремя, трейс мог сохраниться: проверьте файловый менеджер"
          : error && error.status
            ? "сервер отклонил файл (HTTP " + error.status + ")"
            : "ошибка сервера";
        LogosToast.error("Не удалось загрузить трейс: " + reason);
      })
      .finally(function () {
        var currentButton = document.getElementById(uploadButtonId);
        if (currentButton) {
          currentButton.disabled = false;
          currentButton.textContent = "☁ Файлы";
        }
      });
  }

  function installButton() {
    var header = document.querySelector(".logos-trace-header");
    if (!header) return;
    var close = header.querySelector("button");
    if (!header.querySelector("#" + buttonId)) {
      var button = document.createElement("button");
      button.id = buttonId;
      button.type = "button";
      button.className = "logos-btn logos-trace-btn";
      button.title = "Скачать полный трейс в JSON";
      button.setAttribute("aria-label", "Скачать трейс в JSON");
      button.textContent = "⇩ JSON";
      // Fixed width: the label changes to a spinner while the request runs, and the header must not jump.
      button.style.minWidth = "8.5em";
      button.addEventListener("click", download);
      if (close) header.insertBefore(button, close);
      else header.appendChild(button);
    }
    if (!header.querySelector("#" + uploadButtonId)) {
      var uploadButton = document.createElement("button");
      uploadButton.id = uploadButtonId;
      uploadButton.type = "button";
      uploadButton.className = "logos-btn logos-trace-btn";
      uploadButton.title = "Загрузить трейс в файловый менеджер";
      uploadButton.setAttribute("aria-label", "Загрузить трейс в файловый менеджер");
      uploadButton.textContent = "☁ Файлы";
      uploadButton.style.minWidth = "8.5em";
      uploadButton.addEventListener("click", uploadToFileManager);
      if (close) header.insertBefore(uploadButton, close);
      else header.appendChild(uploadButton);
    }
  }

  function hook() {
    if (typeof window.openTraceViewer !== "function" || window.openTraceViewer.__downloadHook)
      return;
    var original = window.openTraceViewer;
    function wrapped(trace) {
      currentTrace = trace;
      var result = original.apply(this, arguments);
      window.requestAnimationFrame(installButton);
      defer(installButton, 50);
      return result;
    }
    wrapped.__downloadHook = true;
    window.openTraceViewer = wrapped;
  }

  hook();
  window.addEventListener("logos:trace-viewer-ready", function () {
    hook();
    installButton();
  });
})();
