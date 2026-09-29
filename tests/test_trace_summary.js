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
  api_requests: [{ step: 1, payload: { model: "gpt://project/demo/latest" } }],
  responses: [
    {
      step: 1,
      timing_ms: 700,
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
      arguments: { secret: "must-not-render" },
      result: { body: "must-not-render" },
    },
  ],
  errors: [{ source: "tool:demo", error: "sensitive internal text", type: "tool_error" }],
};

const summary = build(trace);
assert(summary.title.includes("1 API"), "summary should count API calls");
assert(summary.title.includes("1 инстр."), "summary should count tools");
assert(summary.title.includes("1 ош."), "summary should count errors");
assert(summary.metrics.tokens === 15, "summary should aggregate token usage");
assert(summary.metrics.cost === 0.42, "summary should expose numeric cost");
assert(summary.metrics.model === "demo", "summary should expose model");
assert(summary.items.some((item) => item.label === "filesystem.read_text"), "tool should be named");
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

console.log("trace summary tests passed");
