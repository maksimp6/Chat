[Reading 46 lines from start (total: 46 lines, 0 remaining)]

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createOAuth } from "./oauth.mjs";

const PUBLIC = "https://browser.example.test";
const RESOURCE = `${PUBLIC}/browser/v1/mcp`;
const REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect";
const VERIFIER = "v".repeat(43);
const SHORT_TOKEN = "short-token-owner-secret";
const hash = (value) => createHash("sha256").update(value).digest("base64url");

test("short token authorizes ChatGPT OAuth without GitHub", async (t) => {
  const directory = mkdtempSync(join(tmpdir(), "short-token-oauth-"));
  const oauth = createOAuth({ env: {}, publicUrl: PUBLIC, shortToken: SHORT_TOKEN, ownerId: "owner", stateFile: join(directory, "oauth.json") });
  const server = createServer(async (request, response) => {
    if (await oauth.handle(request, response, new URL(request.url, PUBLIC))) return;
    response.writeHead(404).end();
  }).listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(async () => { await new Promise((resolve) => server.close(resolve)); rmSync(directory, { recursive: true, force: true }); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const request = (path, init = {}) => fetch(base + path, { redirect: "manual", ...init });
  const client = await (await request("/browser/oauth/register", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ client_name: "ChatGPT", redirect_uris: [REDIRECT] }) })).json();
  const query = new URLSearchParams({ client_id: client.client_id, redirect_uri: REDIRECT, response_type: "code", resource: RESOURCE, code_challenge: hash(VERIFIER), code_challenge_method: "S256", scope: "browser", state: "client-state" });
  const start = await request(`/browser/oauth/authorize?${query}`);
  assert.equal(start.status, 200);
  const cookie = start.headers.get("set-cookie").split(";")[0];
  const html = await start.text();
  assert.match(html, /name="password"/);
  assert.doesNotMatch(html, /github\.com/i);
  const transaction = html.match(/name="transaction" value="([^"]+)"/)[1];
  const submit = (password) => request("/browser/oauth/authorize", { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded", cookie }, body: new URLSearchParams({ transaction, password }) });
  assert.equal((await submit("wrong-token")).status, 403);
  const granted = await submit(SHORT_TOKEN);
  assert.equal(granted.status, 302);
  const location = new URL(granted.headers.get("location"));
  assert.equal(location.origin, "https://chatgpt.com");
  assert.ok(location.searchParams.get("code"));
  assert.equal(location.searchParams.get("state"), "client-state");
});

[executed on device: rdc-22706bfa6066-00001-deployment-68b9cf96cc-r22sb (bf88308d-eebc-4fb0-98e4-96542cf47cb8)]