"use strict";

// Core API redaction and revocation on the BrowserShim: real static/core_api.js.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

function loadCoreApi() {
  const shim = new BrowserShim("<body></body>");
  const { window } = shim.load(["static/core_api.js"]);
  return window.LogosCoreAPI;
}

test("a secret written as a JSON pair is redacted in logged text", () => {
  const api = loadCoreApi();
  const out = api.security.redact('{"api_key": "abc123", "ok": true}');
  assert.equal(out.includes("abc123"), false);
  assert.match(out, /\[REDACTED\]/);
});

test("a shared reference is copied in full, not marked as a cycle", () => {
  const api = loadCoreApi();
  const shared = { a: 1 };
  const out = api.security.redact({ x: shared, y: shared });
  assert.deepEqual(JSON.parse(JSON.stringify(out)), { x: { a: 1 }, y: { a: 1 } });
});

test("a real cycle is still marked", () => {
  const api = loadCoreApi();
  const loop = { name: "n" };
  loop.self = loop;
  const out = api.security.redact(loop);
  assert.equal(out.self, "[Circular]");
  assert.equal(out.name, "n");
});

test("revoking a module that was never registered is recorded without throwing", () => {
  const api = loadCoreApi();
  assert.equal(api.revocation.revoke("module", "ghost", "test"), true);
  assert.equal(api.revocation.isRevoked("module", "ghost"), true);
});
