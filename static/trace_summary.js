(function () {
  "use strict";

  function asTrace(input) {
    if (!input) return null;
    if (typeof input === "string") {
      try {
        input = JSON.parse(input);
      } catch (_) {
        return null;
      }
    }
    return input && typeof input === "object" ? input : null;
  }

  function finite(value) {
    var number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function durationMs(trace) {
    var timings = trace.timings || {};
    var total = finite(timings.total_duration_ms);
    if (total !== null) return Math.max(0, total);

    var created = finite(trace.created_at);
    var latest = null;
    (trace.events || []).forEach(function (event) {
      var ts = finite(event && event.timestamp);
      if (ts !== null) latest = latest === null ? ts : Math.max(latest, ts);
    });
    return created !== null && latest !== null ? Math.max(0, (latest - created) * 1000) : null;
  }

  function formatDuration(value) {
    var ms = finite(value);
    if (ms === null) return "";
    if (ms < 1000) return Math.round(ms) + " мс";
    return (ms / 1000).toFixed(ms < 10000 ? 2 : 1) + " с";
  }

  function usageTotals(trace) {
    var result = { input: 0, output: 0, total: 0 };
    (trace.responses || []).forEach(function (response) {
      var usage = response && response.raw && response.raw.usage;
      if (!usage) return;
      result.input += Number(usage.input_tokens || 0);
      result.output += Number(usage.output_tokens || 0);
      result.total += Number(usage.total_tokens || 0);
    });
    return result;
  }

  function modelName(trace) {
    var responses = Array.isArray(trace.responses) ? trace.responses : [];
    for (var i = responses.length - 1; i >= 0; i--) {
      var model = responses[i] && responses[i].raw && responses[i].raw.model;
      if (typeof model === "string" && model) return model;
    }
    var requests = Array.isArray(trace.api_requests) ? trace.api_requests : [];
    for (var j = requests.length - 1; j >= 0; j--) {
      var payload = requests[j] && requests[j].payload;
      if (payload && typeof payload.model === "string" && payload.model) return payload.model;
    }
    return "";
  }

  function operationModel(response, request, fallback) {
    var responseModel = response && response.raw && response.raw.model;
    if (typeof responseModel === "string" && responseModel) return responseModel;
    var requestModel = request && request.payload && request.payload.model;
    if (typeof requestModel === "string" && requestModel) return requestModel;
    return fallback;
  }

  function responseDuration(response) {
    if (!response) return null;
    var start = finite(response.start_timestamp);
    var end = finite(response.end_timestamp);
    if (start !== null && end !== null && end >= start) return (end - start) * 1000;
    return finite(response.timing_ms);
  }

  function toolDuration(tool) {
    if (!tool) return null;
    var direct = finite(tool.timing_ms);
    if (direct !== null) return direct;
    var start = finite(tool.start_timestamp);
    var end = finite(tool.end_timestamp);
    return start !== null && end !== null && end >= start ? (end - start) * 1000 : null;
  }

  function stepKey(entry, index, prefix) {
    if (entry && entry.step !== undefined && entry.step !== null) {
      return "step:" + String(entry.step);
    }
    return prefix + ":" + String(index);
  }

  function firstTimestamp() {
    for (var i = 0; i < arguments.length; i++) {
      var value = finite(arguments[i]);
      if (value !== null) return value;
    }
    return null;
  }

  function build(input) {
    var trace = asTrace(input);
    if (!trace) return null;

    var responses = Array.isArray(trace.responses) ? trace.responses : [];
    var tools = Array.isArray(trace.tool_calls) ? trace.tool_calls : [];
    var errors = Array.isArray(trace.errors) ? trace.errors : [];
    var apiRequests = Array.isArray(trace.api_requests) ? trace.api_requests : [];
    var timeline = [];
    var model = modelName(trace);
    var requestByStep = {};
    var responseSteps = {};
    var apiKeys = {};
    var order = 0;

    apiRequests.forEach(function (request, index) {
      var key = stepKey(request, index, "request");
      apiKeys[key] = true;
      if (request && request.step !== undefined && request.step !== null) {
        requestByStep[String(request.step)] = request;
      }
    });

    responses.forEach(function (response, index) {
      var key = stepKey(response, index, "response");
      apiKeys[key] = true;
      if (response && response.step !== undefined && response.step !== null) {
        responseSteps[String(response.step)] = true;
      }
    });

    var apiCount = Object.keys(apiKeys).length;

    responses.forEach(function (response, index) {
      var step =
        response && response.step !== undefined && response.step !== null ? response.step : null;
      var request = step === null ? null : requestByStep[String(step)] || null;
      var duration = formatDuration(responseDuration(response));
      var operationModelName = operationModel(response, request, model);
      timeline.push({
        item: {
          kind: "api",
          icon: "🤖",
          label: "Модель" + (apiCount > 1 ? " #" + (step === null ? index + 1 : step) : ""),
          detail: [operationModelName, duration].filter(Boolean).join(" · "),
          status:
            response && response.raw && response.raw.status ? String(response.raw.status) : "",
        },
        timestamp: firstTimestamp(
          response && response.start_timestamp,
          request && request.timestamp,
          response && response.timestamp,
          response && response.end_timestamp,
        ),
        order: order++,
      });
    });

    apiRequests.forEach(function (request, index) {
      var step =
        request && request.step !== undefined && request.step !== null ? request.step : null;
      if (step !== null && responseSteps[String(step)]) return;
      timeline.push({
        item: {
          kind: "api",
          icon: "📤",
          label: "API-запрос" + (apiCount > 1 ? " #" + (step === null ? index + 1 : step) : ""),
          detail: operationModel(null, request, model),
          status: "отправлен",
        },
        timestamp: firstTimestamp(request && request.timestamp),
        order: order++,
      });
    });

    var failedToolCallIds = {};
    var failedToolSources = {};
    var failedToolCount = 0;

    tools.forEach(function (tool) {
      var duration = formatDuration(toolDuration(tool));
      var server = tool && tool.server ? String(tool.server) : "";
      var toolName =
        tool && (tool.name || tool.tool_name) ? String(tool.name || tool.tool_name) : "Инструмент";
      var failed = Boolean(tool && tool.error);
      if (failed) {
        failedToolCount += 1;
        if (tool.call_id !== undefined && tool.call_id !== null) {
          failedToolCallIds[String(tool.call_id)] = true;
        }
        failedToolSources["tool:" + toolName] = true;
      }
      timeline.push({
        item: {
          kind: "tool",
          icon: failed ? "❌" : "🔧",
          label: toolName,
          detail: [server, duration].filter(Boolean).join(" · "),
          status: failed ? "ошибка" : "готово",
        },
        timestamp: firstTimestamp(
          tool && tool.start_timestamp,
          tool && tool.timestamp,
          tool && tool.end_timestamp,
        ),
        order: order++,
      });
    });

    var standaloneErrors = [];
    errors.forEach(function (error) {
      var callId =
        error && error.call_id !== undefined && error.call_id !== null ? String(error.call_id) : "";
      var source = error && error.source ? String(error.source) : "";
      if ((callId && failedToolCallIds[callId]) || (source && failedToolSources[source])) return;
      standaloneErrors.push(error);
      timeline.push({
        item: {
          kind: "error",
          icon: "⚠️",
          label: source || "Ошибка",
          detail: error && error.type ? String(error.type) : "",
          status: "ошибка",
        },
        timestamp: firstTimestamp(error && error.timestamp),
        order: order++,
      });
    });

    timeline.sort(function (left, right) {
      if (left.timestamp === null && right.timestamp === null) return left.order - right.order;
      if (left.timestamp === null) return 1;
      if (right.timestamp === null) return -1;
      if (left.timestamp !== right.timestamp) return left.timestamp - right.timestamp;
      return left.order - right.order;
    });

    var items = timeline.map(function (entry) {
      return entry.item;
    });

    var usage = usageTotals(trace);
    var totalMs = durationMs(trace);
    var cost =
      finite(trace.cost) !== null
        ? finite(trace.cost)
        : trace.billing && finite(trace.billing.total_cost) !== null
          ? finite(trace.billing.total_cost)
          : null;

    var errorCount = failedToolCount + standaloneErrors.length;
    var parts = [];
    if (apiCount) parts.push(apiCount + " API");
    if (tools.length) parts.push(tools.length + " инстр.");
    if (errorCount) parts.push(errorCount + " ош.");
    if (totalMs !== null) parts.push(formatDuration(totalMs));

    return {
      title: "⚙️ Что сделала Alice" + (parts.length ? " · " + parts.join(" · ") : ""),
      items: items,
      metrics: {
        api_requests: apiCount,
        tools: tools.length,
        errors: errorCount,
        duration_ms: totalMs,
        tokens: usage.total,
        cost: cost,
        model: model,
      },
    };
  }

  window.AliceTraceSummary = Object.freeze({
    build: build,
  });
})();
