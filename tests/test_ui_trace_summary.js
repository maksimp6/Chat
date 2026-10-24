"use strict";

// Trace summary projection on the BrowserShim: real static/trace_summary.js.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

function summaryApi() {
  const shim = new BrowserShim("<body></body>");
  const { window } = shim.load(["static/trace_summary.js"]);
  return window.LogosTraceSummary;
}

test("a response with a missing start timestamp shows no duration instead of a 1970 interval", () => {
  const summary = summaryApi();
  const built = summary.build({
    responses: [{ step: 1, start_timestamp: null, end_timestamp: 1000, raw: {} }],
  });

  const response = built.items.find((item) => item.kind === "api");
  assert.equal(response.detail, "");
});

test("a null total cost is unknown, not zero", () => {
  const summary = summaryApi();
  const built = summary.build({ cost: null, responses: [] });

  assert.equal(built.metrics.cost, null);
});

test("a complete response keeps its real duration", () => {
  const summary = summaryApi();
  const built = summary.build({
    responses: [{ step: 1, start_timestamp: 1000, end_timestamp: 1002.5, raw: { model: "m1" } }],
  });

  const response = built.items.find((item) => item.kind === "api");
  assert.equal(response.detail, "m1 · 2.50 с");
});
