/* ExecutionTrace export action for the existing Trace Viewer. */
(function () {
    "use strict";

    var currentTrace = null;
    var buttonId = "alice-trace-download-btn";

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

    function download() {
        if (!currentTrace) {
            window.alert("Трейс недоступен для скачивания");
            return;
        }
        var payload;
        try { payload = JSON.stringify(redact(currentTrace), null, 2); }
        catch (error) {
            window.alert("Не удалось подготовить трейс для скачивания");
            return;
        }
        var id = currentTrace.trace_id || currentTrace.id || currentTrace.invocation_id || "trace";
        var blob = new Blob([payload], { type: "application/json;charset=utf-8" });
        var url = URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = url;
        link.download = safeName(id) + ".json";
        link.style.display = "none";
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    }

    function installButton() {
        var header = document.querySelector(".alice-trace-header");
        if (!header || header.querySelector("#" + buttonId)) return;
        var close = header.querySelector("button");
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
