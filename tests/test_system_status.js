"use strict";

const assert = require("assert");
const { BrowserShim } = require("./browser_dom");

const ROOT = require("path").resolve(__dirname, "..");

async function main() {
  const browser = new BrowserShim(
    '<button id="system-status-btn" type="button" data-action="system-status.toggle"></button>' +
      '<div id="alice-system-status" hidden role="dialog">' +
      '<div class="alice-system-status-content">' +
      '<button class="alice-btn alice-system-status-close" type="button"></button>' +
      '<h2 id="alice-system-status-title"></h2>' +
      '<div class="alice-system-status-list"></div>' +
      "</div></div>",
  );
  let requests = 0;
  const mockFetch = async () => {
    requests += 1;
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  const sourcePaths = ["core_api.js", "ui_runtime.js", "dispatcher.js", "system_status.js"].map(
    (name) => require("path").join(ROOT, "static", name),
  );
  browser.load(sourcePaths, { fetch: mockFetch, URL });

  const button = browser.document.getElementById("system-status-btn");
  const panel = browser.document.getElementById("alice-system-status");
  const close = panel.querySelector(".alice-system-status-close");

  const core = browser.window.AliceCoreAPI;
  assert.strictEqual(core.apiVersion, "1");

  const moduleContext = core.module.register({
    id: "test-module",
    version: "1.0.0",
    apiVersion: "1",
    capabilities: ["storage.read", "network"],
  });

  moduleContext.status.set("running", "Тестовая операция token=visible-secret", {
    revocable: true,
    authorization: "do-not-leak",
  });
  const statusSnapshot = core.status.snapshot();
  assert.ok(!JSON.stringify(statusSnapshot).includes("visible-secret"));

  const trace = moduleContext.trace.begin("secret-operation", {
    api_key: "top-secret",
    safe: "visible",
  });
  trace.toolCall("test-tool", {
    token: "secret-token",
    value: "safe",
  });
  trace.response({ status: 200, password: "hidden" });
  trace.response("Bearer plain-secret");
  const snapshot = trace.end("completed");

  const serialized = JSON.stringify(snapshot);
  assert.ok(!serialized.includes("top-secret"));
  assert.ok(!serialized.includes("secret-token"));
  assert.ok(!serialized.includes("hidden"));
  assert.ok(!serialized.includes("plain-secret"));
  assert.ok(serialized.includes("visible"));

  assert.strictEqual(moduleContext.security.require("storage.read"), true);
  assert.strictEqual(core.revocation.isRevoked("module", "test-module"), false);

  core.revocation.revokeAll("test-module", "user_requested");
  assert.strictEqual(core.revocation.isRevoked("module", "test-module"), true);
  assert.throws(() => moduleContext.security.require("storage.read"), /Capability revoked/);

  button.click();
  assert.strictEqual(panel.hasAttribute("hidden"), false);
  assert.strictEqual(button.getAttribute("aria-expanded"), "true");

  close.click();
  assert.strictEqual(panel.hasAttribute("hidden"), true);
  assert.strictEqual(button.getAttribute("aria-expanded"), "false");

  const response = await browser.window.AliceDispatcher.request("/api/status");
  assert.strictEqual(response.status, 200);
  assert.strictEqual(requests, 1);
  assert.ok(JSON.stringify(core.status.snapshot()).includes("network"));

  const networkTrace = moduleContext.trace.begin("network-test", { authorization: "secret" });
  networkTrace.event("response", { path: "/api/test", status: 200 });
  const networkSnapshot = networkTrace.end("completed");
  assert.strictEqual(networkSnapshot.metadata.authorization, "[REDACTED]");
  assert.ok(!JSON.stringify(networkSnapshot).includes("secret"));

  console.log("system status/core API contract tests passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
