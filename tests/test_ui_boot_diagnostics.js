"use strict";

// Boot diagnostics on the BrowserShim: real static/boot.js. A failed script
// load marks that script unavailable; a runtime error reports its own message.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

function bootFrontend() {
  const shim = new BrowserShim('<body><div id="app-root"></div></body>');
  const diagnostics = [];
  const { window } = shim.load(["static/boot.js"], {
    fetch: async () => ({ ok: true, status: 200 }),
  });
  window.addEventListener("logos:diagnostic", (event) => diagnostics.push(event.detail));
  return { window, diagnostics };
}

test("a failed load of a critical script marks it unavailable and shows the degraded state", () => {
  const { window, diagnostics } = bootFrontend();
  window.dispatchEvent({
    type: "error",
    target: { tagName: "SCRIPT", dataset: { criticalScript: "core" } },
  });

  assert.deepEqual([...window.LogosBoot.failedCriticalScripts()], ["core"]);
  assert.equal(diagnostics[0].event, "critical_script_failed");
  assert.equal(diagnostics[0].script, "core");
});

test("a runtime error in core.js marks core failed and keeps the error message", () => {
  const { window, diagnostics } = bootFrontend();
  window.dispatchEvent({
    type: "error",
    target: window,
    filename: "http://localhost/static/core.js?v=1",
    lineno: 12,
    colno: 3,
    message: "Cannot read properties of null",
    error: new Error("Cannot read properties of null"),
  });

  assert.deepEqual([...window.LogosBoot.failedCriticalScripts()], ["core"]);
  assert.equal(diagnostics[0].event, "critical_script_failed");
  assert.equal(diagnostics[0].script, "core");
  assert.equal(diagnostics[0].message, "Cannot read properties of null");
  assert.equal(diagnostics[0].line, 12);
});
