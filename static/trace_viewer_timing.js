/* Safe Execution Trace timing patch. */
(function () {
  "use strict";
  function finite(v) {
    var n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  function interval(r, t) {
    var s = finite(r.start_timestamp),
      e = finite(r.end_timestamp),
      step = Number(r.step);
    if (s !== null && e !== null && e >= s) return { s: s, e: e };
    var q = Array.isArray(t.api_requests) ? t.api_requests : [];
    for (var i = q.length - 1; i >= 0; i--)
      if (Number(q[i].step) === step) {
        s = finite(q[i].timestamp);
        break;
      }
    var ev = Array.isArray(t.events) ? t.events : [];
    for (var j = ev.length - 1; j >= 0; j--) {
      var p = (ev[j] && ev[j].payload) || {};
      if (ev[j].type !== "api_request_completed" || Number(p.step) !== step) continue;
      if (s === null) s = finite(p.start_timestamp);
      e = finite(p.end_timestamp);
      break;
    }
    if (e === null) e = finite(r.timestamp);
    return s !== null && e !== null && e >= s ? { s: s, e: e } : null;
  }
  function fmt(ms) {
    return ms < 1000 ? Math.round(ms) + " ms" : (ms / 1000).toFixed(ms < 10000 ? 2 : 1) + " s";
  }
  function patch(t) {
    var w = document.querySelector(".alice-trace-waterfall");
    if (!w) return;
    var all = [],
      add = function (v) {
        v = finite(v);
        if (v !== null) all.push(v);
      };
    add(t.created_at);
    (t.events || []).forEach(function (x) {
      add(x.timestamp);
      add(x.payload && x.payload.start_timestamp);
      add(x.payload && x.payload.end_timestamp);
    });
    (t.tool_calls || []).forEach(function (x) {
      add(x.start_timestamp);
      add(x.end_timestamp);
    });
    (t.responses || []).forEach(function (r) {
      var x = interval(r, t);
      if (x) {
        add(x.s);
        add(x.e);
      } else add(r.timestamp);
    });
    if (!all.length) return;
    var min = Math.min.apply(Math, all),
      max = Math.max.apply(Math, all),
      span = Math.max(0.001, max - min);
    w.querySelectorAll(".alice-trace-timeline-row").forEach(function (row) {
      var label = row.querySelector(".alice-trace-row-label"),
        m = label && label.textContent.trim().match(/^🤖\s*Responses API #(\d+)/);
      if (!m) return;
      var r = (t.responses || []).find(function (x) {
        return Number(x.step) === Number(m[1]);
      });
      if (!r) return;
      var x = interval(r, t),
        bar = row.querySelector(".alice-trace-bar.response");
      if (!x || !bar) return;
      var left = Math.max(0, Math.min(100, ((x.s - min) / span) * 100)),
        width = Math.max(0.9, Math.min(100 - left, ((x.e - x.s) / span) * 100));
      bar.style.left = left + "%";
      bar.style.width = width + "%";
      bar.classList.remove("inferred");
      bar.title =
        "Responses API #" + m[1] + " · " + fmt((x.e - x.s) * 1000) + " · real request interval";
    });
    var sub = w.querySelector(".alice-trace-waterfall-sub");
    if (sub)
      sub.textContent =
        "tools = real start/end · Responses = real request start/end · events = instant";
  }
  var open = window.openTraceViewer;
  if (typeof open !== "function") return;
  window.openTraceViewer = function (t) {
    open(t);
    requestAnimationFrame(function () {
      patch(t || {});
    });
  };
})();
