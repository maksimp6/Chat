/* Execution Trace Python error inspector. */
(function () {
  "use strict";

  function safe(value) {
    try {
      return JSON.stringify(value, null, 2);
    } catch (_) {
      return String(value);
    }
  }

  function renderPythonErrors(trace) {
    var errors = Array.isArray(trace && trace.errors) ? trace.errors : [];
    var pythonErrors = errors.filter(function (e) {
      return e && e.python_exception;
    });
    if (!pythonErrors.length) return;

    var roots = document.querySelectorAll(".logos-trace-inspector-inner");
    roots.forEach(function (root) {
      if (root.querySelector(".logos-trace-python-errors")) return;

      var box = document.createElement("section");
      box.className = "logos-trace-python-errors";

      var head = document.createElement("div");
      head.className = "logos-trace-python-head";
      head.innerHTML =
        "<span>🐍 Python error state</span><b>" +
        pythonErrors.length +
        " error" +
        (pythonErrors.length === 1 ? "" : "s") +
        "</b>";
      box.appendChild(head);

      pythonErrors.forEach(function (entry, index) {
        var exc = entry.python_exception || {};
        var card = document.createElement("div");
        card.className = "logos-trace-python-card";

        var summary = document.createElement("div");
        summary.className = "logos-trace-python-summary";
        summary.textContent =
          (exc.exception_type || "Exception") +
          ": " +
          (exc.exception_message || entry.error || "Unknown error");
        card.appendChild(summary);

        var meta = document.createElement("div");
        meta.className = "logos-trace-python-meta";
        meta.textContent =
          "source: " +
          (entry.source || "unknown") +
          (entry.step != null ? " · step: " + entry.step : "") +
          " · captured frame state: " +
          (exc.frames || []).length;
        card.appendChild(meta);

        var details = document.createElement("details");
        details.className = "logos-trace-python-details";
        if (index === 0) details.open = true;
        var toggle = document.createElement("summary");
        toggle.textContent = "Показать traceback и locals";
        details.appendChild(toggle);

        var pre = document.createElement("pre");
        pre.className = "logos-trace-pre logos-trace-python-pre";
        pre.textContent = safe(exc);
        details.appendChild(pre);
        card.appendChild(details);
        box.appendChild(card);
      });

      var anchor = root.querySelector(".logos-trace-tabs") || root.firstElementChild;
      if (anchor) root.insertBefore(box, anchor);
      else root.insertBefore(box, root.firstChild);
    });
  }

  function injectStyles() {
    if (document.getElementById("logos-trace-python-error-style")) return;
    var style = document.createElement("style");
    style.id = "logos-trace-python-error-style";
    style.textContent = `
.logos-trace-python-errors{margin:0 0 12px;padding:10px;border:1px solid rgba(255,90,90,.35);border-radius:10px;background:rgba(180,40,40,.08)}
.logos-trace-python-head{display:flex;justify-content:space-between;gap:8px;align-items:center;font-size:12px;font-weight:800;color:#ff8f8f;margin-bottom:8px}
.logos-trace-python-card{padding:9px;border:1px solid rgba(255,90,90,.22);border-radius:8px;background:rgba(127,0,0,.08);margin-top:7px}
.logos-trace-python-summary{font:12px/1.4 ui-monospace,monospace;font-weight:700;word-break:break-word}
.logos-trace-python-meta{font-size:10px;opacity:.62;margin-top:4px}
.logos-trace-python-details{margin-top:8px;font-size:11px}
.logos-trace-python-details summary{cursor:pointer;opacity:.8}
.logos-trace-python-pre{max-height:520px;margin-top:7px}
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
      requestAnimationFrame(function () {
        patch(trace);
      });
    });
  };
})();
