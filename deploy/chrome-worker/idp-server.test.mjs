import assert from "node:assert/strict";
import { once } from "node:events";
import { createServer } from "node:http";
import test from "node:test";

import { createKeys } from "../oauth-idp/crypto.mjs";
import { signJwt } from "../oauth-idp/jwt-sign.mjs";
import { createWorker } from "./server.mjs";

const ISSUER = "https://oauth.example.test";
const RESOURCE = "https://chrome.example.test/browser/v1/mcp";
const keys = createKeys("k".repeat(40));
const NOW = Math.floor(Date.now() / 1000);
const token = (extra = {}) => signJwt(keys, { iss: ISSUER, sub: "12345", aud: RESOURCE, scope: "browser.control", exp: NOW + 600, ...extra });

async function fixture() {
  const chromium = { async launchPersistentContext() { return { pages: () => [], on() {}, async close() {} }; } };
  const worker = createWorker({
    token: "static-token",
    profileDir: "/state/profile",
    chromium,
    idp: { issuer: ISSUER, resource: RESOURCE, ownerIds: ["12345"], fetch: async () => new Response(JSON.stringify({ keys: [keys.jwk] })) },
  });
  const server = createServer(worker.handler).listen(0, "127.0.0.1");
  await once(server, "listening");
  const base = `http://127.0.0.1:${server.address().port}`;
  return { worker, call: (path, bearer, init = {}) => fetch(base + path, { ...init, headers: { ...(bearer ? { authorization: `Bearer ${bearer}` } : {}), ...init.headers } }), close: async () => { await new Promise((resolve) => server.close(resolve)); await worker.close(); } };
}

test("MCP endpoint accepts IdP tokens and challenges everything else", async (t) => {
  const app = await fixture();
  t.after(app.close);
  assert.notEqual((await app.call("/browser/v1/mcp", token())).status, 401);
  const anonymous = await app.call("/browser/v1/mcp");
  assert.equal(anonymous.status, 401);
  assert.match(anonymous.headers.get("www-authenticate"), /resource_metadata=/);
  assert.equal((await app.call("/browser/v1/mcp", token({ sub: "999" }))).status, 401);
  assert.equal((await app.call("/browser/v1/mcp", token({ scope: "mcp" }))).status, 401);
  assert.equal((await app.call("/browser/v1/mcp", token({ scope: "browser.read" }))).status, 401);
});

test("IdP tokens do not open the REST control surface; the static token still does", async (t) => {
  const app = await fixture();
  t.after(app.close);
  assert.equal((await app.call("/browser/v1/status", token())).status, 401);
  assert.equal((await app.call("/browser/v1/status", "static-token")).status, 200);
  assert.notEqual((await app.call("/browser/v1/mcp", "static-token")).status, 401);
});

test("protected-resource metadata points at the central IdP", async (t) => {
  const app = await fixture();
  t.after(app.close);
  for (const path of ["/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/browser/v1/mcp"]) {
    const metadata = await (await app.call(path)).json();
    assert.deepEqual(metadata.authorization_servers, [ISSUER]);
    assert.deepEqual(metadata.scopes_supported, ["browser.read", "browser.control"]);
  }
});

test("healthz reports the IdP mode", async (t) => {
  const app = await fixture();
  t.after(app.close);
  assert.equal((await (await app.call("/healthz")).json()).idp_ready, true);
});
