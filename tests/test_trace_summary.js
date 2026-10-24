const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(path.join(__dirname, "..", "static", "trace_summary.js"), "utf8");
const context = { window: {} };
vm.createContext(context);
vm.runInContext(source, context, { filename: "trace_summary.js" });

const build = context.window.AliceTraceSummary.build;

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const trace = {
  created_at: 10,
  timings: { total_duration_ms: 1250 },
  cost: 0.42,
  api_requests: [{ step: 1, timestamp: 10.1, payload: { model: "gpt://project/demo/latest" } }],
  responses: [
    {
      step: 1,
      timing_ms: 700,
      start_timestamp: 10.1,
      end_timestamp: 10.8,
      raw: {
        id: "resp-1",
        status: "completed",
        model: "demo",
        usage: { input_tokens: 10, output_tokens: 5, total_tokens: 15 },
      },
    },
  ],
  tool_calls: [
    {
      name: "filesystem.read_text",
      server: "Local Registry",
      timing_ms: 80,
      start_timestamp: 10.85,
      end_timestamp: 10.93,
      arguments: { secret: "must-not-render" },
      result: { body: "must-not-render" },
    },
  ],
  errors: [
    {
      source: "provider",
      error: "sensitive internal text",
      type: "provider_error",
      timestamp: 10.95,
    },
  ],
};

const summary = build(trace);
assert(summary.title.includes("1 API"), "summary should count API calls");
assert(summary.title.includes("1 инстр."), "summary should count tools");
assert(summary.title.includes("1 ош."), "summary should count errors");
assert(summary.metrics.tokens === 15, "summary should aggregate token usage");
assert(summary.metrics.cost === 0.42, "summary should expose numeric cost");
assert(summary.metrics.model === "demo", "summary should expose model");
assert(
  summary.items.some((item) => item.label === "filesystem.read_text"),
  "tool should be named",
);
assert(
  JSON.stringify(summary).indexOf("must-not-render") === -1,
  "summary must not copy tool arguments or results",
);
assert(
  JSON.stringify(summary).indexOf("sensitive internal text") === -1,
  "summary must not copy raw error messages",
);

const stringSummary = build(JSON.stringify(trace));
assert(stringSummary && stringSummary.metrics.tools === 1, "JSON string trace should be supported");
assert(build("{broken") === null, "invalid JSON trace should fail closed");

const pollingTrace = {
  api_requests: [{ step: 1, timestamp: 1, payload: { model: "demo" } }],
  responses: [
    {
      step: 1,
      timing_ms: 100,
      start_timestamp: 1,
      end_timestamp: 11,
      raw: { status: "completed", model: "demo" },
    },
  ],
};
const pollingSummary = build(pollingTrace);
assert(
  pollingSummary.items[0].detail.includes("10.0 с"),
  "response duration should prefer the full start/end polling interval",
);

const chronologicalTrace = {
  api_requests: [
    { step: 1, timestamp: 1, payload: { model: "demo" } },
    { step: 2, timestamp: 3, payload: { model: "demo" } },
  ],
  responses: [
    {
      step: 1,
      start_timestamp: 1,
      end_timestamp: 2,
      raw: { status: "completed", model: "demo" },
    },
    {
      step: 2,
      start_timestamp: 3,
      end_timestamp: 4,
      raw: { status: "completed", model: "demo" },
    },
  ],
  tool_calls: [
    {
      name: "filesystem.read_text",
      start_timestamp: 2.2,
      end_timestamp: 2.6,
    },
  ],
};
const chronologicalSummary = build(chronologicalTrace);
assert(
  chronologicalSummary.items.map((item) => item.kind).join(",") === "api,tool,api",
  "summary items should preserve execution chronology",
);

const requestOnlyTrace = {
  api_requests: [
    { step: 1, timestamp: 1, payload: { model: "demo" } },
    { step: 2, timestamp: 2, payload: { model: "demo" } },
  ],
  responses: [
    {
      step: 1,
      start_timestamp: 1,
      end_timestamp: 1.5,
      raw: { status: "completed", model: "demo" },
    },
  ],
};
const requestOnlySummary = build(requestOnlyTrace);
assert(requestOnlySummary.metrics.api_requests === 2, "request-only API attempts must be counted");
assert(
  requestOnlySummary.items.some((item) => item.kind === "api" && item.status === "отправлен"),
  "request-only API attempts must remain visible",
);

const failedToolTrace = {
  tool_calls: [
    {
      call_id: "call-1",
      name: "filesystem.read_text",
      error: "sensitive tool failure",
      start_timestamp: 1,
      end_timestamp: 2,
    },
  ],
  errors: [
    {
      call_id: "call-1",
      source: "tool:filesystem.read_text",
      error: "sensitive tool failure",
      timestamp: 2,
    },
  ],
};
const failedToolSummary = build(failedToolTrace);
assert(failedToolSummary.metrics.errors === 1, "failed tool error should be counted once");
assert(
  failedToolSummary.items.filter((item) => item.kind === "error").length === 0,
  "failed tool error should not render a duplicate standalone row",
);
assert(
  JSON.stringify(failedToolSummary).indexOf("sensitive tool failure") === -1,
  "failed tool summary must not expose raw error text",
);

console.log("trace summary tests passed");
