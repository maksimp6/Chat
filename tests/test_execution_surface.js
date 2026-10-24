"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const { BrowserShim } = require("../browser/emulator/browser_dom");

const ROOT = path.resolve(__dirname, "..");
const CANARY = "sk-CANARY-should-never-render";

function boot(extraSources = []) {
  const browser = new BrowserShim('<div id="chatbox"></div>');
  return browser.load([path.join(ROOT, "static", "execution_surface.js"), ...extraSources], {
    AliceDispatcher: { request: async () => ({ json: async () => ({}) }) },
  });
}

function finishedTrace(overrides = {}) {
  return {
    trace_id: "trace-123",
    timings: { total_duration_ms: 1500 },
    api_requests: [{ step: 1, payload: { input: CANARY, model: "gpt://x/demo/latest" } }],
    responses: [
      {
        step: 1,
        request: { input: CANARY },
        raw: {
          status: "completed",
          model: "demo",
          output: [{ content: CANARY }],
          usage: {
            input_tokens: 100,
            output_tokens: 20,
            total_tokens: 120,
            input_tokens_details: { cached_tokens: 40 },
          },
        },
      },
    ],
    tool_calls: [{ name: "search", arguments: { query: CANARY }, result: CANARY }],
    errors: [],
    events: [
      { type: "api_request_completed", timestamp: 1, payload: { secret: CANARY } },
      { type: "api_response_received", timestamp: 2, payload: {} },
    ],
    billing: { currency: "RUB", items: [] },
    ...overrides,
  };
}

const { window, document } = boot();
const surfaceApi = window.AliceExecutionSurface;
const project = (input) => {
  const result = surfaceApi.project(input);
  return result && JSON.parse(JSON.stringify(result));
};

// Invalid input
assert.equal(project(null), null);
assert.equal(project("not json"), null);
assert.equal(project([1, 2]), null);
assert.equal(project(JSON.stringify({ trace_id: "s" })).trace_id, "s");

// Done with measured usage, unknown cost (no billing items)
const done = project(finishedTrace());
assert.equal(done.schema, "alice.execution_surface.v1");
assert.equal(done.state, "done");
assert.equal(done.blocker, null);
assert.deepEqual(done.models, ["demo"]);
assert.equal(done.tool_calls, 1);
assert.equal(done.model_calls, 1);
assert.equal(done.duration_ms, 1500);
assert.deepEqual(done.usage, {
  status: "measured",
  input: 100,
  output: 20,
  cached: 40,
  total: 120,
});
assert.deepEqual(done.cost, { status: "unknown", amount: null, currency: "RUB" });
assert.deepEqual(
  done.stages.map((stage) => [stage.id, stage.status]),
  [
    ["request", "done"],
    ["model", "done"],
    ["tools", "done"],
    ["finish", "done"],
  ],
);
assert.deepEqual(done.latest_event, { type: "api_response_received", timestamp: 2 });

// Redaction canary: projection and rendered text never carry payloads
assert.ok(!JSON.stringify(done).includes(CANARY), "projection leaked a payload");
const rendered = surfaceApi.render(done);
assert.ok(!rendered.textContent.includes(CANARY), "render leaked a payload");
assert.equal(rendered.dataset.state, "done");
assert.match(rendered.textContent, /✅ Готово/);
assert.match(rendered.textContent, /120 ток\. \(кэш 40\)/);
assert.match(rendered.textContent, /стоимость: неизвестно/);
assert.equal(rendered.querySelectorAll("li").length, 4);

// Billing items -> calculated / partial cost; billing tokens are authoritative
const billed = finishedTrace({
  billing: {
    currency: "RUB",
    items: [{}],
    cost_status: "calculated",
    total_cost: 0.12345,
    input_tokens: 7,
    output_tokens: 3,
    cached_input_tokens: 0,
    total_tokens: 10,
  },
});
const calculated = project(billed);
assert.deepEqual(calculated.cost, { status: "calculated", amount: 0.12345, currency: "RUB" });
assert.equal(calculated.usage.total, 10);
assert.match(surfaceApi.summary(calculated), /0\.1235 RUB \(расчёт по тарифу\)/);
assert.doesNotMatch(surfaceApi.summary(calculated), /кэш/);
billed.billing.cost_status = "partial";
assert.match(surfaceApi.summary(project(billed)), /≥ 0\.1235 RUB/);
billed.billing.currency = "";
assert.equal(project(billed).cost.currency, null);
assert.match(surfaceApi.summary(project(billed)), /≥ 0\.1235 \(/);

// Working: no responses yet, nothing measured
const working = project({ api_requests: [{ step: 1 }], events: [{ payload: {} }] });
assert.equal(working.state, "working");
assert.equal(working.latest_event, null);
assert.equal(working.duration_ms, null);
assert.deepEqual(working.usage.status, "unknown");
assert.deepEqual(
  working.stages.map((stage) => stage.status),
  ["done", "active", "pending"],
);
assert.match(
  surfaceApi.summary(working),
  /⏳ Выполняется · токены: неизвестно · стоимость: неизвестно/,
);
assert.deepEqual(
  project({}).stages.map((stage) => stage.status),
  ["pending", "pending", "pending"],
);

// Failure states
const rawWith = (status) => ({ responses: [{ raw: { status } }] });
assert.equal(project(rawWith("cancelled")).state, "cancelled");
assert.equal(project(rawWith("incomplete")).state, "partial");
assert.equal(project(rawWith("failed")).state, "error");
assert.equal(project({ responses: [{ raw: {} }, null] }).state, "working");

const timeout = project({ errors: [{ type: "ReadTimeout", source: "provider" }] });
assert.equal(timeout.state, "timeout");
assert.deepEqual(timeout.blocker, { source: "provider", label: "ReadTimeout" });

const failed = project({ errors: [{}] });
assert.equal(failed.state, "error");
assert.deepEqual(failed.blocker, { source: "error", label: "Ошибка" });
const errorEl = surfaceApi.render(failed);
assert.match(errorEl.querySelector(".execution-surface-blocker").textContent, /Ошибка/);

const partial = project({
  responses: [{ raw: { status: "completed" } }],
  errors: [{ type: "ToolError", source: "tool:x" }],
});
assert.equal(partial.state, "partial");

const blocked = project({
  timings: { total_duration_ms: 5 },
  events: [{ type: "provider_quota_denied", timestamp: 3 }],
});
assert.equal(blocked.state, "blocked");
assert.deepEqual(blocked.blocker, { source: "provider_quota_denied", label: "Квота провайдера" });
assert.match(surfaceApi.summary(blocked), /⛔ Заблокировано · 5 мс/);

// Missing numeric fields fall back to zero instead of NaN
const sparse = project({
  timings: { total_duration_ms: "abc" },
  billing: { items: [{}], total_cost: 1, cost_status: "calculated" },
});
assert.equal(sparse.duration_ms, null);
assert.deepEqual(sparse.usage, { status: "measured", input: 0, output: 0, cached: 0, total: 0 });
assert.deepEqual(project({ responses: [{ raw: { usage: {} } }] }).usage, {
  status: "measured",
  input: 0,
  output: 0,
  cached: 0,
  total: 0,
});

// Unknown usage stays unknown even when a response has no usage block
assert.equal(
  project({ responses: [{ raw: { status: "completed", usage: "n/a" } }] }).usage.status,
  "unknown",
);

// Chat integration: assistant messages with a trace show the compact surface
const chat = boot([path.join(ROOT, "static", "chat.js")]);
chat.context.addMessage("Ответ", "bot", false, 0, null, 0, null, null, finishedTrace());
const surfaceEl = chat.document.querySelector(".execution-surface");
assert.ok(surfaceEl, "chat renders the execution surface for traced replies");
assert.ok(!chat.document.getElementById("chatbox").textContent.includes(CANARY));

console.log("execution surface tests passed");
