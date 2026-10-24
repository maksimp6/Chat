import assert from "node:assert/strict";
import { once } from "node:events";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createServer, request as httpRequest } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createOAuth } from "./oauth.mjs";
import { startGateway } from "./mcp-gateway.mjs";

const PUBLIC = "https://alice-dev.example.test";
const REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect";
const registration = JSON.stringify({ redirect_uris: [REDIRECT] });

async function fixture(t, options = {}) {
  const directory = mkdtempSync(join(tmpdir(), "alice-oauth-intake-"));
  const stateFile = join(directory, "oauth.json");
  const oauth = createOAuth({ publicUrl: PUBLIC, shortToken: "synthetic-owner-token",
    stateFile, bodyTimeoutMs: 2000, ...options });
  const server = createServer(async (request, response) => {
    if (await oauth.handle(request, response, new URL(request.url, PUBLIC))) return;
    response.writeHead(401).end();
  }).listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(async () => {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    rmSync(directory, { recursive: true, force: true });
  });
  const base = `http://127.0.0.1:${server.address().port}`;
  const request = (path, init = {}) => fetch(base + path, {
    redirect: "manual", signal: AbortSignal.timeout(500), ...init });
  const register = () => request("/oauth/register", { method: "POST",
    headers: { "content-type": "application/json" }, body: registration });
  async function incomplete(path, type) {
    const received = once(server, "request");
    const request = httpRequest(base + path, { method: "POST",
      headers: { "content-type": type, "content-length": 1000 } });
    request.on("error", () => {});
    t.after(() => request.destroy());
    request.write(type === "application/json" ? "{" : "password=");
    await received;
    return request;
  }
  return { request, register, incomplete, stateFile, oauth, base };
}

for (const [path, type, body] of [
  ["register", "text/plain", registration],
  ["register", "application/json", "[]"],
  ["register", "application/json", "{"],
  ["register", "application/json", "x".repeat(16 * 1024 + 1)],
  ["token", "application/x-www-form-urlencoded", "client_id=one&client_id=two"],
]) {
  test(`invalid ${path} input (${type}, ${body.length} bytes) never reaches persistence`, async (t) => {
    let writes = 0;
    const { request, register } = await fixture(t, { onPersist: async () => { writes++; } });
    const response = await request(`/oauth/${path}`, { method: "POST",
      headers: { "content-type": type }, body });
    assert.equal(response.status, 400);
    assert.deepEqual(await response.json(), { error: "invalid_request" });
    assert.equal(writes, 0);
    assert.equal((await register()).status, 201);
    assert.equal(writes, 1);
  });
}

for (const path of ["register", "authorize", "owner-session", "token", "revoke"]) {
  test(`incomplete /oauth/${path} does not block discovery, DCR or MCP`, async (t) => {
    const { request, register, incomplete } = await fixture(t);
    await incomplete(`/oauth/${path}`, path === "register"
      ? "application/json" : "application/x-www-form-urlencoded");
    const responses = await Promise.all([
      request("/.well-known/oauth-authorization-server/oauth"),
      register(), request("/mcp", { method: "POST", body: "{}" }),
    ]);
    assert.deepEqual(responses.map((response) => response.status), [200, 201, 401]);
    await Promise.all(responses.map((response) => response.text()));
  });
}

test("intake deadline rejects an unfinished body and closes its connection", async (t) => {
  const { incomplete, register } = await fixture(t, { bodyTimeoutMs: 40 });
  const request = await incomplete("/oauth/register", "application/json");
  const response = await Promise.race([
    once(request, "response").then(([response]) => response),
    new Promise((_, reject) => {
      const timer = setTimeout(() => reject(new Error("intake_deadline_missing")), 500);
      t.after(() => clearTimeout(timer));
    }),
  ]);
  assert.equal(response.statusCode, 400);
  assert.equal(response.headers.connection, "close");
  let body = "";
  for await (const chunk of response) body += chunk;
  assert.deepEqual(JSON.parse(body), { error: "invalid_request" });
  assert.equal((await register()).status, 201);
});

test("an aborted intake neither persists partial state nor poisons later requests", async (t) => {
  let writes = 0;
  const { incomplete, register, stateFile, oauth } = await fixture(t, {
    onPersist: async () => { writes++; },
  });
  const partial = await incomplete("/oauth/register", "application/json");
  partial.destroy();
  const response = await register();
  assert.equal(response.status, 201);
  assert.equal(writes, 1);
  assert.equal(Object.keys(JSON.parse(readFileSync(stateFile, "utf8")).clients).length, 1);
  assert.equal(oauth.available(), true);
});

test("only mutations wait for a durable checkpoint; commits remain serial", async (t) => {
  let release;
  const blocked = new Promise((resolve) => { release = resolve; });
  t.after(() => release());
  let started;
  const checkpoint = new Promise((resolve) => { started = resolve; });
  let writes = 0;
  let active = 0;
  let maxActive = 0;
  const { request, register, stateFile } = await fixture(t, {
    onPersist: async () => {
      maxActive = Math.max(maxActive, ++active);
      if (++writes === 1) { started(); await blocked; }
      active--;
    },
  });
  const first = register();
  await checkpoint;
  let secondCompleted = false;
  const second = register().then((response) => { secondCompleted = true; return response; });
  const reads = await Promise.all([
    request("/.well-known/oauth-protected-resource/mcp"),
    request("/mcp", { method: "POST", body: "{}" }), request("/oauth/unknown"),
  ]);
  assert.deepEqual(reads.map((response) => response.status), [200, 401, 404]);
  await Promise.all(reads.map((response) => response.text()));
  assert.equal(secondCompleted, false);
  assert.equal(writes, 1);
  release();
  assert.deepEqual((await Promise.all([first, second])).map((response) => response.status), [201, 201]);
  assert.equal(maxActive, 1);
  assert.equal(writes, 2);
  assert.equal(Object.keys(JSON.parse(readFileSync(stateFile, "utf8")).clients).length, 2);
});

test("a disconnected complete request waiting for admission cannot mutate state", async (t) => {
  let release;
  const blocked = new Promise((resolve) => { release = resolve; });
  t.after(() => release());
  let started;
  const checkpoint = new Promise((resolve) => { started = resolve; });
  let writes = 0;
  const { register, base, stateFile } = await fixture(t, {
    onPersist: async () => { if (++writes === 1) { started(); await blocked; } },
  });
  const first = register();
  await checkpoint;
  const request = httpRequest(base + "/oauth/register", { method: "POST",
    headers: { "content-type": "application/json" } });
  request.on("error", () => {});
  request.end(registration);
  await once(request, "finish");
  // Let the server finish intake and wait on the occupied mutation queue.
  await new Promise((resolve) => setTimeout(resolve, 20));
  request.destroy();
  await new Promise((resolve) => request.once("close", resolve));
  await new Promise((resolve) => setTimeout(resolve, 20));
  release();
  assert.equal((await first).status, 201);
  const third = await register();
  assert.equal(third.status, 201);
  assert.equal(writes, 2);
  assert.equal(Object.keys(JSON.parse(readFileSync(stateFile, "utf8")).clients).length, 2);
});

test("the real gateway serves OAuth discovery, health and authorized MCP during a slow upload", async (t) => {
  const directory = mkdtempSync(join(tmpdir(), "alice-gateway-intake-"));
  const gateway = await startGateway({ token: "synthetic-owner-token", publicUrl: PUBLIC,
    oauthStateFile: join(directory, "oauth.json"), credentialFile: join(directory, "credentials.json"),
    host: "127.0.0.1", port: 0,
    upstream: { async connect() {}, async close() {} }, upstreamTransport: {} });
  t.after(async () => {
    gateway.server.closeAllConnections();
    await gateway.close();
    rmSync(directory, { recursive: true, force: true });
  });
  const base = `http://127.0.0.1:${gateway.server.address().port}`;
  const received = once(gateway.server, "request");
  const partial = httpRequest(base + "/oauth/register", { method: "POST",
    headers: { "content-type": "application/json", "content-length": 1000 } });
  partial.on("error", () => {});
  t.after(() => partial.destroy());
  partial.write("{");
  await received;
  const responses = await Promise.all([
    fetch(base + "/healthz", { signal: AbortSignal.timeout(500) }),
    fetch(base + "/.well-known/oauth-authorization-server/oauth", { signal: AbortSignal.timeout(500) }),
    fetch(base + "/mcp", { method: "POST", signal: AbortSignal.timeout(500),
      headers: { authorization: "Bearer synthetic-owner-token", "content-type": "application/json",
        accept: "application/json, text/event-stream" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize",
        params: { protocolVersion: "2025-11-25", capabilities: {},
          clientInfo: { name: "synthetic-intake-test", version: "1" } } }) }),
  ]);
  assert.deepEqual(responses.map((response) => response.status), [200, 200, 200]);
  await responses[0].text();
  assert.equal((await responses[1].json()).issuer, PUBLIC + "/oauth");
  assert.match(await responses[2].text(), /protocolVersion/);
});
