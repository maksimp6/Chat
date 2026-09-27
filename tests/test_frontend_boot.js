const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("static/boot.js", "utf8");

const listeners = {};
const logs = [];
const bootstrapNotice = { hidden: true };
const documentElementAttributes = {};
const context = {
  console: {
    info: (...args) => logs.push(["info", ...args]),
    error: (...args) => logs.push(["error", ...args]),
    warn: (...args) => logs.push(["warn", ...args]),
  },
  document: {
    currentScript: { dataset: {} },
    documentElement: {
      setAttribute: (name, value) => {
        documentElementAttributes[name] = value;
      },
    },
    getElementById: (id) => (id === "bootstrap-status" ? bootstrapNotice : null),
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
assert.equal(documentElementAttributes["data-bootstrap-state"], "degraded");
assert.equal(bootstrapNotice.hidden, false);
assert.equal(diagnostic.detail.event, "critical_script_failure");
assert.equal(diagnostic.detail.module, "dispatcher");
assert.equal(diagnostic.detail.failureType, "download");

bootstrapNotice.hidden = true;
listeners.error({ filename: "https://example.invalid/static/core.js?version=secret" });
assert.equal(bootstrapNotice.hidden, false);
assert.equal(diagnostic.detail.module, "core");
assert.equal(diagnostic.detail.failureType, "runtime");
assert.equal("filename" in diagnostic.detail, false);

console.log("boot observability regression checks passed");
