/* Logos trace viewer: one turn's execution trace (трассировка) — how the turn ended and what it cost, a timeline,
   the list of what happened (model requests, tools, errors, events) and the details of each part. The whole viewer
   scrolls, so nothing is cut off; on a phone the list and the details are two screens. */
(function () {
  "use strict";

  var PHONE_QUERY = "(max-width: 820px)";
  var JSON_DEPTHS = [1, 2, 3, 4, 5, 6, null];
  var MARK_MS = 1600;

  var EVENT_ICONS = {
    request_initialized: "🚀",
    turn_limits: "⚙️",
    tools_grouped_by_category: "🧰",
    tool_choice_grouped: "🧰",
    provider_key_selected: "🔑",
    ultra_profile_assembled: "⚙️",
    api_request_manifest: "📝",
    model_view: "👁",
    pre_api_pipeline_completed: "⏱️",
    api_request_registered: "📤",
    api_request_sent: "📤",
    api_response_received: "📡",
    api_request_completed: "📥",
    provider_output_observed: "📄",
    tool_group_call: "🔧",
    tool_executed: "🛠️",
    tool_call_started: "🔧",
    tool_call_finished: "🛠️",
    provider_terminal_state: "🏁",
    trace_finalized: "🏁",
    request_started: "▶️",
    request_completed: "✅",
    error_occurred: "⚠️",
    error: "⚠️",
  };

  var RESPONSE_STATUS = {
    completed: "готово",
    failed: "сбой",
    incomplete: "не завершён",
    in_progress: "выполняется",
    queued: "в очереди",
    cancelled: "отменён",
  };

  var state = {
    trace: null,
    model: null,
    selected: null,
    modal: null,
    content: null,
    inspector: null,
    detail: null,
    detailBody: null,
    detailTitle: null,
    detailBack: null,
    listScrollTop: 0,
    jsonDepth: 4,
    returnFocus: null,
    media: null,
  };

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function button(className, text, onClick, title) {
    var node = el("button", "logos-btn " + className, text);
    node.type = "button";
    if (title) node.title = title;
    if (onClick) node.addEventListener("click", onClick);
    return node;
  }

  // Number(null) is 0, so a missing value is rejected before the conversion.
  function finite(value) {
    if (value === null || value === undefined || value === "") return null;
    var number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function objectsOf(list) {
    return Array.isArray(list)
      ? list.filter(function (x) {
          return x !== null && typeof x === "object";
        })
      : [];
  }

  function objectOf(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function plural(n, one, few, many) {
    var mod10 = n % 10;
    var mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
    return many;
  }

  function fmtNumber(value) {
    var n = finite(value);
    return n === null ? "—" : n.toLocaleString("ru-RU");
  }

  function count(n, one, few, many) {
    return fmtNumber(n) + " " + plural(Math.abs(Math.round(n)), one, few, many);
  }

  function fmtMs(value) {
    var ms = finite(value);
    if (ms === null) return "—";
    return ms < 1000 ? Math.round(ms) + " мс" : (ms / 1000).toFixed(ms < 10000 ? 2 : 1) + " с";
  }

  // The time since the start of the turn, for the timeline and the list.
  function fmtOffset(seconds) {
    var s = finite(seconds);
    return s === null ? "" : "+" + fmtMs(Math.max(0, s) * 1000);
  }

  function fmtClock(seconds) {
    var s = finite(seconds);
    return s === null ? "—" : new Date(s * 1000).toLocaleString("ru-RU");
  }

  function sinceStart(seconds) {
    return state.model && state.model.start !== null && finite(seconds) !== null
      ? fmtOffset(seconds - state.model.start)
      : "";
  }

  function getTimestamp(obj) {
    if (!obj || typeof obj !== "object") return null;
    var candidates = [obj.timestamp, obj.end_timestamp, obj.created_at];
    for (var i = 0; i < candidates.length; i++) {
      var n = finite(candidates[i]);
      if (n !== null) return n;
      if (typeof candidates[i] === "string") {
        var parsed = Date.parse(candidates[i]);
        if (Number.isFinite(parsed)) return parsed / 1000;
      }
    }
    return null;
  }

  function normalizeTrace(input) {
    var trace = input;
    if (typeof input === "string") {
      try {
        trace = JSON.parse(input);
      } catch (_) {
        return null;
      }
    }
    return trace && typeof trace === "object" && !Array.isArray(trace) ? trace : null;
  }

  // Tool arguments and results often arrive as JSON text; they are shown as JSON when they parse.
  function parsedJson(value) {
    if (typeof value !== "string") return value;
    var text = value.trim();
    if (!text || (text[0] !== "{" && text[0] !== "[")) return value;
    try {
      return JSON.parse(text);
    } catch (_) {
      return value;
    }
  }

  function limitJson(value, depth, level) {
    if (depth === null || value === null || typeof value !== "object") return value;
    if (level >= depth) {
      return Array.isArray(value)
        ? "… " + count(value.length, "элемент", "элемента", "элементов")
        : "… " + count(Object.keys(value).length, "ключ", "ключа", "ключей");
    }
    if (Array.isArray(value)) {
      return value.map(function (v) {
        return limitJson(v, depth, level + 1);
      });
    }
    var out = {};
    Object.keys(value).forEach(function (key) {
      out[key] = limitJson(value[key], depth, level + 1);
    });
    return out;
  }

  function jsonText(value, depth) {
    if (value === undefined) return "нет данных";
    if (typeof value === "string") return value;
    try {
      return JSON.stringify(limitJson(value, depth, 0), null, 2);
    } catch (_) {
      return String(value);
    }
  }

  function fullJson(value) {
    if (typeof value === "string") return value;
    try {
      return JSON.stringify(value, null, 2);
    } catch (_) {
      return String(value);
    }
  }

  function errorMessage(error) {
    var exception = objectOf(error && error.python_exception);
    var text = error && (error.error || error.message || exception.exception_message || error.type);
    return typeof text === "string" && text ? text : "Ошибка без описания";
  }

  // Responses with no start or end of their own take them from the request record or the completion event of
  // the same step, as older traces store them; failing that, the step starts where the previous one ended.
  function responseInterval(response, trace, events) {
    var start = finite(response.start_timestamp);
    var end = finite(response.end_timestamp);
    if (start !== null && end !== null && end >= start) return { start: start, end: end };
    var step = finite(response.step);
    if (step !== null) {
      var requests = objectsOf(trace.api_requests);
      for (var i = requests.length - 1; i >= 0; i--) {
        if (finite(requests[i].step) === step) {
          start = finite(requests[i].timestamp);
          break;
        }
      }
      for (var j = events.length - 1; j >= 0; j--) {
        var payload = objectOf(events[j].payload);
        if (events[j].type !== "api_request_completed" || finite(payload.step) !== step) continue;
        if (start === null) start = finite(payload.start_timestamp);
        end = finite(payload.end_timestamp);
        break;
      }
    }
    if (end === null) end = finite(response.timestamp);
    return start !== null && end !== null && end >= start ? { start: start, end: end } : null;
  }

  function toolFailed(call) {
    var result = call.result;
    return (
      Boolean(call.error) ||
      Boolean(result && typeof result === "object" && (result.error || result.success === false))
    );
  }

  function buildModel(trace) {
    var events = objectsOf(trace.events);
    var times = [];
    function note(value) {
      var n = finite(value);
      if (n !== null) times.push(n);
    }
    note(trace.created_at);

    var model = {
      overview: { kind: "overview", id: "overview", timestamp: null },
      pipeline: null,
      errors: [],
      responses: [],
      tools: [],
      events: [],
      start: null,
      end: null,
    };

    var pre = objectOf(trace.timings && trace.timings.pre_api_pipeline);
    var preStart = finite(pre.start_timestamp);
    var preEnd = finite(pre.end_timestamp);
    if (preStart !== null && preEnd !== null && preEnd >= preStart) {
      note(preStart);
      note(preEnd);
      model.pipeline = { kind: "pipeline", id: "pipeline", start: preStart, end: preEnd, timestamp: preStart, data: pre };
    }

    objectsOf(trace.errors).forEach(function (error, index) {
      var ts = finite(error.timestamp);
      note(ts);
      model.errors.push({ kind: "error", id: "error-" + index, name: errorMessage(error), timestamp: ts, data: error });
    });

    var previousEnd = finite(trace.created_at);
    objectsOf(trace.responses).forEach(function (response, index) {
      var step = finite(response.step);
      var span = responseInterval(response, trace, events);
      var inferred = false;
      if (!span) {
        var end = finite(response.end_timestamp);
        if (end === null) end = finite(response.timestamp);
        if (end !== null && previousEnd !== null && end >= previousEnd) {
          span = { start: previousEnd, end: end };
          inferred = true;
        }
      }
      if (span) {
        note(span.start);
        note(span.end);
        previousEnd = span.end;
      }
      var raw = objectOf(response.raw);
      model.responses.push({
        kind: "response",
        id: "response-" + index,
        step: step === null ? index + 1 : step,
        start: span ? span.start : null,
        end: span ? span.end : null,
        inferred: inferred,
        timestamp: span ? span.start : finite(response.timestamp),
        status: typeof raw.status === "string" ? raw.status : "",
        correlationId: response.correlation_id || null,
        data: response,
      });
    });

    objectsOf(trace.tool_calls).forEach(function (call, index) {
      var start = finite(call.start_timestamp);
      var end = finite(call.end_timestamp);
      var span = start !== null && end !== null && end >= start;
      var ts = span ? start : getTimestamp(call);
      if (span) {
        note(start);
        note(end);
      } else {
        note(ts);
      }
      model.tools.push({
        kind: "tool",
        id: "tool-" + index,
        name: String(call.name || call.tool_name || "инструмент"),
        start: span ? start : null,
        end: span ? end : null,
        timestamp: ts,
        failed: toolFailed(call),
        callId: call.call_id || null,
        data: call,
      });
    });

    events.forEach(function (event, index) {
      var ts = getTimestamp(event);
      note(ts);
      var payload = objectOf(event.payload);
      var type = typeof event.type === "string" && event.type ? event.type : "событие";
      model.events.push({
        kind: "event",
        id: "event-" + index,
        name: type,
        timestamp: ts,
        isError: /error|fail/i.test(type),
        correlationId: payload.correlation_id || null,
        callId: payload.call_id || null,
        data: event,
      });
    });

    if (times.length) {
      model.start = Math.min.apply(Math, times);
      model.end = Math.max.apply(Math, times);
    }
    return model;
  }

  function usageOf(model) {
    var usage = { found: false, input: 0, output: 0, total: 0, cached: 0, reasoning: 0 };
    model.responses.forEach(function (item) {
      var u = objectOf(objectOf(item.data.raw).usage);
      if (!Object.keys(u).length) return;
      usage.found = true;
      usage.input += finite(u.input_tokens) || 0;
      usage.output += finite(u.output_tokens) || 0;
      usage.total += finite(u.total_tokens) || 0;
      usage.cached += finite(objectOf(u.input_tokens_details).cached_tokens) || 0;
      usage.reasoning += finite(objectOf(u.output_tokens_details).reasoning_tokens) || 0;
    });
    return usage;
  }

  function durationMs(trace, model) {
    var total = finite(objectOf(trace.timings).total_duration_ms);
    if (total !== null) return total;
    return model.start !== null && model.end !== null ? (model.end - model.start) * 1000 : null;
  }

  // How the turn ended: the server's own final record decides; older traces fall back to the last model answer.
  function outcome(trace, model) {
    var terminal = null;
    model.events.forEach(function (item) {
      if (item.name === "provider_terminal_state") terminal = objectOf(item.data.payload);
    });
    var status = terminal ? terminal.invocation_status : null;
    if (status === "completed") return { mark: "✓", text: "Ход завершён", kind: "ok" };
    if (status === "failed") return { mark: "✕", text: "Ход завершился сбоем", kind: "error" };
    var last = model.responses.length ? model.responses[model.responses.length - 1].status : "";
    if (last === "completed") return { mark: "✓", text: "Ход завершён", kind: "ok" };
    if (last === "failed") return { mark: "✕", text: "Ход завершился сбоем", kind: "error" };
    if (last === "incomplete") return { mark: "◐", text: "Ответ модели не завершён", kind: "warn" };
    if (model.errors.length) return { mark: "✕", text: "Ход завершился сбоем", kind: "error" };
    if (finite(objectOf(trace.timings).total_duration_ms) !== null) {
      return { mark: "•", text: "Ход завершён без ответа модели", kind: "warn" };
    }
    return { mark: "…", text: "Ход не завершён", kind: "warn" };
  }

  function costText(trace) {
    var billing = objectOf(trace.billing);
    var amount = finite(billing.total_cost);
    if (amount === null) return null;
    var currency = !billing.currency || billing.currency === "RUB" ? "₽" : String(billing.currency);
    return "≈ " + amount.toFixed(2) + " " + currency;
  }

  function modelName(trace, model) {
    for (var i = model.responses.length - 1; i >= 0; i--) {
      var name = objectOf(model.responses[i].data.raw).model;
      if (typeof name === "string" && name) return name;
    }
    var requests = objectsOf(trace.api_requests);
    for (var j = requests.length - 1; j >= 0; j--) {
      var requested = objectOf(requests[j].payload).model;
      if (typeof requested === "string" && requested) return requested;
    }
    var asked = objectOf(trace.request).model;
    return typeof asked === "string" ? asked : "";
  }

  function statusText(status) {
    return RESPONSE_STATUS[status] || status || "";
  }

  function iconOf(item) {
    if (item.kind === "overview") return "📋";
    if (item.kind === "pipeline") return "⏱️";
    if (item.kind === "error") return "⚠️";
    if (item.kind === "response") return "🤖";
    if (item.kind === "tool") return item.failed ? "❌" : "🔧";
    return EVENT_ICONS[item.name] || "•";
  }

  function labelOf(item) {
    if (item.kind === "overview") return "Обзор хода";
    if (item.kind === "pipeline") return "Подготовка запроса";
    if (item.kind === "response") return "Модель · шаг " + item.step;
    return item.name;
  }

  function metaOf(item) {
    var parts = [];
    if (item.kind === "overview") {
      parts.push(outcome(state.trace, state.model).text);
    } else if (item.kind === "error") {
      if (item.data.source) parts.push(String(item.data.source));
      parts.push(sinceStart(item.timestamp));
    } else if (item.kind === "event") {
      parts.push(sinceStart(item.timestamp));
    } else {
      parts.push(sinceStart(item.start !== null ? item.start : item.timestamp));
      if (item.start !== null && item.end !== null) parts.push(fmtMs((item.end - item.start) * 1000));
      if (item.kind === "response") parts.push(statusText(item.status));
      if (item.kind === "tool") parts.push(item.failed ? "ошибка" : "готово");
    }
    return parts.filter(Boolean).join(" · ");
  }

  function isFailure(item) {
    return (
      item.kind === "error" ||
      (item.kind === "tool" && item.failed) ||
      (item.kind === "event" && item.isError) ||
      (item.kind === "response" && item.status !== "" && item.status !== "completed")
    );
  }

  // The result of a copy shows on the button that asked for it.
  function copyButton(className, label, title, valueOf) {
    var timer = null;
    var node = button(className, label, null, title);
    node.addEventListener("click", function () {
      window.LogosClipboard.copy(valueOf()).then(
        function () {
          node.textContent = "Скопировано";
        },
        function () {
          node.textContent = "Не скопировано";
        },
      ).then(function () {
        clearTimeout(timer);
        timer = setTimeout(function () {
          node.textContent = label;
        }, MARK_MS);
      });
    });
    return node;
  }

  function injectStyles() {
    if (document.getElementById("logos-trace-viewer-style")) return;
    var style = document.createElement("style");
    style.id = "logos-trace-viewer-style";
    style.textContent = `
.logos-trace-modal{position:fixed;inset:0;z-index:200000;display:flex;justify-content:center;align-items:stretch;padding:16px;box-sizing:border-box;background:rgba(0,0,0,.55);overscroll-behavior:contain}
.logos-trace-modal *{box-sizing:border-box}
.logos-trace-window{position:relative;display:flex;flex-direction:column;width:min(1400px,100%);height:100%;overflow:hidden;background:var(--bg-app);color:var(--text-main);border:1px solid var(--border-color);border-radius:14px;box-shadow:0 24px 80px rgba(0,0,0,.4);font-size:14px;line-height:1.45}
.logos-trace-header{flex:0 0 auto;display:flex;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid var(--border-color)}
.logos-trace-heading{flex:1 1 auto;min-width:0}
.logos-trace-title{font-size:16px;font-weight:700}
.logos-trace-sub{font:11px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--text-secondary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.logos-trace-actions{flex:0 1 auto;display:flex;gap:6px;min-width:0;overflow-x:auto}
.logos-trace-btn{flex:0 0 auto;min-height:36px;padding:0 12px;border:1px solid var(--border-color);border-radius:8px;background:var(--btn-bg);color:var(--text-main);font:inherit;font-size:13px;line-height:1;white-space:nowrap;cursor:pointer}
.logos-trace-btn:hover{background:var(--bg-hover)}
.logos-trace-btn:disabled{opacity:.6;cursor:default}
.logos-trace-close{min-width:36px;padding:0;font-size:16px}
.logos-trace-content{flex:1 1 0%;min-height:0;display:flex;flex-direction:column}
.logos-trace-metrics{flex:0 0 auto;display:flex;flex-wrap:wrap;gap:6px;padding:8px 12px;border-bottom:1px solid var(--border-color)}
.logos-trace-chip{display:inline-flex;align-items:center;min-height:26px;padding:3px 9px;border:0;border-radius:13px;background:var(--chip-bg);color:var(--text-main);font:inherit;font-size:12px;line-height:1.2;white-space:nowrap}
.logos-trace-chip-ok{color:var(--success-text)}
.logos-trace-chip-error{color:var(--danger-text)}
button.logos-trace-chip{cursor:pointer}
.logos-trace-timeline-box{flex:0 0 auto;border-bottom:1px solid var(--border-color)}
.logos-trace-timeline-summary{padding:6px 12px;font-size:12px;font-weight:600;color:var(--text-secondary);cursor:pointer}
.logos-trace-timeline-scroll{max-height:32vh;overflow:auto;padding:0 12px 8px}
.logos-trace-timeline{min-width:480px}
.logos-trace-row{display:grid;grid-template-columns:150px minmax(0,1fr);align-items:center;min-height:22px}
.logos-trace-row-label{padding-right:8px;font-size:11px;color:var(--text-secondary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.logos-trace-axis-track{position:relative;height:16px;font:10px/16px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--text-secondary)}
.logos-trace-axis-tick{position:absolute;top:0;transform:translateX(-50%);white-space:nowrap}
.logos-trace-axis-tick:first-child{transform:none}
.logos-trace-axis-tick:last-child{transform:translateX(-100%)}
.logos-trace-track{position:relative;height:22px;border-bottom:1px solid var(--border-color)}
.logos-trace-grid{position:absolute;top:0;bottom:0;width:1px;background:var(--border-color)}
.logos-trace-bar{position:absolute;top:5px;height:12px;min-width:3px;border-radius:4px;background:var(--accent);cursor:pointer}
.logos-trace-bar.response{background:#8f7cf0}
.logos-trace-bar.pipeline{background:var(--text-secondary)}
.logos-trace-bar.failed{background:var(--danger)}
.logos-trace-bar.inferred{opacity:.55}
.logos-trace-dot{position:absolute;top:6px;width:10px;height:10px;margin-left:-5px;border-radius:50%;background:var(--accent);cursor:pointer}
.logos-trace-dot.failed{background:var(--danger)}
.logos-trace-tick{position:absolute;top:4px;width:2px;height:14px;margin-left:-1px;background:var(--text-secondary);cursor:pointer}
.logos-trace-tick.error{top:6px;width:10px;height:10px;margin-left:-5px;border-radius:50%;background:var(--danger)}
.logos-trace-main{flex:1 1 0%;min-height:0;display:grid;grid-template-columns:minmax(240px,320px) minmax(0,1fr)}
.logos-trace-nav{min-height:0;overflow:auto;padding:6px;border-right:1px solid var(--border-color)}
.logos-trace-section-title{padding:10px 8px 4px;font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:var(--text-secondary)}
.logos-trace-item{display:flex;align-items:center;gap:8px;width:100%;min-height:44px;padding:6px 8px;border:0;border-radius:8px;background:transparent;color:var(--text-main);font:inherit;text-align:left;cursor:pointer}
.logos-trace-item:hover{background:var(--bg-hover)}
.logos-trace-item.active{background:var(--accent-soft)}
.logos-trace-item.is-failure .logos-trace-item-name{color:var(--danger-text)}
.logos-trace-item-icon{flex:0 0 22px;text-align:center}
.logos-trace-item-main{flex:1 1 auto;min-width:0}
.logos-trace-item-name{display:block;font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.logos-trace-item-meta{display:block;margin-top:1px;font-size:11px;color:var(--text-secondary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.logos-trace-inspector{min-height:0;min-width:0;overflow:auto}
.logos-trace-inspector-inner{padding:14px 16px 24px}
.logos-trace-inspector-title{margin:0 0 2px;font-size:17px;font-weight:700;overflow-wrap:anywhere}
.logos-trace-inspector-sub{margin-bottom:10px;font-size:12px;color:var(--text-secondary)}
.logos-trace-tabs{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
.logos-trace-tab{min-height:34px;padding:0 10px;border:1px solid var(--border-color);border-radius:8px;background:transparent;color:var(--text-main);font:inherit;font-size:12px;cursor:pointer}
.logos-trace-tab.active{background:var(--accent-soft);border-color:var(--accent);font-weight:600}
.logos-trace-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-bottom:12px}
.logos-trace-card{min-width:0;padding:8px 10px;border:1px solid var(--border-color);border-radius:8px;background:var(--bg-sidebar)}
.logos-trace-card-label{font-size:11px;color:var(--text-secondary)}
.logos-trace-card-value{margin-top:2px;font-size:13px;font-weight:600;overflow-wrap:anywhere}
.logos-trace-block-title{margin:16px 0 6px;font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:var(--text-secondary)}
.logos-trace-notice{margin:0 0 10px;padding:9px 11px;border-radius:8px;background:var(--chip-bg);font-size:13px;overflow-wrap:anywhere}
.logos-trace-notice.error{color:var(--danger-text)}
.logos-trace-text{margin:0 0 10px;padding:10px 12px;max-height:60vh;overflow:auto;border:1px solid var(--border-color);border-radius:8px;background:var(--bg-sidebar);font-size:14px;line-height:1.55;white-space:pre-wrap;overflow-wrap:anywhere}
.logos-trace-error-card{display:block;width:100%;margin:0 0 8px;padding:9px 11px;border:1px solid var(--danger);border-radius:8px;background:transparent;color:var(--text-main);font:inherit;text-align:left}
button.logos-trace-error-card{cursor:pointer}
.logos-trace-error-message{font-size:13px;font-weight:600;color:var(--danger-text);overflow-wrap:anywhere}
.logos-trace-error-meta{margin-top:3px;font-size:11px;color:var(--text-secondary);overflow-wrap:anywhere}
.logos-trace-json{min-width:0;margin:0 0 12px}
.logos-trace-json-bar{display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:12px;color:var(--text-secondary)}
.logos-trace-depth{margin-left:4px;min-height:32px;padding:0 6px;border:1px solid var(--border-color);border-radius:8px;background:var(--bg-input);color:var(--text-main);font:inherit;font-size:12px}
.logos-trace-pre{margin:0;padding:10px 12px;max-height:60vh;overflow:auto;border:1px solid var(--code-border);border-radius:8px;background:var(--code-bg);color:var(--text-main);font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre;tab-size:2}
.logos-trace-related{display:flex;flex-wrap:wrap;gap:6px}
.logos-trace-detail{display:none}
@media (max-width:820px){
.logos-trace-modal{padding:0}
.logos-trace-window{border:0;border-radius:0}
.logos-trace-header{flex-wrap:wrap;gap:6px;padding:8px 10px}
.logos-trace-close{order:2}
.logos-trace-actions{order:3;flex:1 1 100%}
.logos-trace-content{display:block;overflow-y:auto;-webkit-overflow-scrolling:touch;overscroll-behavior:contain}
.logos-trace-timeline-scroll{max-height:none;overflow-x:auto;overflow-y:visible}
.logos-trace-timeline{min-width:440px}
.logos-trace-row{grid-template-columns:104px minmax(0,1fr)}
.logos-trace-main{display:block}
.logos-trace-nav{overflow:visible;padding:4px 6px 16px;border-right:0}
.logos-trace-main>.logos-trace-inspector{display:none}
.logos-trace-detail{position:absolute;inset:0;z-index:2;display:none;flex-direction:column;background:var(--bg-app)}
.logos-trace-detail.active{display:flex}
.logos-trace-detail-head{flex:0 0 auto;display:flex;align-items:center;gap:8px;padding:8px 10px;border-bottom:1px solid var(--border-color)}
.logos-trace-detail-title{min-width:0;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.logos-trace-detail-body{flex:1 1 0%;min-height:0;overflow-y:auto;-webkit-overflow-scrolling:touch;overscroll-behavior:contain}
.logos-trace-inspector-inner{padding:12px 12px 24px}
.logos-trace-pre,.logos-trace-text{max-height:none}
}
`;
    document.head.appendChild(style);
  }

  function renderHeader(trace) {
    var header = el("div", "logos-trace-header");
    var heading = el("div", "logos-trace-heading");
    heading.appendChild(el("div", "logos-trace-title", "Трассировка"));
    var id = trace.trace_id ? String(trace.trace_id) : "";
    var sub = el("div", "logos-trace-sub", id || "без идентификатора");
    if (id) sub.title = id;
    heading.appendChild(sub);
    header.appendChild(heading);
    // trace_download.js adds its download and upload buttons to this row.
    var actions = el("div", "logos-trace-actions");
    actions.appendChild(
      copyButton("logos-trace-btn logos-trace-copy", "Копировать JSON", "Скопировать всю трассировку в JSON", function () {
        return fullJson(trace);
      }),
    );
    header.appendChild(actions);
    var close = button("logos-trace-btn logos-trace-close", "✕", closeViewer, "Закрыть");
    close.setAttribute("aria-label", "Закрыть трассировку");
    header.appendChild(close);
    return header;
  }

  function renderMetrics(trace, model) {
    var row = el("div", "logos-trace-metrics");
    var result = outcome(trace, model);
    row.appendChild(el("span", "logos-trace-chip logos-trace-chip-" + result.kind, result.mark + " " + result.text));
    var duration = durationMs(trace, model);
    if (duration !== null) row.appendChild(el("span", "logos-trace-chip", "⏱️ " + fmtMs(duration)));
    row.appendChild(
      el("span", "logos-trace-chip", count(model.responses.length, "запрос к модели", "запроса к модели", "запросов к модели")),
    );
    row.appendChild(el("span", "logos-trace-chip", count(model.tools.length, "инструмент", "инструмента", "инструментов")));
    row.appendChild(el("span", "logos-trace-chip", count(model.events.length, "событие", "события", "событий")));
    var usage = usageOf(model);
    if (usage.found) row.appendChild(el("span", "logos-trace-chip", count(usage.total, "токен", "токена", "токенов")));
    var cost = costText(trace);
    if (cost) row.appendChild(el("span", "logos-trace-chip", cost));
    if (model.errors.length) {
      row.appendChild(
        button(
          "logos-trace-chip logos-trace-chip-error",
          "⚠️ " + count(model.errors.length, "ошибка", "ошибки", "ошибок"),
          function () {
            selectItem(model.errors[0]);
          },
          "Показать первую ошибку",
        ),
      );
    }
    return row;
  }

  function renderTimeline(model) {
    var box = el("details", "logos-trace-timeline-box");
    box.open = true;
    box.appendChild(el("summary", "logos-trace-timeline-summary", "Шкала времени"));
    var scroll = el("div", "logos-trace-timeline-scroll");
    box.appendChild(scroll);
    if (model.start === null || model.end === null) {
      scroll.appendChild(el("div", "logos-trace-notice", "В трассировке нет отметок времени."));
      return box;
    }
    var span = Math.max(0.001, model.end - model.start);
    function pct(t) {
      return Math.max(0, Math.min(100, ((t - model.start) / span) * 100));
    }
    function track() {
      var node = el("div", "logos-trace-track");
      [25, 50, 75].forEach(function (p) {
        var line = el("span", "logos-trace-grid");
        line.style.left = p + "%";
        node.appendChild(line);
      });
      return node;
    }
    function mark(className, item, title, left, width) {
      var node = el("div", className);
      node.title = title;
      node.style.left = left + "%";
      if (width !== undefined) node.style.width = width + "%";
      node.addEventListener("click", function () {
        selectItem(item);
      });
      return node;
    }

    var timeline = el("div", "logos-trace-timeline");
    var axis = el("div", "logos-trace-row");
    axis.appendChild(el("div", "logos-trace-row-label"));
    var axisTrack = el("div", "logos-trace-axis-track");
    [0, 0.25, 0.5, 0.75, 1].forEach(function (p) {
      var tick = el("span", "logos-trace-axis-tick", fmtMs(p * span * 1000));
      tick.style.left = p * 100 + "%";
      axisTrack.appendChild(tick);
    });
    axis.appendChild(axisTrack);
    timeline.appendChild(axis);

    function spanRow(item) {
      var label = labelOf(item);
      var row = el("div", "logos-trace-row");
      var name = el("div", "logos-trace-row-label", label);
      name.title = label;
      row.appendChild(name);
      var lane = track();
      var kind = item.kind + (isFailure(item) ? " failed" : "");
      if (item.start !== null && item.end !== null) {
        var left = pct(item.start);
        lane.appendChild(
          mark(
            "logos-trace-bar " + kind + (item.inferred ? " inferred" : ""),
            item,
            label + " · " + fmtMs((item.end - item.start) * 1000) + (item.inferred ? " · начало вычислено" : ""),
            left,
            Math.max(0.6, pct(item.end) - left),
          ),
        );
      } else if (item.timestamp !== null) {
        lane.appendChild(mark("logos-trace-dot " + kind, item, label + " · " + sinceStart(item.timestamp), pct(item.timestamp)));
      }
      row.appendChild(lane);
      timeline.appendChild(row);
    }

    if (model.pipeline) spanRow(model.pipeline);
    // Model requests and tools in the order they ran.
    model.responses
      .concat(model.tools)
      .filter(function (item) {
        return item.timestamp !== null;
      })
      .sort(function (a, b) {
        return a.timestamp - b.timestamp;
      })
      .forEach(spanRow);

    // Every instant event on one lane; errors stand out.
    var events = el("div", "logos-trace-row");
    events.appendChild(el("div", "logos-trace-row-label", "События"));
    var eventLane = track();
    model.events.forEach(function (item) {
      if (item.timestamp === null) return;
      eventLane.appendChild(
        mark("logos-trace-tick" + (item.isError ? " error" : ""), item, item.name + " · " + sinceStart(item.timestamp), pct(item.timestamp)),
      );
    });
    events.appendChild(eventLane);
    timeline.appendChild(events);
    scroll.appendChild(timeline);
    return box;
  }

  function itemButton(item) {
    var node = button("logos-trace-item" + (isFailure(item) ? " is-failure" : ""), null, function () {
      selectItem(item);
    });
    node.appendChild(el("span", "logos-trace-item-icon", iconOf(item)));
    var main = el("span", "logos-trace-item-main");
    main.appendChild(el("span", "logos-trace-item-name", labelOf(item)));
    var meta = metaOf(item);
    if (meta) main.appendChild(el("span", "logos-trace-item-meta", meta));
    node.appendChild(main);
    item.button = node;
    return node;
  }

  function renderNav(model) {
    var nav = el("div", "logos-trace-nav");
    nav.setAttribute("aria-label", "Состав хода");
    nav.appendChild(itemButton(model.overview));
    if (model.pipeline) nav.appendChild(itemButton(model.pipeline));
    [
      ["Ошибки", model.errors],
      ["Запросы к модели", model.responses],
      ["Инструменты", model.tools],
      ["События", model.events],
    ].forEach(function (section) {
      if (!section[1].length) return;
      nav.appendChild(el("div", "logos-trace-section-title", section[0] + " · " + section[1].length));
      section[1].forEach(function (item) {
        nav.appendChild(itemButton(item));
      });
    });
    return nav;
  }

  function cards(content, pairs) {
    var grid = el("div", "logos-trace-cards");
    pairs.forEach(function (pair) {
      if (pair[1] === null || pair[1] === undefined || pair[1] === "") return;
      var card = el("div", "logos-trace-card");
      card.appendChild(el("div", "logos-trace-card-label", pair[0]));
      card.appendChild(el("div", "logos-trace-card-value", pair[1]));
      grid.appendChild(card);
    });
    if (grid.children.length) content.appendChild(grid);
  }

  function notice(content, text, isError) {
    content.appendChild(el("div", "logos-trace-notice" + (isError ? " error" : ""), text));
  }

  function blockTitle(content, text) {
    content.appendChild(el("div", "logos-trace-block-title", text));
  }

  function jsonBlock(content, value, rerender) {
    var wrap = el("div", "logos-trace-json");
    var bar = el("div", "logos-trace-json-bar");
    var label = el("label", "", "Глубина");
    var select = el("select", "logos-trace-depth");
    JSON_DEPTHS.forEach(function (depth) {
      var option = el("option", "", depth === null ? "вся" : String(depth));
      option.value = depth === null ? "all" : String(depth);
      option.selected = depth === state.jsonDepth;
      select.appendChild(option);
    });
    select.addEventListener("change", function () {
      state.jsonDepth = select.value === "all" ? null : Number(select.value);
      rerender();
    });
    label.appendChild(select);
    bar.appendChild(label);
    bar.appendChild(
      copyButton("logos-trace-btn", "Копировать", "Скопировать этот JSON целиком", function () {
        return fullJson(value);
      }),
    );
    wrap.appendChild(bar);
    wrap.appendChild(el("pre", "logos-trace-pre", jsonText(value, state.jsonDepth)));
    content.appendChild(wrap);
  }

  function errorCard(item, clickable) {
    var card = clickable
      ? button("logos-trace-error-card", null, function () {
          selectItem(item);
        })
      : el("div", "logos-trace-error-card");
    card.appendChild(el("div", "logos-trace-error-message", item.name));
    var error = item.data;
    var meta = [
      error.source ? "источник: " + error.source : "",
      error.type ? "тип: " + error.type : "",
      finite(error.step) !== null ? "шаг " + error.step : "",
      sinceStart(item.timestamp),
    ]
      .filter(Boolean)
      .join(" · ");
    if (meta) card.appendChild(el("div", "logos-trace-error-meta", meta));
    return card;
  }

  // A Python exception kept with an error: its type and message, and the traceback with locals as JSON.
  function pythonException(content, error, rerender) {
    var exception = error.python_exception;
    if (!exception || typeof exception !== "object") return;
    blockTitle(content, "Исключение Python");
    var card = el("div", "logos-trace-error-card");
    card.appendChild(
      el(
        "div",
        "logos-trace-error-message",
        (exception.exception_type || "Exception") + ": " + (exception.exception_message || errorMessage(error)),
      ),
    );
    var frames = Array.isArray(exception.frames) ? exception.frames.length : 0;
    card.appendChild(el("div", "logos-trace-error-meta", count(frames, "кадр", "кадра", "кадров") + " стека"));
    content.appendChild(card);
    jsonBlock(content, exception, rerender);
  }

  function responseText(raw) {
    if (typeof raw.output_text === "string" && raw.output_text) return raw.output_text;
    var parts = [];
    objectsOf(raw.output).forEach(function (out) {
      if (out.type === "reasoning" || out.type === "function_call") return;
      if (typeof out.text === "string") parts.push(out.text);
      objectsOf(out.content).forEach(function (part) {
        if (typeof part.text === "string") parts.push(part.text);
      });
    });
    return parts.join("\n").trim();
  }

  function reasoningText(raw) {
    var parts = [];
    objectsOf(raw.output).forEach(function (out) {
      if (out.type !== "reasoning") return;
      objectsOf(out.summary)
        .concat(objectsOf(out.content))
        .forEach(function (part) {
          if (typeof part.text === "string" && part.text) parts.push(part.text);
        });
    });
    return parts.join("\n\n").trim();
  }

  // The tool calls a model answer asked for: its function_call items name them by call_id.
  function stepCalls(item) {
    var ids = {};
    objectsOf(objectOf(item.data.raw).output).forEach(function (out) {
      if (out.type === "function_call" && out.call_id) ids[out.call_id] = true;
    });
    return state.model.tools.filter(function (tool) {
      var step = finite(tool.data.step);
      return (tool.callId && ids[tool.callId]) || (step !== null && step === item.step);
    });
  }

  function toolGroup(item) {
    var group = "";
    state.model.events.forEach(function (event) {
      var payload = objectOf(event.data.payload);
      if (event.name === "tool_group_call" && item.callId && payload.call_id === item.callId && payload.group) {
        group = String(payload.group);
      }
    });
    return group;
  }

  function apiRequestPayload(step) {
    var requests = objectsOf(state.trace.api_requests);
    for (var i = requests.length - 1; i >= 0; i--) {
      if (finite(requests[i].step) === step) return requests[i].payload;
    }
    return undefined;
  }

  function relatedOf(item) {
    if (item.kind === "overview" || item.kind === "pipeline") return [];
    var model = state.model;
    var related = [];
    function linked(other) {
      return (
        other !== item &&
        ((item.correlationId && other.correlationId === item.correlationId) || (item.callId && other.callId === item.callId))
      );
    }
    model.responses.concat(model.tools, model.events).forEach(function (other) {
      if (linked(other)) related.push(other);
    });
    return related;
  }

  function tabsOf(item) {
    if (item.kind === "overview") return ["Обзор", "Вопрос и параметры", "Трассировка целиком"];
    if (item.kind === "response") {
      return ["Обзор", "Текст ответа", "Рассуждения", "Инструменты", "Токены", "Вывод", "Запрос", "Ответ целиком"];
    }
    if (item.kind === "tool") return ["Обзор", "Аргументы", "Результат", "Целиком"];
    return ["Обзор", "Целиком"];
  }

  function subtitleOf(item) {
    if (item.kind === "overview") return modelName(state.trace, state.model);
    if (item.kind === "pipeline") return "Сборка запроса до первого обращения к модели";
    if (item.kind === "response") return "Запрос к модели и её ответ" + (item.status ? " · " + statusText(item.status) : "");
    if (item.kind === "tool") return "Вызов инструмента · " + (item.failed ? "ошибка" : "готово");
    if (item.kind === "error") return "Ошибка, записанная в ходе";
    return "Событие трассировки";
  }

  function renderOverviewTab(content) {
    var trace = state.trace;
    var model = state.model;
    var usage = usageOf(model);
    var result = outcome(trace, model);
    cards(content, [
      ["Итог", result.mark + " " + result.text],
      ["Длительность", fmtMs(durationMs(trace, model))],
      ["Запросов к модели", fmtNumber(model.responses.length)],
      ["Инструментов", fmtNumber(model.tools.length)],
      ["Событий", fmtNumber(model.events.length)],
      ["Ошибок", fmtNumber(model.errors.length)],
      ["Токенов всего", usage.found ? fmtNumber(usage.total) : null],
      ["На входе", usage.found ? fmtNumber(usage.input) : null],
      ["Из кэша", usage.found ? fmtNumber(usage.cached) : null],
      ["На выходе", usage.found ? fmtNumber(usage.output) : null],
      ["Рассуждения", usage.found ? fmtNumber(usage.reasoning) : null],
      ["Стоимость", costText(trace)],
      ["Начало", model.start !== null ? fmtClock(model.start) : null],
      ["Модель", modelName(trace, model)],
    ]);
    if (model.errors.length) {
      blockTitle(content, "Ошибки · " + model.errors.length);
      model.errors.forEach(function (item) {
        content.appendChild(errorCard(item, true));
      });
    }
    var question = objectOf(trace.request).message;
    if (typeof question === "string" && question) {
      blockTitle(content, "Вопрос");
      content.appendChild(el("div", "logos-trace-text", question));
    }
    if (!model.responses.length && !model.tools.length && !model.events.length) {
      notice(content, "В трассировке нет ни запросов к модели, ни событий.");
    }
  }

  function renderResponseTab(item, tab, content, rerender) {
    var raw = objectOf(item.data.raw);
    var usage = objectOf(raw.usage);
    if (tab === "Обзор") {
      cards(content, [
        ["Шаг", String(item.step)],
        ["Статус", statusText(item.status) || "—"],
        [
          "Длительность",
          item.start !== null && item.end !== null
            ? fmtMs((item.end - item.start) * 1000) + (item.inferred ? " (начало вычислено)" : "")
            : fmtMs(item.data.timing_ms),
        ],
        ["Начало", item.start !== null ? sinceStart(item.start) : null],
        ["Модель", raw.model],
        ["ID ответа", raw.id || item.data.response_id],
        ["Элементов вывода", Array.isArray(raw.output) ? fmtNumber(raw.output.length) : null],
        ["Токенов", finite(usage.total_tokens) !== null ? fmtNumber(usage.total_tokens) : null],
        ["На входе", finite(usage.input_tokens) !== null ? fmtNumber(usage.input_tokens) : null],
        ["На выходе", finite(usage.output_tokens) !== null ? fmtNumber(usage.output_tokens) : null],
      ]);
      if (raw.incomplete_details) notice(content, "Ответ не завершён: " + fullJson(raw.incomplete_details), true);
      if (raw.error) {
        var providerError = objectOf(raw.error);
        notice(content, "Ошибка провайдера: " + (providerError.message || fullJson(raw.error)), true);
      }
      var calls = stepCalls(item);
      if (calls.length) {
        blockTitle(content, "Вызванные инструменты · " + calls.length);
        content.appendChild(relatedButtons(calls));
      }
    } else if (tab === "Текст ответа") {
      var text = responseText(raw);
      if (text) content.appendChild(el("div", "logos-trace-text", text));
      else notice(content, stepCalls(item).length ? "Текста нет: в этом шаге модель вызвала инструменты." : "В этом ответе нет текста.");
    } else if (tab === "Рассуждения") {
      var reasoning = reasoningText(raw);
      if (reasoning) content.appendChild(el("div", "logos-trace-text", reasoning));
      else notice(content, "Модель не вернула текст рассуждений.");
      if (raw.reasoning) {
        blockTitle(content, "Настройки рассуждений");
        jsonBlock(content, raw.reasoning, rerender);
      }
    } else if (tab === "Инструменты") {
      var stepToolCalls = stepCalls(item);
      blockTitle(content, "Вызванные в этом шаге · " + stepToolCalls.length);
      if (stepToolCalls.length) content.appendChild(relatedButtons(stepToolCalls));
      else notice(content, "В этом шаге инструменты не вызывались.");
      blockTitle(content, "Объявленные инструменты (определения, не вызовы)");
      if (raw.tools) jsonBlock(content, raw.tools, rerender);
      else notice(content, "Ответ не перечисляет объявленные инструменты.");
    } else if (tab === "Токены") {
      jsonBlock(content, raw.usage || {}, rerender);
    } else if (tab === "Вывод") {
      jsonBlock(content, raw.output || [], rerender);
    } else if (tab === "Запрос") {
      var request = item.data.request || apiRequestPayload(item.step);
      if (request) jsonBlock(content, request, rerender);
      else notice(content, "Тело запроса в трассировке не сохранено.");
    } else {
      jsonBlock(content, item.data.raw || item.data, rerender);
    }
  }

  function renderToolTab(item, tab, content, rerender) {
    var call = item.data;
    if (tab === "Обзор") {
      cards(content, [
        ["Инструмент", item.name],
        ["Группа", toolGroup(item)],
        ["Итог", item.failed ? "✕ ошибка" : "✓ готово"],
        ["Длительность", item.start !== null && item.end !== null ? fmtMs((item.end - item.start) * 1000) : fmtMs(call.timing_ms)],
        ["Начало", sinceStart(item.start !== null ? item.start : item.timestamp)],
        ["Процессор", finite(call.cpu_ms) !== null ? fmtMs(call.cpu_ms) : null],
        ["Сервер", call.server],
        ["ID вызова", item.callId],
      ]);
      if (call.error) notice(content, "Ошибка: " + (typeof call.error === "string" ? call.error : fullJson(call.error)), true);
    } else if (tab === "Аргументы") {
      jsonBlock(content, parsedJson(call.arguments !== undefined ? call.arguments : {}), rerender);
    } else if (tab === "Результат") {
      if (call.error) jsonBlock(content, { error: call.error, result: parsedJson(call.result) }, rerender);
      else if (call.result === undefined) notice(content, "Результат в трассировке не сохранён.");
      else jsonBlock(content, parsedJson(call.result), rerender);
    } else {
      jsonBlock(content, call, rerender);
    }
  }

  function renderOtherTab(item, tab, content, rerender) {
    if (tab !== "Обзор") {
      jsonBlock(content, item.data, rerender);
      return;
    }
    if (item.kind === "pipeline") {
      cards(content, [
        ["Начало", sinceStart(item.start)],
        ["Конец", sinceStart(item.end)],
        ["Длительность", fmtMs((item.end - item.start) * 1000)],
      ]);
      return;
    }
    if (item.kind === "error") {
      content.appendChild(errorCard(item, false));
      cards(content, [["Время", item.timestamp !== null ? fmtClock(item.timestamp) : null]]);
      pythonException(content, item.data, rerender);
      return;
    }
    cards(content, [
      ["Тип", item.name],
      ["От начала хода", sinceStart(item.timestamp)],
      ["Время", item.timestamp !== null ? fmtClock(item.timestamp) : null],
    ]);
    if (item.data.payload !== undefined) {
      blockTitle(content, "Данные события");
      jsonBlock(content, item.data.payload, rerender);
    }
  }

  function relatedButtons(items) {
    var row = el("div", "logos-trace-related");
    items.forEach(function (other) {
      row.appendChild(
        button("logos-trace-tab", iconOf(other) + " " + labelOf(other), function () {
          selectItem(other);
        }, metaOf(other)),
      );
    });
    return row;
  }

  function renderInspector(item, target, tab) {
    target.textContent = "";
    var tabs = tabsOf(item);
    var selected = tab && tabs.indexOf(tab) >= 0 ? tab : tabs[0];
    function rerender() {
      var scrollTop = target.scrollTop;
      renderInspector(item, target, selected);
      target.scrollTop = scrollTop;
    }
    var inner = el("div", "logos-trace-inspector-inner");
    inner.appendChild(el("div", "logos-trace-inspector-title", labelOf(item)));
    var sub = subtitleOf(item);
    if (sub) inner.appendChild(el("div", "logos-trace-inspector-sub", sub));
    var tabBar = el("div", "logos-trace-tabs");
    tabBar.setAttribute("role", "tablist");
    tabs.forEach(function (name) {
      var node = button("logos-trace-tab" + (name === selected ? " active" : ""), name, function () {
        renderInspector(item, target, name);
      });
      node.setAttribute("role", "tab");
      node.setAttribute("aria-selected", name === selected ? "true" : "false");
      tabBar.appendChild(node);
    });
    inner.appendChild(tabBar);
    var content = el("div", "logos-trace-tab-content");
    if (item.kind === "overview") {
      if (selected === "Обзор") renderOverviewTab(content);
      else if (selected === "Вопрос и параметры") {
        if (state.trace.request) jsonBlock(content, state.trace.request, rerender);
        else notice(content, "Вопрос и параметры в трассировке не сохранены.");
      } else jsonBlock(content, state.trace, rerender);
    } else if (item.kind === "response") {
      renderResponseTab(item, selected, content, rerender);
    } else if (item.kind === "tool") {
      renderToolTab(item, selected, content, rerender);
    } else {
      renderOtherTab(item, selected, content, rerender);
    }
    inner.appendChild(content);
    var related = relatedOf(item);
    if (related.length) {
      blockTitle(inner, "Связанные записи · " + related.length);
      inner.appendChild(relatedButtons(related));
    }
    target.appendChild(inner);
    target.scrollTop = 0;
  }

  function isPhone() {
    return Boolean(window.matchMedia && window.matchMedia(PHONE_QUERY).matches);
  }

  function selectItem(item) {
    if (!item || !state.modal) return;
    state.selected = item;
    state.modal.querySelectorAll(".logos-trace-item.active").forEach(function (node) {
      node.classList.remove("active");
      node.removeAttribute("aria-current");
    });
    if (item.button) {
      item.button.classList.add("active");
      item.button.setAttribute("aria-current", "true");
    }
    if (isPhone()) {
      showDetail(item);
      return;
    }
    renderInspector(item, state.inspector);
    if (item.button) item.button.scrollIntoView({ block: "nearest" });
  }

  function showDetail(item) {
    if (!state.detail.classList.contains("active")) state.listScrollTop = state.content.scrollTop;
    state.detailTitle.textContent = labelOf(item);
    state.detail.classList.add("active");
    renderInspector(item, state.detailBody);
    state.detailBack.focus({ preventScroll: true });
  }

  function hideDetail() {
    if (!state.detail || !state.detail.classList.contains("active")) return;
    state.detail.classList.remove("active");
    state.content.scrollTop = state.listScrollTop;
    if (state.selected && state.selected.button) state.selected.button.focus({ preventScroll: true });
  }

  // Turning the phone or resizing the window moves the details between the side panel and their own screen.
  function onMediaChange() {
    if (!state.modal || !state.selected) return;
    if (isPhone()) return;
    state.detail.classList.remove("active");
    renderInspector(state.selected, state.inspector);
  }

  function onKey(event) {
    if (event.key !== "Escape") return;
    if (state.detail && state.detail.classList.contains("active")) hideDetail();
    else closeViewer();
  }

  function closeViewer() {
    var focus = state.returnFocus;
    if (state.modal) state.modal.remove();
    document.removeEventListener("keydown", onKey);
    if (state.media) state.media.removeEventListener("change", onMediaChange);
    state.trace = null;
    state.model = null;
    state.selected = null;
    state.modal = null;
    state.content = null;
    state.inspector = null;
    state.detail = null;
    state.detailBody = null;
    state.detailTitle = null;
    state.detailBack = null;
    state.returnFocus = null;
    state.media = null;
    if (focus && focus.isConnected && typeof focus.focus === "function") focus.focus({ preventScroll: true });
  }

  function open(input) {
    var trace = normalizeTrace(input);
    if (!trace) return;
    injectStyles();
    closeViewer();
    state.trace = trace;
    state.jsonDepth = 4;
    state.model = buildModel(trace);
    state.returnFocus = document.activeElement;

    var modal = el("div", "logos-trace-modal");
    var win = el("div", "logos-trace-window");
    win.setAttribute("role", "dialog");
    win.setAttribute("aria-modal", "true");
    win.setAttribute("aria-label", "Трассировка");
    win.appendChild(renderHeader(trace));

    var content = el("div", "logos-trace-content");
    content.appendChild(renderMetrics(trace, state.model));
    content.appendChild(renderTimeline(state.model));
    var main = el("div", "logos-trace-main");
    main.appendChild(renderNav(state.model));
    state.inspector = el("div", "logos-trace-inspector");
    main.appendChild(state.inspector);
    content.appendChild(main);
    win.appendChild(content);
    state.content = content;

    var detail = el("div", "logos-trace-detail");
    var head = el("div", "logos-trace-detail-head");
    state.detailBack = button("logos-trace-btn logos-trace-back", "← Назад", hideDetail, "Вернуться к списку");
    head.appendChild(state.detailBack);
    state.detailTitle = el("div", "logos-trace-detail-title");
    head.appendChild(state.detailTitle);
    detail.appendChild(head);
    state.detailBody = el("div", "logos-trace-detail-body");
    detail.appendChild(state.detailBody);
    win.appendChild(detail);
    state.detail = detail;

    modal.appendChild(win);
    modal.addEventListener("click", function (event) {
      if (event.target === modal) closeViewer();
    });
    document.body.appendChild(modal);
    state.modal = modal;
    document.addEventListener("keydown", onKey);
    if (window.matchMedia) {
      state.media = window.matchMedia(PHONE_QUERY);
      state.media.addEventListener("change", onMediaChange);
    }
    // A wide screen opens on the overview; a phone opens on the list.
    if (!isPhone()) selectItem(state.model.overview);
    var close = modal.querySelector(".logos-trace-close");
    if (close) close.focus({ preventScroll: true });
  }

  window.openTraceViewer = open;
  window.dispatchEvent(new CustomEvent("logos:trace-viewer-ready"));
})();
