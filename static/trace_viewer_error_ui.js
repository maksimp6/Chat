/* Execution Trace Python error inspector. */
(function () {
    "use strict";

    function safe(value) {
        try { return JSON.stringify(value, null, 2); }
        catch (_) { return String(value); }
    }

    function renderPythonErrors(trace) {
        var errors = Array.isArray(trace && trace.errors) ? trace.errors : [];
        var pythonErrors = errors.filter(function (e) {
            return e && e.python_exception;
        });
        if (!pythonErrors.length) return;

        var roots = document.querySelectorAll(".alice-trace-inspector-inner");
        roots.forEach(function (root) {
            if (root.querySelector(".alice-trace-python-errors")) return;

            var box = document.createElement("section");
            box.className = "alice-trace-python-errors";

            var head = document.createElement("div");
            head.className = "alice-trace-python-head";
            head.innerHTML = "<span>🐍 Python error state</span><b>" + pythonErrors.length + " error" + (pythonErrors.length === 1 ? "" : "s") + "</b>";
            box.appendChild(head);

            pythonErrors.forEach(function (entry, index) {
                var exc = entry.python_exception || {};
                var card = document.createElement("div");
                card.className = "alice-trace-python-card";

                var summary = document.createElement("div");
                summary.className = "alice-trace-python-summary";
                summary.textContent = (exc.exception_type || "Exception") + ": " + (exc.exception_message || entry.error || "Unknown error");
                card.appendChild(summary);

                var meta = document.createElement("div");
                meta.className = "alice-trace-python-meta";
                meta.textContent = "source: " + (entry.source || "unknown") +
                    (entry.step != null ? " · step: " + entry.step : "") +
                    " · captured frame state: " + ((exc.frames || []).length);
                card.appendChild(meta);

                var details = document.createElement("details");
                details.className = "alice-trace-python-details";
                if (index === 0) details.open = true;
                var toggle = document.createElement("summary");
                toggle.textContent = "Показать traceback и locals";
                details.appendChild(toggle);

                var pre = document.createElement("pre");
                pre.className = "alice-trace-pre alice-trace-python-pre";
                pre.textContent = safe(exc);
                details.appendChild(pre);
                card.appendChild(details);
                box.appendChild(card);
            });

            var anchor = root.querySelector(".alice-trace-tabs") || root.firstElementChild;
            if (anchor) root.insertBefore(box, anchor);
            else root.insertBefore(box, root.firstChild);
        });
    }

    function injectStyles() {
        if (document.getElementById("alice-trace-python-error-style")) return;
        var style = document.createElement("style");
        style.id = "alice-trace-python-error-style";
        style.textContent = `
.alice-trace-python-errors{margin:0 0 12px;padding:10px;border:1px solid rgba(255,90,90,.35);border-radius:10px;background:rgba(180,40,40,.08)}
.alice-trace-python-head{display:flex;justify-content:space-between;gap:8px;align-items:center;font-size:12px;font-weight:800;color:#ff8f8f;margin-bottom:8px}
.alice-trace-python-card{padding:9px;border:1px solid rgba(255,90,90,.22);border-radius:8px;background:rgba(127,0,0,.08);margin-top:7px}
.alice-trace-python-summary{font:12px/1.4 ui-monospace,monospace;font-weight:700;word-break:break-word}
.alice-trace-python-meta{font-size:10px;opacity:.62;margin-top:4px}
.alice-trace-python-details{margin-top:8px;font-size:11px}
.alice-trace-python-details summary{cursor:pointer;opacity:.8}
.alice-trace-python-pre{max-height:520px;margin-top:7px}
`;
        document.head.appendChild(style);
    }

    function patch(trace) {
        injectStyles();
        renderPythonErrors(trace || {});
    }

    var open = window.openTraceViewer;
    if (typeof open !== "function") return;
    window.openTraceViewer = function (trace) {
        open(trace);
        requestAnimationFrame(function () {
            patch(trace);
            requestAnimationFrame(function () { patch(trace); });
        });
    };
})();
