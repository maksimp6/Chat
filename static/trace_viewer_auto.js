/* Bridges trace metadata in chat messages to the full Execution Trace viewer. */
(function () {
    "use strict";

    function enhance(root) {
        if (!root || typeof window.openTraceViewer !== "function") return;
        var nodes = root.querySelectorAll ? root.querySelectorAll("details") : [];
        nodes.forEach(function (details) {
            if (details.dataset.traceViewerBound === "1") return;
            var summary = details.querySelector("summary");
            var pre = details.querySelector("pre");
            if (!summary || !pre) return;

            var summaryText = summary.textContent.trim();
            if (!/^🔍 Trace\b/.test(summaryText)) return;

            var raw = pre.textContent;
            try { raw = JSON.parse(raw); } catch (_) { return; }
            if (!raw || typeof raw !== "object") return;

            details.dataset.traceViewerBound = "1";
            details.style.width = "auto";
            details.style.display = "inline-block";
            details.style.marginTop = "4px";

            var message = details.closest(".msg");
            var isError = !!(message && message.classList.contains("bot") && /(^|\s)⚠️\s*Ошибка/.test(message.textContent));
            var button = document.createElement("button");
            button.type = "button";
            button.style.cssText = "font-size:11px;background:transparent;border:0;padding:0;color:inherit;cursor:pointer;font-weight:600;";
            button.textContent = isError ? "🔍 Трейс ошибки" : summaryText;
            button.title = isError ? "Открыть Execution Trace ошибки" : "Открыть Execution Trace viewer";
            button.onclick = function (event) {
                event.preventDefault();
                event.stopPropagation();
                window.openTraceViewer(raw);
            };
            summary.textContent = "";
            summary.appendChild(button);
            pre.remove();
        });
    }

    function init() {
        enhance(document);
        var observer = new MutationObserver(function (mutations) {
            mutations.forEach(function (mutation) {
                mutation.addedNodes.forEach(function (node) {
                    if (node.nodeType === 1) enhance(node);
                });
            });
        });
        observer.observe(document.body, { childList: true, subtree: true });
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
})();
