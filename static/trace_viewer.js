/* Logos Execution Trace viewer. Mobile master-detail with readable JSON depth control. */
(function () {
  "use strict";

  var state = {
    trace: null,
    items: null,
    selected: null,
    modal: null,
    body: null,
    nav: null,
    list: null,
    detail: null,
    detailTitle: null,
    listScrollTop: 0,
    jsonDepth: 4,
  };

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (key) {
      if (key === "className") node.className = attrs[key];
      else if (key === "title") node.title = attrs[key];
      else if (key === "textContent") node.textContent = attrs[key];
      else node.setAttribute(key, attrs[key]);
    });
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function fmtMs(value) {
    var n = Number(value);
    if (!Number.isFinite(n)) return "—";
    return n < 1000 ? Math.round(n) + " ms" : (n / 1000).toFixed(n < 10000 ? 2 : 1) + " s";
  }
  function fmtNumber(value) {
    var n = Number(value);
    return Number.isFinite(n) ? n.toLocaleString("ru-RU") : "0";
  }
  function normalizeTrace(input) {
    var t = input;
    if (typeof input === "string") {
      try {
        t = JSON.parse(input);
      } catch (_) {
        return null;
      }
    }
    return t && typeof t === "object" ? t : null;
  }
  function getTimestamp(obj) {
    if (!obj || typeof obj !== "object") return null;
    var c = [obj.timestamp, obj.end_timestamp, obj.created_at];
    for (var i = 0; i < c.length; i++) {
      var n = Number(c[i]);
      if (Number.isFinite(n)) return n;
      if (typeof c[i] === "string") {
        var p = Date.parse(c[i]);
        if (Number.isFinite(p)) return p / 1000;
      }
    }
    return null;
  }

  function limitJson(value, depth, level) {
    if (depth === null) return value;
    if (value === null || typeof value !== "object") return value;
    if (level >= depth)
      return Array.isArray(value)
        ? "… " + value.length + " items"
        : "… " + Object.keys(value).length + " keys";
    if (Array.isArray(value))
      return value.map(function (v) {
        return limitJson(v, depth, level + 1);
      });
    var out = {};
    Object.keys(value).forEach(function (k) {
      out[k] = limitJson(value[k], depth, level + 1);
    });
    return out;
  }
  function jsonText(value) {
    if (value === undefined) return "undefined";
    if (typeof value === "string") return value;
    try {
      return JSON.stringify(limitJson(value, state.jsonDepth, 0), null, 2);
    } catch (_) {
      return String(value);
    }
  }

  function injectStyles() {
    if (document.getElementById("logos-trace-viewer-style")) return;
    var s = document.createElement("style");
    s.id = "logos-trace-viewer-style";
    s.textContent = `
.logos-trace-modal{position:fixed;inset:0;z-index:200000;background:rgba(0,0,0,.62);backdrop-filter:blur(8px);display:flex;align-items:stretch;justify-content:center;padding:18px;box-sizing:border-box;overscroll-behavior:contain}
.logos-trace-window{width:min(1500px,100%);height:100%;background:var(--bg-app);color:var(--text-main,#eee);border:1px solid var(--border-color,rgba(255,255,255,.12));border-radius:14px;overflow:hidden;display:flex;flex-direction:column;box-shadow:0 24px 80px rgba(0,0,0,.42);font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.logos-trace-header{display:flex;gap:10px;align-items:center;padding:12px 14px;border-bottom:1px solid var(--border-color,rgba(255,255,255,.1));background:rgba(127,127,127,.06);flex:0 0 auto}.logos-trace-title{font-weight:750;min-width:0}.logos-trace-sub{font:11px ui-monospace,monospace;opacity:.62;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.logos-trace-spacer{flex:1}.logos-trace-btn{border:1px solid var(--border-color,rgba(255,255,255,.14));background:rgba(127,127,127,.08);color:inherit;border-radius:8px;padding:7px 10px;cursor:pointer}.logos-trace-btn:hover{background:rgba(127,127,127,.16)}
.logos-trace-metrics{display:flex;flex-wrap:wrap;gap:7px;padding:8px 12px;border-bottom:1px solid var(--border-color,rgba(255,255,255,.08));font-size:11px;flex:0 0 auto}.logos-trace-metric{padding:5px 8px;border-radius:7px;background:rgba(127,127,127,.07);white-space:nowrap}.logos-trace-metric b{font-weight:700}
.logos-trace-waterfall{padding:10px 12px 12px;border-bottom:1px solid var(--border-color,rgba(255,255,255,.08));overflow-x:auto;overflow-y:hidden;-webkit-overflow-scrolling:touch;touch-action:pan-x;flex:0 0 auto}.logos-trace-waterfall-head{display:flex;gap:10px;justify-content:space-between;align-items:center;margin-bottom:8px;font-size:11px;font-weight:750;text-transform:uppercase;opacity:.72}.logos-trace-waterfall-sub{font-size:10px;font-weight:500;text-transform:none;white-space:nowrap}.logos-trace-timeline{min-width:520px}.logos-trace-axis-labels{display:grid;grid-template-columns:108px 1fr;align-items:end;margin-bottom:4px;font:10px ui-monospace,monospace;opacity:.5}.logos-trace-axis-values{display:flex;justify-content:space-between;padding:0 2px}.logos-trace-timeline-body{position:relative}.logos-trace-gridline{position:absolute;top:0;bottom:0;width:1px;background:rgba(127,127,127,.12);pointer-events:none}.logos-trace-timeline-row{display:grid;grid-template-columns:108px 1fr;min-height:27px;align-items:center}.logos-trace-row-label{padding-right:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:10px;opacity:.72}.logos-trace-row-track{position:relative;height:27px;border-bottom:1px solid rgba(127,127,127,.06)}
.logos-trace-bar{position:absolute;top:7px;height:13px;border-radius:5px;min-width:5px;background:var(--accent,#7aa2ff);opacity:.92;cursor:pointer}.logos-trace-bar.response{background:#a794ff}.logos-trace-bar.pipeline{opacity:.78;background-image:repeating-linear-gradient(135deg,transparent 0,transparent 4px,rgba(255,255,255,.16) 4px,rgba(255,255,255,.16) 6px)}.logos-trace-point{position:absolute;top:9px;width:9px;height:9px;border-radius:50%;background:var(--accent);border:1px solid var(--bg-app);transform:translateX(-50%);cursor:pointer}.logos-trace-point.response{background:#a794ff}
.logos-trace-main{display:flex;min-height:0;flex:1 1 0%;position:relative}.logos-trace-nav{width:280px;min-width:220px;border-right:1px solid var(--border-color,rgba(255,255,255,.08));overflow:auto;padding:8px}.logos-trace-inspector{min-width:0;flex:1;overflow:auto;-webkit-overflow-scrolling:touch}.logos-trace-section-title{font-size:10px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;opacity:.55;padding:7px 8px}
.logos-trace-item{width:100%;box-sizing:border-box;text-align:left;border:0;background:transparent;color:inherit;padding:8px;border-radius:8px;cursor:pointer;display:grid;grid-template-columns:18px minmax(0,1fr) auto;gap:8px;align-items:center}.logos-trace-item:hover,.logos-trace-item.active{background:rgba(127,127,127,.1)}.logos-trace-icon{width:18px;text-align:center}.logos-trace-item-main{min-width:0}.logos-trace-item-name{display:block;font-size:12px;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.logos-trace-item-meta{display:block;font-size:10px;opacity:.55;margin-top:2px}.logos-trace-item-time{font:10px ui-monospace,monospace;opacity:.55;white-space:nowrap;text-align:right}
.logos-trace-mobile-tabs,.logos-trace-mobile-list,.logos-trace-mobile-detail{display:none}.logos-trace-inspector-inner{padding:14px 16px}.logos-trace-inspector-title{font-size:18px;font-weight:750;margin-bottom:3px}.logos-trace-inspector-sub{font-size:11px;opacity:.58;margin-bottom:12px}.logos-trace-tabs{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:10px}.logos-trace-tab{border:1px solid var(--border-color,rgba(255,255,255,.12));background:transparent;color:inherit;border-radius:7px;padding:6px 8px;font-size:11px;cursor:pointer}.logos-trace-tab.active{background:rgba(127,127,127,.12);font-weight:700}.logos-trace-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px;margin-bottom:12px}.logos-trace-card{padding:9px;border:1px solid var(--border-color,rgba(255,255,255,.08));border-radius:8px;background:rgba(127,127,127,.035)}.logos-trace-card-label{font-size:10px;opacity:.55}.logos-trace-card-value{font-size:13px;font-weight:700;margin-top:2px;word-break:break-word}.logos-trace-pre{margin:0;padding:12px;border-radius:9px;background:var(--bg-input);color:var(--text-main,#eee);border:1px solid var(--border-color,rgba(127,127,127,.18));font:11px/1.5 ui-monospace,monospace;white-space:pre;overflow:auto;max-width:100%;max-height:min(55vh,520px);max-height:min(55dvh,520px);text-align:left;tab-size:2;-webkit-overflow-scrolling:touch}.logos-trace-json-wrap{min-width:0;max-width:100%;overflow:hidden}.logos-trace-json-toolbar{display:flex;align-items:center;gap:7px;margin:0 0 8px}.logos-trace-json-label{font-size:11px;font-weight:700;opacity:.72}.logos-trace-depth{border:1px solid var(--border-color,rgba(127,127,127,.22));background:var(--bg-input);color:var(--text-main,#eee);border-radius:7px;padding:5px 7px;font-size:11px}.logos-trace-notice{padding:10px 12px;border-radius:8px;background:rgba(127,127,127,.06);font-size:12px;opacity:.75}
.logos-trace-mobile-detail-head{display:none}
@media(max-width:820px){
.logos-trace-modal{padding:0;touch-action:auto}.logos-trace-window{border-radius:0}.logos-trace-nav{display:none}
.logos-trace-mobile-tabs{display:flex;gap:5px;width:100%;box-sizing:border-box;padding:7px 8px;border-bottom:1px solid var(--border-color,rgba(255,255,255,.08));overflow-x:auto;overflow-y:hidden;-webkit-overflow-scrolling:touch;touch-action:pan-x;flex:0 0 auto}.logos-trace-mobile-tabs.detail-active{display:none}
.logos-trace-mobile-tab{flex:0 0 auto;border:1px solid var(--border-color,rgba(255,255,255,.12));background:transparent;color:inherit;border-radius:7px;padding:6px 9px;font-size:11px;cursor:pointer}.logos-trace-mobile-tab.active{background:rgba(127,127,127,.12);font-weight:700}
.logos-trace-main{display:block;min-height:0;flex:1 1 0%;overflow:hidden;position:relative}.logos-trace-main .logos-trace-inspector{display:none}.logos-trace-mobile-list{display:block;width:100%;height:100%;overflow-y:auto;overflow-x:hidden;-webkit-overflow-scrolling:touch;touch-action:pan-y;overscroll-behavior:contain}.logos-trace-mobile-list .logos-trace-item{padding:10px 12px;border-radius:0;grid-template-columns:22px minmax(0,1fr) auto}.logos-trace-mobile-detail{position:absolute;inset:0;display:none;flex-direction:column;background:var(--bg-app);z-index:10;min-height:0}.logos-trace-mobile-detail.active{display:flex}.logos-trace-mobile-detail-head{display:flex;align-items:center;gap:8px;padding:8px 10px;border-bottom:1px solid var(--border-color,rgba(255,255,255,.1));flex:0 0 auto}.logos-trace-mobile-back{border:1px solid var(--border-color,rgba(255,255,255,.12));background:transparent;color:inherit;border-radius:7px;padding:6px 9px;cursor:pointer}.logos-trace-mobile-detail-title{min-width:0;font-size:12px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.logos-trace-mobile-detail-content{flex:1 1 0%;min-height:0;overflow-y:auto;overflow-x:hidden;-webkit-overflow-scrolling:touch;touch-action:pan-y;padding:0}.logos-trace-mobile-detail-content .logos-trace-inspector{display:block;overflow:visible;max-height:none}.logos-trace-mobile-detail-content .logos-trace-inspector-inner{padding:12px}.logos-trace-mobile-detail-content .logos-trace-pre{max-height:none;overflow:auto;white-space:pre;word-break:normal}.logos-trace-mobile-detail-content .logos-trace-json-wrap{overflow:visible}.logos-trace-mobile-detail-content .logos-trace-json-toolbar{position:sticky;top:0;background:var(--bg-app);padding:4px 0;z-index:2}.logos-trace-header{padding:9px}.logos-trace-waterfall{padding-left:8px;padding-right:8px}.logos-trace-timeline{min-width:460px}
}
@media(max-width:600px){.logos-trace-title{font-size:13px}.logos-trace-sub{max-width:120px}.logos-trace-btn{padding:6px 8px}.logos-trace-metrics{gap:5px;padding:7px}.logos-trace-metric{font-size:10px;padding:4px 6px}.logos-trace-waterfall-head{align-items:flex-start;flex-direction:column;gap:3px}.logos-trace-timeline{min-width:430px}.logos-trace-axis-labels{grid-template-columns:88px 1fr}.logos-trace-timeline-row{grid-template-columns:88px 1fr}.logos-trace-row-label{font-size:9px}.logos-trace-pre{font-size:10px;line-height:1.45}.logos-trace-inspector-title{font-size:16px}}
`;
    document.head.appendChild(s);
  }

  function buildItems(trace) {
    var items = [];
    (trace.events || []).forEach(function (ev, i) {
      var payload = ev.payload || {};
      items.push({
        kind: "event",
        index: i,
        id: "event-" + i,
        name: ev.type || "event",
        timestamp: getTimestamp(ev),
        correlation_id: payload.correlation_id || null,
        data: ev,
      });
    });
    (trace.tool_calls || []).forEach(function (t, i) {
      var st = Number(t.start_timestamp),
        en = Number(t.end_timestamp),
        span = Number.isFinite(st) && Number.isFinite(en) && en >= st;
      items.push({
        kind: "tool",
        index: i,
        id: "tool-" + i,
        name: t.name || "tool",
        timestamp: span ? en : getTimestamp(t),
        start: st,
        end: en,
        data: t,
      });
    });
    (trace.responses || []).forEach(function (r, i) {
      var st = Number(r.start_timestamp),
        en = Number(r.end_timestamp),
        ts = getTimestamp(r);
      if (!Number.isFinite(en)) en = ts;
      items.push({
        kind: "response",
        index: i,
        id: "response-" + i,
        name: "Responses API #" + (r.step || i + 1),
        timestamp: ts,
        start: Number.isFinite(st) ? st : null,
        end: Number.isFinite(en) ? en : null,
        correlation_id: r.correlation_id || null,
        data: r,
      });
    });
    var preApi = trace.timings && trace.timings.pre_api_pipeline;
    if (preApi) {
      var pst = Number(preApi.start_timestamp),
        pen = Number(preApi.end_timestamp);
      if (Number.isFinite(pst) && Number.isFinite(pen) && pen >= pst) {
        items.push({
          kind: "pipeline",
          index: -1,
          id: "pre-api-pipeline",
          name: "Pre-API Pipeline",
          timestamp: pen,
          start: pst,
          end: pen,
          data: preApi,
        });
      }
    }
    items.sort(function (a, b) {
      return (
        (a.timestamp == null ? Infinity : a.timestamp) -
        (b.timestamp == null ? Infinity : b.timestamp)
      );
    });
    var prev = Number(trace.created_at);
    if (!Number.isFinite(prev))
      prev = items.length && Number.isFinite(items[0].timestamp) ? items[0].timestamp : null;
    items
      .filter(function (x) {
        return x.kind === "response" && (!Number.isFinite(x.start) || !Number.isFinite(x.end));
      })
      .sort(function (a, b) {
        return a.index - b.index;
      })
      .forEach(function (x) {
        x.start = prev;
        if (Number.isFinite(x.end) && Number.isFinite(prev) && x.end >= prev) prev = x.end;
      });
    return items;
  }
  function duration(trace) {
    if (trace.timings && Number.isFinite(Number(trace.timings.total_duration_ms)))
      return Number(trace.timings.total_duration_ms);
    var st = Number(trace.created_at),
      en = null;
    (trace.events || []).forEach(function (e) {
      var t = getTimestamp(e);
      if (t !== null) en = en === null ? t : Math.max(en, t);
    });
    return Number.isFinite(st) && en !== null ? Math.max(0, (en - st) * 1000) : null;
  }
  function collectUsage(trace) {
    var r = { input: 0, output: 0, total: 0 };
    (trace.responses || []).forEach(function (x) {
      var u = x.raw && x.raw.usage;
      if (!u) return;
      r.input += Number(u.input_tokens || 0);
      r.output += Number(u.output_tokens || 0);
      r.total += Number(u.total_tokens || 0);
    });
    return r;
  }
  function renderMetricRow(trace) {
    var row = el("div", { className: "logos-trace-metrics" }),
      u = collectUsage(trace),
      m = [
        ["Duration", fmtMs(duration(trace))],
        ["Events", (trace.events || []).length],
        ["Responses", (trace.responses || []).length],
        ["Tools", (trace.tool_calls || []).length],
        ["Tokens", fmtNumber(u.total)],
        ["Cost", trace.billing && trace.billing.total_cost != null ? "≈ " + trace.billing.total_cost + " ₽" : "—"],
        ["Status", (trace.errors || []).length ? "⚠ " + trace.errors.length : "● completed"],
      ];
    m.forEach(function (x) {
      var c = el("div", { className: "logos-trace-metric" });
      c.appendChild(document.createTextNode(x[0] + ": "));
      c.appendChild(el("b", {}, String(x[1])));
      row.appendChild(c);
    });
    return row;
  }

  function renderWaterfall(trace, items) {
    var wrap = el("div", { className: "logos-trace-waterfall" }),
      head = el("div", { className: "logos-trace-waterfall-head" });
    head.appendChild(el("span", {}, "Waterfall"));
    head.appendChild(
      el(
        "span",
        { className: "logos-trace-waterfall-sub" },
        "pipeline = real interval · tools = real start/end · Responses = real request/response interval · events = instant",
      ),
    );
    wrap.appendChild(head);
    var timed = items.filter(function (x) {
      return Number.isFinite(x.timestamp) || (Number.isFinite(x.start) && Number.isFinite(x.end));
    });
    if (!timed.length) {
      wrap.appendChild(
        el("div", { className: "logos-trace-notice" }, "Trace не содержит временных событий."),
      );
      return wrap;
    }
    var times = [];
    timed.forEach(function (x) {
      if (Number.isFinite(x.start)) times.push(x.start);
      if (Number.isFinite(x.end)) times.push(x.end);
      if (Number.isFinite(x.timestamp)) times.push(x.timestamp);
    });
    if (Number.isFinite(Number(trace.created_at))) times.push(Number(trace.created_at));
    var min = Math.min.apply(Math, times),
      max = Math.max.apply(Math, times),
      span = Math.max(0.001, max - min),
      tl = el("div", { className: "logos-trace-timeline" }),
      axis = el("div", { className: "logos-trace-axis-labels" });
    axis.appendChild(el("div", {}, ""));
    var av = el("div", { className: "logos-trace-axis-values" });
    [0, 0.25, 0.5, 0.75, 1].forEach(function (p) {
      av.appendChild(el("span", {}, fmtMs(p * span * 1000)));
    });
    axis.appendChild(av);
    tl.appendChild(axis);
    var body = el("div", { className: "logos-trace-timeline-body" });
    [0, 0.25, 0.5, 0.75, 1].forEach(function (p) {
      var l = el("div", { className: "logos-trace-gridline" });
      l.style.left = "calc(108px + (100% - 108px) * " + p + ")";
      body.appendChild(l);
    });
    function row(label, it, st, en, inferred) {
      var r = el("div", { className: "logos-trace-timeline-row" });
      r.appendChild(el("div", { className: "logos-trace-row-label", title: label }, label));
      var tr = el("div", { className: "logos-trace-row-track" });
      if (Number.isFinite(st) && Number.isFinite(en) && en >= st) {
        var left = Math.max(0, Math.min(100, ((st - min) / span) * 100)),
          w = Math.max(0.9, Math.min(100 - left, ((en - st) / span) * 100)),
          b = el("div", {
            className: "logos-trace-bar " + it.kind + (inferred ? " inferred" : ""),
            title:
              it.name + " · " + fmtMs((en - st) * 1000) + (inferred ? " · start inferred" : ""),
          });
        b.style.left = left + "%";
        b.style.width = w + "%";
        b.onclick = function () {
          selectItem(it);
        };
        tr.appendChild(b);
      } else if (Number.isFinite(it.timestamp)) {
        var p = el("div", {
          className: "logos-trace-point " + it.kind,
          title: it.name + " · instant",
        });
        p.style.left = Math.max(0, Math.min(100, ((it.timestamp - min) / span) * 100)) + "%";
        p.onclick = function () {
          selectItem(it);
        };
        tr.appendChild(p);
      }
      r.appendChild(tr);
      body.appendChild(r);
    }
    timed
      .filter(function (x) {
        return x.kind === "pipeline";
      })
      .forEach(function (x) {
        row("⏱️ " + x.name, x, x.start, x.end, false);
      });
    timed
      .filter(function (x) {
        return x.kind === "tool";
      })
      .forEach(function (x) {
        row("🔧 " + x.name, x, x.start, x.end, false);
      });
    timed
      .filter(function (x) {
        return x.kind === "response";
      })
      .forEach(function (x) {
        row("🤖 " + x.name, x, x.start, x.end, false);
      });
    timed
      .filter(function (x) {
        return x.kind === "event";
      })
      .forEach(function (x) {
        row(eventIcon(x.data.type) + " " + x.name, x, null, null, false);
      });
    tl.appendChild(body);
    wrap.appendChild(tl);
    return wrap;
  }

  function eventIcon(type) {
    var icons = {
      request_initialized: "🚀",
      api_request_registered: "📤",
      api_response_received: "📡",
      pre_api_pipeline_completed: "⏱️",
      tool_executed: "⚙️",
      trace_finalized: "🏁",
      request_started: "▶️",
      request_completed: "✅",
      error: "❌",
      tool_call_started: "🔧",
      tool_call_finished: "🛠️",
    };
    return icons[type] || "•";
  }
  function icon(x) {
    return x.kind === "pipeline"
      ? "⏱️"
      : x.kind === "tool"
        ? "🔧"
        : x.kind === "response"
          ? "🤖"
          : eventIcon(x.data && x.data.type);
  }
  function meta(x) {
    return (x.kind === "tool" || x.kind === "response" || x.kind === "pipeline") &&
      Number.isFinite(x.start) &&
      Number.isFinite(x.end)
      ? fmtMs((x.end - x.start) * 1000)
      : "instant";
  }
  function makeItemButton(item) {
    var b = el("button", { type: "button", className: "logos-btn logos-trace-item" });
    b.appendChild(el("span", { className: "logos-trace-icon" }, icon(item)));
    var m = el("span", { className: "logos-trace-item-main" });
    m.appendChild(el("span", { className: "logos-trace-item-name" }, item.name));
    m.appendChild(el("span", { className: "logos-trace-item-meta" }, meta(item)));
    b.appendChild(m);
    b.appendChild(
      el(
        "span",
        { className: "logos-trace-item-time" },
        Number.isFinite(item.timestamp)
          ? new Date(item.timestamp * 1000).toLocaleTimeString("ru-RU")
          : "",
      ),
    );
    b.onclick = function () {
      selectItem(item);
    };
    return b;
  }
  function renderMobileTabs(items) {
    var nav = el("div", { className: "logos-trace-mobile-tabs" });
    [
      ["All", items],
      [
        "Tools",
        items.filter(function (x) {
          return x.kind === "tool";
        }),
      ],
      [
        "Responses",
        items.filter(function (x) {
          return x.kind === "response";
        }),
      ],
      [
        "Events",
        items.filter(function (x) {
          return x.kind === "event";
        }),
      ],
    ].forEach(function (pair, i) {
      var b = el(
        "button",
        { type: "button", className: "logos-btn logos-trace-mobile-tab" },
        pair[0],
      );
      if (i === 0) b.classList.add("active");
      b.onclick = function () {
        nav.querySelectorAll(".logos-trace-mobile-tab").forEach(function (x) {
          x.classList.remove("active");
        });
        b.classList.add("active");
        renderMobileList(pair[1]);
      };
      nav.appendChild(b);
    });
    return nav;
  }
  function renderMobileList(items) {
    if (!state.list) return;
    state.list.textContent = "";
    if (!items.length) {
      state.list.appendChild(
        el("div", { className: "logos-trace-notice" }, "Нет элементов в этом фильтре."),
      );
      return;
    }
    items.forEach(function (x) {
      var b = makeItemButton(x);
      x._mobileButton = b;
      state.list.appendChild(b);
    });
  }
  function renderSidebar(items) {
    var side = el("div", { className: "logos-trace-nav" });
    side.appendChild(el("div", { className: "logos-trace-section-title" }, "EVENTS / TRACE"));
    items.forEach(function (x) {
      var b = makeItemButton(x);
      x._button = b;
      side.appendChild(b);
    });
    return side;
  }
  function tabsFor(item) {
    if (item.kind === "response")
      return [
        "Overview",
        "Text",
        "Request",
        "Raw Response",
        "Output",
        "Usage",
        "Tools",
        "Reasoning",
        "Metadata",
      ];
    if (item.kind === "tool") return ["Overview", "Arguments", "Result", "Metadata", "Raw"];
    if (item.kind === "pipeline") return ["Overview", "Raw"];
    return ["Overview", "Payload", "Raw"];
  }
  function rawFor(item) {
    return item.kind === "response" ? item.data.raw || item.data : item.data;
  }
  function responseText(item) {
    var raw = item && item.data && item.data.raw;
    if (!raw || typeof raw !== "object") return "";
    if (typeof raw.output_text === "string") return raw.output_text;
    var out = Array.isArray(raw.output) ? raw.output : [];
    var parts = [];
    out.forEach(function (x) {
      if (!x || typeof x !== "object") return;
      if (typeof x.text === "string") parts.push(x.text);
      (Array.isArray(x.content) ? x.content : []).forEach(function (c) {
        if (c && typeof c.text === "string") parts.push(c.text);
        if (c && c.type === "output_text" && typeof c.text === "string") parts.push(c.text);
      });
    });
    return window.LogosWikifunctions.trim(parts.join("\n"));
  }

  function addJsonToolbar(content, rerender) {
    var bar = el("div", { className: "logos-trace-json-toolbar" });
    bar.appendChild(el("span", { className: "logos-trace-json-label" }, "JSON depth"));
    var sel = el("select", { className: "logos-trace-depth", title: "Глубина отображения JSON" });
    [
      [1, "1"],
      [2, "2"],
      [3, "3"],
      [4, "4"],
      [5, "5"],
      [6, "6"],
      [null, "Full"],
    ].forEach(function (x) {
      var o = el("option", { value: x[0] === null ? "full" : String(x[0]) }, x[1]);
      if ((state.jsonDepth === null && x[0] === null) || state.jsonDepth === x[0])
        o.selected = true;
      sel.appendChild(o);
    });
    sel.onchange = function () {
      state.jsonDepth = this.value === "full" ? null : Number(this.value);
      rerender();
    };
    bar.appendChild(sel);
    content.appendChild(bar);
  }

  function renderInspector(trace, item, tab, target) {
    var area = target || state.body.querySelector(".logos-trace-inspector");
    area.textContent = "";
    if (!item) return;
    var inner = el("div", { className: "logos-trace-inspector-inner" });
    inner.appendChild(el("div", { className: "logos-trace-inspector-title" }, item.name));
    inner.appendChild(
      el(
        "div",
        { className: "logos-trace-inspector-sub" },
        window.LogosWikifunctions.uppercase(item.kind) + (item.data.step ? " · step " + item.data.step : ""),
      ),
    );
    var tabs = el("div", { className: "logos-trace-tabs" }),
      selected = tab || tabsFor(item)[0];
    tabsFor(item).forEach(function (t) {
      var b = el("button", { type: "button", className: "logos-btn logos-trace-tab" }, t);
      if (t === selected) b.classList.add("active");
      b.onclick = function () {
        renderInspector(trace, item, t, target);
      };
      tabs.appendChild(b);
    });
    inner.appendChild(tabs);
    var content = document.createElement("div");
    function pre(value) {
      var wrap = el("div", { className: "logos-trace-json-wrap" });
      addJsonToolbar(wrap, function () {
        renderInspector(trace, item, selected, target);
      });
      wrap.appendChild(el("pre", { className: "logos-trace-pre" }, jsonText(value)));
      content.appendChild(wrap);
    }
    if (selected === "Overview") {
      var grid = el("div", { className: "logos-trace-grid" }),
        pairs = [];
      if (item.kind === "tool")
        pairs = [
          ["Name", item.data.name],
          ["Server", item.data.server || "Local Registry"],
          ["Duration", fmtMs(item.data.timing_ms)],
          ["Status", item.data.error ? "error" : "success"],
          ["Call ID", item.data.call_id || "—"],
          ["Step", item.data.step || "—"],
        ];
      else if (item.kind === "response")
        pairs = [
          ["Step", item.data.step || "—"],
          [
            "Timestamp",
            item.data.timestamp
              ? new Date(item.data.timestamp * 1000).toLocaleString("ru-RU")
              : "—",
          ],
          ["Model", (item.data.raw && item.data.raw.model) || "—"],
          ["Response ID", (item.data.raw && item.data.raw.id) || "—"],
          [
            "Output items",
            item.data.raw && Array.isArray(item.data.raw.output)
              ? item.data.raw.output.length
              : "—",
          ],
          [
            "Step interval",
            Number.isFinite(item.start) && Number.isFinite(item.end)
              ? fmtMs((item.end - item.start) * 1000)
              : "—",
          ],
        ];
      else if (item.kind === "pipeline")
        pairs = [
          [
            "Start",
            item.data.start_timestamp
              ? new Date(item.data.start_timestamp * 1000).toLocaleString("ru-RU")
              : "—",
          ],
          [
            "End",
            item.data.end_timestamp
              ? new Date(item.data.end_timestamp * 1000).toLocaleString("ru-RU")
              : "—",
          ],
          ["Duration", fmtMs(item.data.duration_ms)],
        ];
      else
        pairs = [
          ["Type", item.data.type || "event"],
          [
            "Timestamp",
            item.data.timestamp
              ? new Date(item.data.timestamp * 1000).toLocaleString("ru-RU")
              : "—",
          ],
        ];
      pairs.forEach(function (p) {
        var c = el("div", { className: "logos-trace-card" });
        c.appendChild(el("div", { className: "logos-trace-card-label" }, p[0]));
        c.appendChild(el("div", { className: "logos-trace-card-value" }, String(p[1])));
        grid.appendChild(c);
      });
      content.appendChild(grid);
      if (item.kind === "tool" && item.data.error)
        content.appendChild(
          el("div", { className: "logos-trace-notice" }, "⚠ " + String(item.data.error)),
        );
      if (item.kind === "event")
        pre(item.data.payload !== undefined ? item.data.payload : item.data);
    } else if (selected === "Text") {
      var responseBodyText = responseText(item);
      if (responseBodyText) {
        content.appendChild(
          el(
            "div",
            {
              style:
                "white-space:pre-wrap;word-break:break-word;font-size:14px;line-height:1.55;color:var(--text-main,#1c1c1e);background:var(--bg-sidebar,#fff);padding:12px;border:1px solid var(--border-color,#e5e5ea);border-radius:9px;",
            },
            responseBodyText,
          ),
        );
      } else {
        content.appendChild(
          el(
            "div",
            { className: "logos-trace-notice" },
            "Ответ с текстом в этом response не найден.",
          ),
        );
      }
    } else if (selected === "Request") {
      pre(
        item.kind === "response" ? item.data.request || trace.request || {} : trace.request || {},
      );
    } else if (selected === "Raw Response") {
      pre(rawFor(item));
    } else if (selected === "Payload") {
      pre(item.data.payload !== undefined ? item.data.payload : item.data);
    } else if (selected === "Raw") {
      pre(rawFor(item));
    } else if (selected === "Arguments") {
      pre(item.data.arguments || {});
    } else if (selected === "Result") {
      pre(
        item.data.error ? { error: item.data.error, result: item.data.result } : item.data.result,
      );
    } else if (selected === "Metadata") {
      pre(
        item.kind === "response"
          ? item.data.raw && item.data.raw.metadata
          : {
              call_id: item.data.call_id,
              server: item.data.server,
              step: item.data.step,
              start_timestamp: item.data.start_timestamp,
              end_timestamp: item.data.end_timestamp,
            },
      );
    } else if (selected === "Output") {
      pre((item.data.raw && item.data.raw.output) || []);
    } else if (selected === "Usage") {
      pre((item.data.raw && item.data.raw.usage) || {});
    } else if (selected === "Tools") {
      pre((item.data.raw && item.data.raw.tools) || trace.tool_calls || []);
    } else if (selected === "Reasoning") {
      pre((item.data.raw && item.data.raw.reasoning) || null);
    }
    inner.appendChild(content);
    if (item.correlation_id && state.items) {
      var related = state.items.filter(function (other) {
        return other.id !== item.id && other.correlation_id === item.correlation_id;
      });
      if (related.length) {
        var relatedWrap = el("div", {
          className: "logos-trace-related",
          style:
            "margin-top:12px;padding:10px;border:1px solid var(--border-color,rgba(127,127,127,.14));border-radius:8px;",
        });
        relatedWrap.appendChild(
          el(
            "div",
            { style: "font-size:11px;font-weight:700;opacity:.7;margin-bottom:6px;" },
            "Связанные события",
          ),
        );
        related.forEach(function (other) {
          var link = el(
            "button",
            { type: "button", className: "logos-trace-tab", title: "Перейти к связанному событию" },
            other.name,
          );
          link.onclick = function () {
            selectItem(other);
          };
          relatedWrap.appendChild(link);
        });
        inner.appendChild(relatedWrap);
      }
    }
    area.appendChild(inner);
  }

  function showMobileDetail(item) {
    if (!state.detail) return;
    state.listScrollTop = state.list ? state.list.scrollTop : 0;
    state.selected = item;
    state.detail.classList.add("active");
    state.nav.classList.add("detail-active");
    state.detailTitle.textContent = item.name;
    renderInspector(state.trace, item, null, state.detail.querySelector(".logos-trace-inspector"));
  }
  function hideMobileDetail() {
    if (!state.detail) return;
    state.detail.classList.remove("active");
    state.nav.classList.remove("detail-active");
    if (state.list)
      requestAnimationFrame(function () {
        state.list.scrollTop = state.listScrollTop;
      });
  }
  function selectItem(item) {
    state.selected = item;
    if (state.modal)
      state.modal.querySelectorAll(".logos-trace-item").forEach(function (b) {
        b.classList.remove("active");
      });
    if (item._button) item._button.classList.add("active");
    if (item._mobileButton) item._mobileButton.classList.add("active");
    if (window.matchMedia && window.matchMedia("(max-width:820px)").matches) {
      showMobileDetail(item);
      return;
    }
    renderInspector(state.trace, item);
  }
  function copyTrace() {
    var text = JSON.stringify(state.trace, null, 2);
    if (navigator.clipboard && navigator.clipboard.writeText)
      navigator.clipboard.writeText(text).catch(function () {
        fallbackCopy(text);
      });
    else fallbackCopy(text);
  }
  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
    } catch (_) {}
    ta.remove();
  }
  function close() {
    if (state.modal) {
      state.modal.remove();
      state.modal = null;
      state.trace = null;
      state.items = null;
      state.selected = null;
      state.body = null;
      state.nav = null;
      state.list = null;
      state.detail = null;
      state.detailTitle = null;
    }
    document.removeEventListener("keydown", onKey);
  }
  function onKey(e) {
    if (e.key === "Escape") close();
  }

  function open(input) {
    var trace = normalizeTrace(input);
    if (!trace) return;
    injectStyles();
    close();
    state.trace = trace;
    state.jsonDepth = 4;
    var items = buildItems(trace);
    state.items = items;
    var modal = el("div", { className: "logos-trace-modal" }),
      win = el("div", {
        className: "logos-trace-window",
        role: "dialog",
        "aria-modal": "true",
        "aria-label": "Execution Trace",
      });
    var header = el("div", { className: "logos-trace-header" });
    header.appendChild(el("span", {}, "⚡"));
    header.appendChild(el("div", { className: "logos-trace-title" }, "Execution Trace"));
    header.appendChild(
      el(
        "div",
        { className: "logos-trace-sub" },
        trace.trace_id ? String(trace.trace_id) : "local",
      ),
    );
    header.appendChild(el("div", { className: "logos-trace-spacer" }));
    var copy = el(
      "button",
      { type: "button", className: "logos-btn logos-trace-btn", title: "Copy raw JSON" },
      "Copy JSON",
    );
    copy.onclick = copyTrace;
    header.appendChild(copy);
    var cb = el("button", { type: "button", className: "logos-trace-btn", title: "Close" }, "×");
    cb.onclick = close;
    header.appendChild(cb);
    win.appendChild(header);
    win.appendChild(renderMetricRow(trace));
    win.appendChild(renderWaterfall(trace, items));
    state.nav = renderMobileTabs(items);
    win.appendChild(state.nav);
    var main = el("div", { className: "logos-trace-main" });
    state.body = main;
    state.list = el("div", { className: "logos-trace-mobile-list" });
    main.appendChild(state.list);
    renderMobileList(items);
    main.appendChild(renderSidebar(items));
    main.appendChild(el("div", { className: "logos-trace-inspector" }));
    state.detail = el("div", { className: "logos-trace-mobile-detail" });
    var dh = el("div", { className: "logos-trace-mobile-detail-head" }),
      back = el(
        "button",
        { type: "button", className: "logos-btn logos-trace-mobile-back" },
        "← Events",
      );
    back.onclick = hideMobileDetail;
    state.detailTitle = el("div", { className: "logos-trace-mobile-detail-title" }, "");
    dh.appendChild(back);
    dh.appendChild(state.detailTitle);
    state.detail.appendChild(dh);
    var dc = el("div", { className: "logos-trace-mobile-detail-content" });
    dc.appendChild(el("div", { className: "logos-trace-inspector" }));
    state.detail.appendChild(dc);
    main.appendChild(state.detail);
    win.appendChild(main);
    modal.appendChild(win);
    document.body.appendChild(modal);
    state.modal = modal;
    document.addEventListener("keydown", onKey);
  }
  window.openTraceViewer = open;
  window.dispatchEvent(new CustomEvent("logos:trace-viewer-ready"));
})();
