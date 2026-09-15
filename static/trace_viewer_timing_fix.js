/* Fixes Execution Trace Waterfall to use real Responses API request intervals. */
(function () {
    "use strict";

    function finite(v) {
        var n = Number(v);
        return Number.isFinite(n) ? n : null;
    }

    function responseInterval(response, trace) {
        var start = finite(response.start_timestamp);
        var end = finite(response.end_timestamp);

        if (start !== null && end !== null && end >= start) {
            return { start: start, end: end };
        }

        var step = response.step;
        var requests = Array.isArray(trace.api_requests) ? trace.api_requests : [];
        for (var i = requests.length - 1; i >= 0; i--) {
            if (requests[i] && requests[i].step === step) {
                start = finite(requests[i].timestamp);
                break;
            }
        }

        var events = Array.isArray(trace.events) ? trace.events : [];
        for (var j = events.length - 1; j >= 0; j--) {
            var ev = events[j] || {};
            if (ev.type !== "api_request_completed") continue;
            var p = ev.payload || {};
            if (p.step !== step) continue;
            if (start === null) start = finite(p.start_timestamp);
            end = finite(p.end_timestamp);
            break;
        }

        if (end === null) end = finite(response.end_timestamp) || finite(response.timestamp);
        return start !== null && end !== null && end >= start ? { start: start, end: end } : null;
    }

    function allTimes(trace) {
        var times = [];
        function add(v) { v = finite(v); if (v !== null) times.push(v); }
        add(trace.created_at);

        (trace.events || []).forEach(function (ev) {
            add(ev && ev.timestamp);
            var p = ev && ev.payload || {};
            add(p.start_timestamp);
            add(p.end_timestamp);
        });
        (trace.tool_calls || []).forEach(function (tool) {
            add(tool && tool.start_timestamp);
            add(tool && tool.end_timestamp);
        });
        (trace.responses || []).forEach(function (response) {
            var span = responseInterval(response, trace);
            if (span) { add(span.start); add(span.end); }
            else add(response && response.timestamp);
        });

        return times;
    }

    function formatMs(ms) {
        var n = Number(ms);
        if (!Number.isFinite(n)) return "—";
        return n < 1000 ? Math.round(n) + " ms" : (n / 1000).toFixed(n < 10000 ? 2 : 1) + " s";
    }

    function patchWaterfall(trace) {
        var waterfall = document.querySelector(".alice-trace-waterfall");
        if (!waterfall) return;

        var times = allTimes(trace);
        if (!times.length) return;
        var min = Math.min.apply(Math, times);
        var max = Math.max.apply(Math, times);
        var span = Math.max(0.001, max - min);

        var rows = waterfall.querySelectorAll(".alice-trace-timeline-row");
        rows.forEach(function (row) {
            var label = row.querySelector(".alice-trace-row-label");
            if (!label) return;
            var match = label.textContent.trim().match(/^🤖\s*Responses API #(\d+)/);
            if (!match) return;

            var step = Number(match[1]);
            var response = (trace.responses || []).find(function (r) {
                return Number(r.step) === step;
            });
            if (!response) return;

            var interval = responseInterval(response, trace);
            if (!interval) return;

            var bar = row.querySelector(".alice-trace-bar.response");
            if (!bar) return;

            var left = Math.max(0, Math.min(100, (interval.start - min) / span * 100));
            var width = Math.max(0.9, Math.min(100 - left, (interval.end - interval.start) / span * 100));
            bar.style.left = left + "%";
            bar.style.width = width + "%";
            bar.classList.remove("inferred");
            bar.title = "Responses API #" + step + " · " + formatMs((interval.end - interval.start) * 1000) + " · real request interval";
        });

        var subtitle = waterfall.querySelector(".alice-trace-waterfall-sub");
        if (subtitle) subtitle.textContent = "tools = real start/end · Responses = real request start/end · events = instant";
    }

    function patchInspector(trace) {
        var title = document.querySelector(".alice-trace-inspector-title");
        if (!title) return;
        var match = title.textContent.trim().match(/^Responses API #(\d+)/);
        if (!match) return;

        var step = Number(match[1]);
        var response = (trace.responses || []).find(function (r) { return Number(r.step) === step; });
        if (!response) return;
        var interval = responseInterval(response, trace);
        if (!interval) return;

        document.querySelectorAll(".alice-trace-card").forEach(function (card) {
            var label = card.querySelector(".alice-trace-card-label");
            var value = card.querySelector(".alice-trace-card-value");
            if (label && value && label.textContent.trim() === "Step interval") {
                value.textContent = formatMs((interval.end - interval.start) * 1000);
            }
        });
    }

    function patchAfterRender(trace) {
        requestAnimationFrame(function () {
            patchWaterfall(trace);
            patchInspector(trace);
        });

        var modal = document.querySelector(".alice-trace-modal");
        if (!modal || modal.dataset.timingFixBound === "1") return;
        modal.dataset.timingFixBound = "1";
        var observer = new MutationObserver(function () {
            patchWaterfall(trace);
            patchInspector(trace);
        });
        observer.observe(modal, { childList: true, subtree: true, characterData: true });
    }

    var originalOpen = window.openTraceViewer;
    if (typeof originalOpen !== "function") return;

    window.openTraceViewer = function (trace) {
        originalOpen(trace);
        patchAfterRender(trace || {});
    };
})();
