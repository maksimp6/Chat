"use strict";

// Execution trace viewer on the BrowserShim: real static/trace_viewer.js.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

function boot() {
  const shim = new BrowserShim("<body></body>");
  const { window, document } = shim.load(["static/trace_viewer.js"]);
  return { window, document };
}

const TRACE = {
  trace_id: "trace-123456789",
  events: [{ type: "request_started", timestamp: 1000 }],
  responses: [
    {
      step: 1,
      raw: {
        id: "resp_1",
        output: [{ text: "первая часть" }, { text: "вторая часть" }],
      },
    },
  ],
  tool_calls: [],
};

test("Escape closes the trace viewer", () => {
  const { window, document } = boot();
  window.openTraceViewer(TRACE);
  assert.notEqual(document.querySelector(".logos-trace-modal"), null);

  document.dispatchEvent({ type: "keydown", key: "Escape" });

  assert.equal(document.querySelector(".logos-trace-modal"), null);
});

test("the text of a response in several parts is joined with real line breaks", () => {
  const { window, document } = boot();
  window.openTraceViewer(TRACE);

  const responseItem = Array.from(document.querySelectorAll(".logos-trace-item")).find((item) =>
    item.textContent.includes("Responses API #1"),
  );
  responseItem.click();
  const textTab = Array.from(document.querySelectorAll(".logos-trace-tab")).find(
    (tab) => tab.textContent === "Text",
  );
  textTab.click();

  const inspector = document.querySelector(".logos-trace-inspector").textContent;
  assert.match(inspector, /первая часть\nвторая часть/);
  assert.doesNotMatch(inspector, /\\n/);
});
