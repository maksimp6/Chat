const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("static/boot.js", "utf8");

const listeners = {};
const logs = [];
const statusElement = { hidden: true };
const rootAttributes = {};
const context = {
  console: {
    info: (...args) => logs.push(["info", ...args]),
    error: (...args) => logs.push(["error", ...args]),
    warn: (...args) => logs.push(["warn", ...args]),
  },
  document: {
    currentScript: { dataset: {} },
    getElementById: (id) => {
      if (id === "frontend-degraded-status") return statusElement;
      if (id === "app-root") {
        return { setAttribute: (name, value) => (rootAttributes[name] = value) };
      }
      return null;
    },
  },
  window: {
    addEventListener: (name, fn) => {
      listeners[name] = fn;
    },
    dispatchEvent: () => {},
    fetch: () => Promise.resolve({}),
  },
  navigator: {},
  Headers: class {
    set() {}
  },
  URL,
  Request: class {},
  EventSource: null,
  CustomEvent: class {
    constructor(name, init) {
      this.name = name;
      this.detail = init.detail;
    }
  },
};
context.window.window = context.window;
context.window.fetch = function () {
  return Promise.resolve({});
};
vm.runInNewContext(source, context, { filename: "static/boot.js" });

assert(logs.some((item) => item[0] === "info" && String(item[1]).includes("hello")));
assert.equal(typeof listeners.error, "function");
assert.equal(typeof listeners.unhandledrejection, "function");
assert.equal(typeof context.window.AliceBoot.markShellReady, "function");

let diagnostic;
context.window.dispatchEvent = (event) => {
  diagnostic = event;
};
listeners.error({
  message: "boom",
  error: new Error("boom"),
  filename: "app.js",
  lineno: 3,
  colno: 4,
});
assert.equal(diagnostic.detail.event, "uncaught_error");
assert.equal(diagnostic.detail.message, "boom");

listeners.unhandledrejection({ reason: new Error("promise boom") });
assert.equal(diagnostic.detail.event, "unhandled_rejection");
assert.equal(diagnostic.detail.message, "promise boom");

listeners.error({ target: { dataset: { criticalScript: "dispatcher" } } });
assert.equal(diagnostic.detail.event, "critical_script_failed");
assert.equal(diagnostic.detail.script, "dispatcher");
assert.deepEqual(Array.from(context.window.AliceBoot.failedCriticalScripts()), ["dispatcher"]);
assert.equal(context.window.AliceBoot.markShellReady(), false);
assert.equal(statusElement.hidden, false);
assert.equal(rootAttributes["data-enhancement-state"], "degraded");

listeners.error({ error: new Error("core crashed"), filename: "/static/core.js?v=1" });
assert.deepEqual(Array.from(context.window.AliceBoot.failedCriticalScripts()), [
  "dispatcher",
  "core",
]);

console.log("boot observability regression checks passed");
