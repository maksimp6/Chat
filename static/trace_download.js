/* ExecutionTrace export actions for the existing Trace Viewer. */
(function () {
  "use strict";

  var currentTrace = null;
  var buttonId = "logos-trace-download-btn";
  var uploadButtonId = "logos-trace-upload-btn";
  var DOWNLOAD_LABEL = "Скачать JSON";
  var UPLOAD_LABEL = "В файлы";

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
    if (!currentTrace) throw new Error("Трассировка недоступна");
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
    setButtonState(button, "Готовим…", true);
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
      setButtonState(button, "Скачано", true);
      defer(function () {
        setButtonState(button, DOWNLOAD_LABEL, false);
      }, 1200);
    } catch (error) {
      setButtonState(button, "Не скачано", true);
      defer(function () {
        setButtonState(button, DOWNLOAD_LABEL, false);
      }, 1600);
      LogosToast.error("Не удалось подготовить трассировку");
    }
  }

  function uploadToFileManager() {
    var payload;
    try {
      payload = buildPayload();
    } catch (error) {
      LogosToast.error("Не удалось подготовить трассировку");
      return;
    }

    var blob = new Blob([payload], { type: "application/json" });

    var button = document.getElementById(uploadButtonId);
    if (button) {
      button.disabled = true;
      button.textContent = "Загружаем…";
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
            if (currentButton && size > 0) {
              currentButton.textContent = "Загружаем " + Math.floor((received * 100) / size) + "%";
            }
          },
        });
      })
      .then(function () {
        LogosToast.show("Трассировка загружена в файловый менеджер.");
      })
      .catch(function (error) {
        var code = error && error.body ? error.body.error : null;
        var reason = code === "completion_uncertain"
          ? "сервер не ответил вовремя, трассировка могла сохраниться: проверьте файловый менеджер"
          : code === "files_unreachable" || (error && (error.status === 429 || error.status === 408))
            ? "файлы временно недоступны, повторите позже"
            : error && error.body && error.body.message
              ? error.body.message
              : error && error.status
                ? "сервер отклонил файл (HTTP " + error.status + ")"
                : "ошибка сервера";
        LogosToast.error("Не удалось загрузить трассировку: " + reason);
      })
      .finally(function () {
        var currentButton = document.getElementById(uploadButtonId);
        if (currentButton) {
          currentButton.disabled = false;
          currentButton.textContent = UPLOAD_LABEL;
        }
      });
  }

  // The viewer's action row (trace_viewer.js) takes both buttons after its own copy button.
  function installButton() {
    var actions = document.querySelector(".logos-trace-actions");
    if (!actions) return;
    if (!actions.querySelector("#" + buttonId)) {
      var button = document.createElement("button");
      button.id = buttonId;
      button.type = "button";
      button.className = "logos-btn logos-trace-btn";
      button.title = "Скачать всю трассировку в JSON";
      button.textContent = DOWNLOAD_LABEL;
      // Fixed width: the label changes while the file is prepared, and the row must not jump.
      button.style.minWidth = "8.5em";
      button.addEventListener("click", download);
      actions.appendChild(button);
    }
    if (!actions.querySelector("#" + uploadButtonId)) {
      var uploadButton = document.createElement("button");
      uploadButton.id = uploadButtonId;
      uploadButton.type = "button";
      uploadButton.className = "logos-btn logos-trace-btn";
      uploadButton.title = "Загрузить трассировку в файловый менеджер";
      uploadButton.textContent = UPLOAD_LABEL;
      uploadButton.style.minWidth = "8.5em";
      uploadButton.addEventListener("click", uploadToFileManager);
      actions.appendChild(uploadButton);
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
