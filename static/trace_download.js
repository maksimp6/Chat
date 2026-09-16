/* ExecutionTrace export actions for the existing Trace Viewer. */
(function () {
    "use strict";

    var currentTrace = null;
    var buttonId = "alice-trace-download-btn";
    var uploadButtonId = "alice-trace-upload-btn";

    function safeName(value) {
        return String(value || "trace").replace(/[^a-zA-Z0-9._-]+/g, "_").slice(0, 80) || "trace";
    }

    function redact(value, seen) {
        if (value === null || typeof value !== "object") return value;
        seen = seen || [];
        if (seen.indexOf(value) !== -1) return "[Circular]";
        seen.push(value);
        var sensitive = /(^|_)(api[_-]?key|authorization|token|secret|password|cookie|credential|private[_-]?key)(_|$)/i;
        var result;
        if (Array.isArray(value)) {
            result = value.map(function (item) { return redact(item, seen); });
        } else {
            result = {};
            Object.keys(value).forEach(function (key) {
                if (!sensitive.test(key)) result[key] = redact(value[key], seen);
            });
        }
        seen.pop();
        return result;
    }

    function buildPayload() {
        if (!currentTrace) throw new Error("Трейс недоступен");
        return JSON.stringify(redact(currentTrace), null, 2);
    }

    function traceFilename() {
        return safeName(currentTrace && (currentTrace.trace_id || currentTrace.id || currentTrace.invocation_id)) + ".json";
    }

    function download() {
        var payload;
        try { payload = buildPayload(); }
        catch (error) { window.alert(error.message || "Не удалось подготовить трейс"); return; }
        var blob = new Blob([payload], { type: "application/json;charset=utf-8" });
        var url = URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = url;
        link.download = traceFilename();
        link.style.display = "none";
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    }

    function uploadToFileManager() {
        var payload;
        try { payload = buildPayload(); }
        catch (error) { window.alert(error.message || "Не удалось подготовить трейс"); return; }

        var form = new FormData();
        form.append("file", new Blob([payload], { type: "application/json" }), traceFilename());
        form.append("purpose", "assistants");

        var button = document.getElementById(uploadButtonId);
        if (button) { button.disabled = true; button.textContent = "⏳ Загрузка..."; }

        fetch("/api/files", { method: "POST", body: form })
            .then(function (response) {
                return response.json().catch(function () { return {}; }).then(function (data) {
                    if (!response.ok) throw new Error(data.error || "Ошибка загрузки");
                    return data;
                });
            })
            .then(function () {
                window.alert("Трейс загружен в файловый менеджер: " + traceFilename());
            })
            .catch(function (error) {
                window.alert("Не удалось загрузить трейс: " + error.message);
            })
            .finally(function () {
                var currentButton = document.getElementById(uploadButtonId);
                if (currentButton) { currentButton.disabled = false; currentButton.textContent = "☁ Файлы"; }
            });
    }

    function installButton() {
        var header = document.querySelector(".alice-trace-header");
        if (!header) return;
        var close = header.querySelector("button");
        if (!header.querySelector("#" + buttonId)) {
            var button = document.createElement("button");
            button.id = buttonId;
            button.type = "button";
            button.className = "alice-trace-btn";
            button.title = "Скачать полный трейс в JSON";
            button.setAttribute("aria-label", "Скачать трейс в JSON");
            button.textContent = "⇩ JSON";
            button.addEventListener("click", download);
            if (close) header.insertBefore(button, close); else header.appendChild(button);
        }
        if (!header.querySelector("#" + uploadButtonId)) {
            var uploadButton = document.createElement("button");
            uploadButton.id = uploadButtonId;
            uploadButton.type = "button";
            uploadButton.className = "alice-trace-btn";
            uploadButton.title = "Загрузить трейс в файловый менеджер";
            uploadButton.setAttribute("aria-label", "Загрузить трейс в файловый менеджер");
            uploadButton.textContent = "☁ Файлы";
            uploadButton.addEventListener("click", uploadToFileManager);
            if (close) header.insertBefore(uploadButton, close); else header.appendChild(uploadButton);
        }
    }

    function hook() {
        if (typeof window.openTraceViewer !== "function" || window.openTraceViewer.__downloadHook) return;
        var original = window.openTraceViewer;
        function wrapped(trace) {
            currentTrace = trace;
            var result = original.apply(this, arguments);
            window.requestAnimationFrame(installButton);
            window.setTimeout(installButton, 50);
            return result;
        }
        wrapped.__downloadHook = true;
        window.openTraceViewer = wrapped;
    }

    hook();
    window.setInterval(function () { hook(); installButton(); }, 500);
})();
