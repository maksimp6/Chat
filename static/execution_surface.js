(function () {
  "use strict";

  // Read-only projection of a sanitized ExecutionTrace. Only whitelisted metadata
  // is copied out: never prompts, payloads, tool arguments or credentials.
  var SCHEMA = "logos.execution_surface.v1";

  var STATE_LABELS = {
    working: "⏳ Выполняется",
    blocked: "⛔ Заблокировано",
    done: "✅ Готово",
    partial: "◐ Частично",
    error: "❌ Ошибка",
    timeout: "⌛ Таймаут",
    cancelled: "🚫 Отменено",
  };

  var STAGE_MARKS = { done: "✓", active: "●", pending: "○", partial: "◐", error: "✕" };

  var BLOCKING_EVENTS = {
    provider_quota_denied: "Квота провайдера",
    mcp_runtime_access_denied: "Доступ к инструменту",
    approval_required: "Требуется подтверждение",
  };

  function asTrace(input) {
    if (typeof input === "string") {
      try {
        input = JSON.parse(input);
      } catch (_) {
        return null;
      }
    }
    return input && typeof input === "object" && !Array.isArray(input) ? input : null;
  }

  function list(value) {
    return Array.isArray(value) ? value : [];
  }

  function finite(value) {
    if (value === null || value === undefined || value === "") return null;
    var number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function text(value, limit) {
    if (typeof value !== "string") return "";
    var max = limit || 120;
    return value.length > max ? value.slice(0, max) + "…" : value;
  }

  // Raw error types such as provider.http_500 are shown as readable text; other errors by their own message.
  function errorLabel(error) {
    var http = /^provider\.http_(\d{3})$/.exec(text(error && error.type, 64));
    if (http) return "Ошибка провайдера, HTTP " + http[1];
    return text(error && error.error, 160) || text(error && error.message, 160) || "Ошибка";
  }

  // Errors of a turn that still completed: each message once, with how many times it was met.
  function warningsOf(errors) {
    var counts = {};
    var order = [];
    errors.forEach(function (error) {
      var label = errorLabel(error);
      if (!Object.prototype.hasOwnProperty.call(counts, label)) {
        counts[label] = 0;
        order.push(label);
      }
      counts[label] += 1;
    });
    return order.map(function (label) {
      return { label: label, count: counts[label] };
    });
  }

  // The server's own final record of the turn (chat/mcp_routes.py): "completed" or "failed", or "" without one.
  function terminalStatus(events) {
    for (var i = events.length - 1; i >= 0; i--) {
      if (events[i] && events[i].type === "provider_terminal_state") {
        var status = events[i].payload && events[i].payload.invocation_status;
        return typeof status === "string" ? status : "";
      }
    }
    return "";
  }

  function responseStatus(response) {
    var raw = response && response.raw;
    return raw && typeof raw.status === "string" ? raw.status.toLowerCase() : "";
  }

  function isTimeout(error) {
    var kind = (text(error && error.type) + " " + text(error && error.source)).toLowerCase();
    return kind.indexOf("timeout") >= 0;
  }

  function findBlocker(events, errors) {
    for (var i = events.length - 1; i >= 0; i--) {
      var type = events[i] && events[i].type;
      if (BLOCKING_EVENTS[type]) return { source: type, label: BLOCKING_EVENTS[type] };
    }
    var last = errors[errors.length - 1];
    if (last) {
      return {
        source: text(last.source) || "error",
        label: errorLabel(last),
      };
    }
    return null;
  }

  function deriveState(trace, responses, errors, blocker, terminal) {
    var statuses = responses.map(responseStatus);
    var finished = finite((trace.timings || {}).total_duration_ms) !== null;
    var blocked = Boolean(blocker && BLOCKING_EVENTS[blocker.source]);
    if (statuses.indexOf("cancelled") >= 0) return "cancelled";
    // A turn the server recorded as completed is done: errors met on the way are its warnings, not its outcome.
    // A block on the way (quota, tool access, approval) is still named.
    if (terminal === "completed" && !blocked) return "done";
    if (errors.some(isTimeout)) return "timeout";
    if (blocked) return "blocked";
    if (terminal === "failed") return "error";
    var completed = statuses.indexOf("completed") >= 0;
    if (errors.length) return completed ? "partial" : "error";
    if (statuses.indexOf("incomplete") >= 0) return "partial";
    if (statuses.indexOf("failed") >= 0) return "error";
    if (finished) return "done";
    return "working";
  }

  function usage(trace, responses) {
    var billing = trace.billing || {};
    if (list(billing.items).length) {
      return {
        status: "measured",
        input: finite(billing.input_tokens) || 0,
        output: finite(billing.output_tokens) || 0,
        cached: finite(billing.cached_input_tokens) || 0,
        total: finite(billing.total_tokens) || 0,
      };
    }
    var found = false;
    var result = { status: "unknown", input: 0, output: 0, cached: 0, total: 0 };
    responses.forEach(function (response) {
      var raw = response && response.raw && response.raw.usage;
      if (!raw || typeof raw !== "object") return;
      found = true;
      result.input += finite(raw.input_tokens) || 0;
      result.output += finite(raw.output_tokens) || 0;
      result.total += finite(raw.total_tokens) || 0;
      var details = raw.input_tokens_details || {};
      result.cached += finite(details.cached_tokens) || 0;
    });
    if (found) result.status = "measured";
    return result;
  }

  function cost(trace) {
    var billing = trace.billing || {};
    if (!list(billing.items).length || finite(billing.total_cost) === null) {
      return { status: "unknown", amount: null, currency: text(billing.currency) || null };
    }
    return {
      status: billing.cost_status === "calculated" ? "calculated" : "partial",
      amount: finite(billing.total_cost),
      currency: text(billing.currency) || null,
    };
  }

  // A stage with a failed call is not shown as done: the marks must agree with the reason line.
  function toolFailed(call) {
    var result = call && call.result;
    return Boolean(call && call.error) || Boolean(result && typeof result === "object" && (result.error || result.success === false));
  }

  function finishStatus(state) {
    if (state === "partial") return "partial";
    if (state === "error" || state === "timeout" || state === "cancelled") return "error";
    return "done";
  }

  function stages(trace, responses, tools, state) {
    var requested = list(trace.api_requests).length > 0 || responses.length > 0;
    var finished = ["done", "partial", "error", "timeout", "cancelled"].indexOf(state) >= 0;
    var result = [
      { id: "request", label: "Запрос", status: requested ? "done" : "pending" },
      {
        id: "model",
        label: "Модель",
        status: responses.length ? "done" : requested ? "active" : "pending",
      },
    ];
    if (tools.length) {
      result.push({ id: "tools", label: "Инструменты", status: tools.some(toolFailed) ? "error" : "done" });
    }
    result.push({ id: "finish", label: "Итог", status: finished ? finishStatus(state) : "pending" });
    return result;
  }

  function latestEvent(events) {
    for (var i = events.length - 1; i >= 0; i--) {
      var event = events[i];
      if (event && typeof event.type === "string") {
        return { type: event.type.slice(0, 64), timestamp: finite(event.timestamp) };
      }
    }
    return null;
  }

  // WIKIFUNCTIONS-CANDIDATE WF-17: https://www.wikifunctions.org/wiki/Z19202.
  // Заменить ядро stable dedup строк; адаптер сохраняет извлечение raw.model, text(..., 80),
  // пропуск пустых значений и порядок первого появления; не менять UTF-16 усечение text().
  function models(responses) {
    var seen = new Set();
    responses.forEach(function (response) {
      var model = text(response && response.raw && response.raw.model, 80);
      if (model) seen.add(model);
    });
    return Array.from(seen);
  }

  function project(input) {
    var trace = asTrace(input);
    if (!trace) return null;
    var responses = list(trace.responses);
    var tools = list(trace.tool_calls);
    var errors = list(trace.errors);
    var events = list(trace.events);
    var blocker = findBlocker(events, errors);
    var state = deriveState(trace, responses, errors, blocker, terminalStatus(events));
    return {
      schema: SCHEMA,
      trace_id: text(trace.trace_id, 64) || null,
      state: state,
      stages: stages(trace, responses, tools, state),
      latest_event: latestEvent(events),
      blocker: state === "done" ? null : blocker,
      warnings: state === "done" ? warningsOf(errors) : [],
      models: models(responses),
      tool_calls: tools.length,
      model_calls: responses.length,
      duration_ms: finite((trace.timings || {}).total_duration_ms),
      usage: usage(trace, responses),
      cost: cost(trace),
    };
  }

  function formatDuration(ms) {
    return ms < 1000 ? Math.round(ms) + " мс" : (ms / 1000).toFixed(1) + " с";
  }

  // A count with its Russian word form, digits grouped: 1 вызов, 4 инструмента, 1 960 065 токенов.
  function countText(n, one, few, many) {
    var mod10 = n % 10;
    var mod100 = n % 100;
    var word = mod10 === 1 && mod100 !== 11 ? one : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14) ? few : many;
    return n.toLocaleString("ru-RU") + " " + word;
  }

  function summaryParts(surface) {
    var parts = [STATE_LABELS[surface.state]];
    if (surface.model_calls) parts.push(countText(surface.model_calls, "вызов модели", "вызова модели", "вызовов модели"));
    if (surface.tool_calls) parts.push(countText(surface.tool_calls, "инструмент", "инструмента", "инструментов"));
    if (surface.duration_ms !== null) parts.push(formatDuration(surface.duration_ms));
    if (surface.usage.status === "measured") {
      var tokens = countText(surface.usage.total, "токен", "токена", "токенов");
      if (surface.usage.cached) tokens += " (кэш " + surface.usage.cached.toLocaleString("ru-RU") + ")";
      parts.push(tokens);
    } else {
      parts.push("токены: неизвестно");
    }
    if (surface.cost.status === "unknown") {
      parts.push("стоимость: неизвестно");
    } else {
      var amount = surface.cost.amount.toFixed(4) + " " + (surface.cost.currency || "");
      parts.push(
        (surface.cost.status === "partial" ? "≥ " : "") + amount.trim() + " (расчёт по тарифу)",
      );
    }
    return parts;
  }

  function render(surface) {
    var doc = document;
    var root = doc.createElement("div");
    root.className = "execution-surface";
    root.dataset.state = surface.state;
    root.setAttribute("role", "status");

    var line = doc.createElement("div");
    line.className = "execution-surface-summary";
    line.textContent = summaryParts(surface).join(" · ");
    root.appendChild(line);

    var stagesEl = doc.createElement("ol");
    stagesEl.className = "execution-surface-stages";
    surface.stages.forEach(function (stage) {
      var item = doc.createElement("li");
      item.dataset.status = stage.status;
      // The state is shown as a mark in the text, not only in data-status, so it is visible without CSS.
      item.textContent = STAGE_MARKS[stage.status] + " " + stage.label;
      stagesEl.appendChild(item);
    });
    root.appendChild(stagesEl);

    if (surface.blocker) {
      var blocker = doc.createElement("div");
      blocker.className = "execution-surface-blocker";
      blocker.textContent = "Причина: " + surface.blocker.label;
      root.appendChild(blocker);
    }
    if (surface.warnings && surface.warnings.length) {
      var warnings = doc.createElement("div");
      warnings.className = "execution-surface-warnings";
      warnings.textContent =
        "⚠️ Предупреждения: " +
        surface.warnings
          .map(function (warning) {
            return warning.label + (warning.count > 1 ? " (×" + warning.count + ")" : "");
          })
          .join("; ");
      root.appendChild(warnings);
    }
    return root;
  }

  window.LogosExecutionSurface = Object.freeze({
    SCHEMA: SCHEMA,
    project: project,
    render: render,
    summary: function (surface) {
      return summaryParts(surface).join(" · ");
    },
  });
})();
