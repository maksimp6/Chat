(function () {
  "use strict";

  // Read-only projection of a sanitized ExecutionTrace. Only whitelisted metadata
  // is copied out: never prompts, payloads, tool arguments or credentials.
  var SCHEMA = "alice.execution_surface.v1";

  var STATE_LABELS = {
    working: "⏳ Выполняется",
    blocked: "⛔ Заблокировано",
    done: "✅ Готово",
    partial: "◐ Частично",
    error: "❌ Ошибка",
    timeout: "⌛ Таймаут",
    cancelled: "🚫 Отменено",
  };

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
    return typeof value === "string" ? value.slice(0, limit || 120) : "";
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
        label: text(last.type) || "Ошибка",
      };
    }
    return null;
  }

  function deriveState(trace, responses, errors, blocker) {
    var statuses = responses.map(responseStatus);
    var finished = finite((trace.timings || {}).total_duration_ms) !== null;
    if (statuses.indexOf("cancelled") >= 0) return "cancelled";
    if (errors.some(isTimeout)) return "timeout";
    if (blocker && BLOCKING_EVENTS[blocker.source]) return "blocked";
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
    if (tools.length) result.push({ id: "tools", label: "Инструменты", status: "done" });
    result.push({ id: "finish", label: "Итог", status: finished ? "done" : "pending" });
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

  function models(responses) {
    var seen = [];
    responses.forEach(function (response) {
      var model = text(response && response.raw && response.raw.model, 80);
      if (model && seen.indexOf(model) < 0) seen.push(model);
    });
    return seen;
  }

  function project(input) {
    var trace = asTrace(input);
    if (!trace) return null;
    var responses = list(trace.responses);
    var tools = list(trace.tool_calls);
    var errors = list(trace.errors);
    var events = list(trace.events);
    var blocker = findBlocker(events, errors);
    var state = deriveState(trace, responses, errors, blocker);
    return {
      schema: SCHEMA,
      trace_id: text(trace.trace_id, 64) || null,
      state: state,
      stages: stages(trace, responses, tools, state),
      latest_event: latestEvent(events),
      blocker: state === "done" ? null : blocker,
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

  function summaryParts(surface) {
    var parts = [STATE_LABELS[surface.state]];
    if (surface.model_calls) parts.push(surface.model_calls + " выз. модели");
    if (surface.tool_calls) parts.push(surface.tool_calls + " инстр.");
    if (surface.duration_ms !== null) parts.push(formatDuration(surface.duration_ms));
    if (surface.usage.status === "measured") {
      var tokens = surface.usage.total + " ток.";
      if (surface.usage.cached) tokens += " (кэш " + surface.usage.cached + ")";
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

  function renderBody(surface, doc) {
    var body = doc.createElement("div");
    body.className = "execution-surface-body";

    var stagesEl = doc.createElement("ol");
    stagesEl.className = "execution-surface-stages";
    surface.stages.forEach(function (stage) {
      var item = doc.createElement("li");
      item.dataset.status = stage.status;
      item.textContent = stage.label;
      stagesEl.appendChild(item);
    });
    body.appendChild(stagesEl);

    var metricsEl = doc.createElement("div");
    metricsEl.className = "execution-surface-metrics";

    var timingRow = doc.createElement("div");
    timingRow.className = "execution-surface-timing";
    if (surface.duration_ms !== null) {
      timingRow.dataset.status = "measured";
      timingRow.textContent = "Время: " + formatDuration(surface.duration_ms) + " (измерено)";
    } else {
      timingRow.dataset.status = "unknown";
      timingRow.textContent = "Время: неизвестно";
    }
    metricsEl.appendChild(timingRow);

    var usageRow = doc.createElement("div");
    usageRow.className = "execution-surface-usage";
    usageRow.dataset.status = surface.usage.status;
    if (surface.usage.status === "measured") {
      var tokenParts = [surface.usage.total + " ток."];
      if (surface.usage.input) tokenParts.push("вх " + surface.usage.input);
      if (surface.usage.output) tokenParts.push("вых " + surface.usage.output);
      if (surface.usage.cached) tokenParts.push("кэш " + surface.usage.cached);
      usageRow.textContent = "Токены: " + tokenParts.join(", ") + " (измерено)";
    } else {
      usageRow.textContent = "Токены: неизвестно";
    }
    metricsEl.appendChild(usageRow);

    var costRow = doc.createElement("div");
    costRow.className = "execution-surface-cost";
    costRow.dataset.status = surface.cost.status;
    if (surface.cost.status === "unknown") {
      costRow.textContent = "Стоимость: неизвестно";
    } else {
      var prefix = surface.cost.status === "partial" ? "≥ " : "";
      var amount = surface.cost.amount.toFixed(4) + " " + (surface.cost.currency || "");
      costRow.textContent =
        "Стоимость: " + prefix + amount.trim() + " (" + surface.cost.status + ")";
    }
    metricsEl.appendChild(costRow);

    body.appendChild(metricsEl);

    if (surface.blocker) {
      var blockerEl = doc.createElement("div");
      blockerEl.className = "execution-surface-blocker";
      blockerEl.textContent = "Причина: " + surface.blocker.label;
      body.appendChild(blockerEl);
    }

    if (surface.models && surface.models.length) {
      var modelsEl = doc.createElement("div");
      modelsEl.className = "execution-surface-models";
      modelsEl.textContent = "Модели: " + surface.models.join(", ");
      body.appendChild(modelsEl);
    }

    return body;
  }

  function render(surface) {
    var doc = document;
    var root = doc.createElement("details");
    root.className = "execution-surface";
    root.dataset.state = surface.state;

    var summaryEl = doc.createElement("summary");
    summaryEl.className = "execution-surface-summary";
    summaryEl.textContent = summaryParts(surface).join(" · ");
    root.appendChild(summaryEl);

    root.appendChild(renderBody(surface, doc));
    return root;
  }

  window.AliceExecutionSurface = Object.freeze({
    SCHEMA: SCHEMA,
    project: project,
    render: render,
    summary: function (surface) {
      return summaryParts(surface).join(" · ");
    },
  });
})();
