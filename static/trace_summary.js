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

  function responseDuration(response) {
    if (!response) return null;
    var direct = finite(response.timing_ms);
    if (direct !== null) return direct;
    var start = finite(response.start_timestamp);
    var end = finite(response.end_timestamp);
    return start !== null && end !== null && end >= start ? (end - start) * 1000 : null;
  }

  function toolDuration(tool) {
    if (!tool) return null;
    var direct = finite(tool.timing_ms);
    if (direct !== null) return direct;
    var start = finite(tool.start_timestamp);
    var end = finite(tool.end_timestamp);
    return start !== null && end !== null && end >= start ? (end - start) * 1000 : null;
  }

  function build(input) {
    var trace = asTrace(input);
    if (!trace) return null;

    var responses = Array.isArray(trace.responses) ? trace.responses : [];
    var tools = Array.isArray(trace.tool_calls) ? trace.tool_calls : [];
    var errors = Array.isArray(trace.errors) ? trace.errors : [];
    var apiRequests = Array.isArray(trace.api_requests) ? trace.api_requests : [];
    var items = [];
    var model = modelName(trace);

    responses.forEach(function (response, index) {
      var duration = formatDuration(responseDuration(response));
      items.push({
        kind: "api",
        icon: "🤖",
        label: "Модель" + (responses.length > 1 ? " #" + (index + 1) : ""),
        detail: [model, duration].filter(Boolean).join(" · "),
        status: response && response.raw && response.raw.status ? String(response.raw.status) : "",
      });
    });

    if (!responses.length) {
      apiRequests.forEach(function (request, index) {
        items.push({
          kind: "api",
          icon: "📤",
          label: "API-запрос" + (apiRequests.length > 1 ? " #" + (index + 1) : ""),
          detail: model,
          status: "отправлен",
        });
      });
    }

    tools.forEach(function (tool) {
      var duration = formatDuration(toolDuration(tool));
      var server = tool && tool.server ? String(tool.server) : "";
      items.push({
        kind: "tool",
        icon: tool && tool.error ? "❌" : "🔧",
        label: tool && (tool.name || tool.tool_name) ? String(tool.name || tool.tool_name) : "Инструмент",
        detail: [server, duration].filter(Boolean).join(" · "),
        status: tool && tool.error ? "ошибка" : "готово",
      });
    });

    errors.forEach(function (error) {
      items.push({
        kind: "error",
        icon: "⚠️",
        label: error && error.source ? String(error.source) : "Ошибка",
        detail: error && error.type ? String(error.type) : "",
        status: "ошибка",
      });
    });

    var usage = usageTotals(trace);
    var totalMs = durationMs(trace);
    var cost =
      finite(trace.cost) !== null
        ? finite(trace.cost)
        : trace.billing && finite(trace.billing.total_cost) !== null
          ? finite(trace.billing.total_cost)
          : null;

    var parts = [];
    var apiCount = responses.length || apiRequests.length;
    if (apiCount) parts.push(apiCount + " API");
    if (tools.length) parts.push(tools.length + " инстр.");
    if (errors.length) parts.push(errors.length + " ош.");
    if (totalMs !== null) parts.push(formatDuration(totalMs));

    return {
      title: "⚙️ Что сделала Alice" + (parts.length ? " · " + parts.join(" · ") : ""),
      items: items,
      metrics: {
        api_requests: apiCount,
        tools: tools.length,
        errors: errors.length,
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
