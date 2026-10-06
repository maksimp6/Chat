import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const PUBLIC = "https://alice-dev.example.test";
const REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect";
const SHORT = "synthetic-test-token";
const VERIFIER = "a".repeat(43);
const hash = (value) => createHash("sha256").update(value).digest("base64url");

test("SIGKILL and clean filesystems preserve DCR, consent, code, tokens and replay revocation", { timeout: 30000 }, async (t) => {
  const root = await mkdtemp(join(tmpdir(), "oauth-restart-"));
  const stateDir = join(root, "durable");
  await mkdir(stateDir);
  await writeFile(join(stateDir, "owner.json"), JSON.stringify({ schema: 1, purpose: "alice-dev-oauth", origin: PUBLIC, owner_id: "owner" }));
  let child;
  let base;
  let revision = 0;
  const stop = async () => {
    if (child && child.exitCode === null && child.signalCode === null) {
      const exited = once(child, "exit");
      child.kill("SIGKILL");
      await exited;
    }
  };
  t.after(async () => { await stop(); await rm(root, { recursive: true, force: true }); });
  async function restart() {
    await stop();
    const stateFile = join(root, `runtime-${++revision}`, "oauth.json");
    const code = `
      import { startGateway } from ${JSON.stringify(new URL("./mcp-gateway.mjs", import.meta.url).href)};
      import { createOAuthStateStore } from ${JSON.stringify(new URL("./oauth-state.mjs", import.meta.url).href)};
      const store = createOAuthStateStore(${JSON.stringify({ stateDir, stateFile, origin: PUBLIC, ownerId: "owner", requireMount: false })});
      const gateway = await startGateway({
        token: ${JSON.stringify(SHORT)}, publicUrl: ${JSON.stringify(PUBLIC)}, host: "127.0.0.1", port: 0,
        oauthStateFile: ${JSON.stringify(stateFile)}, oauthStateStore: store,
        credentialFile: ${JSON.stringify(join(root, `runtime-${revision}`, "credentials.json"))},
        upstreamTransport: {}, upstream: {
          async connect() {}, async close() {},
          async listTools() { return { tools: [{ name: "get_config", inputSchema: { type: "object" } }] }; },
          async callTool() { return { content: [{ type: "text", text: "synthetic-upstream-ok" }] }; },
        },
      });
      process.send({ port: gateway.server.address().port });
    `;
    child = spawn(process.execPath, ["--input-type=module", "-e", code], { stdio: ["ignore", "ignore", "ignore", "ipc"] });
    const port = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("child_start_timeout")), 5000);
      child.once("message", (message) => { clearTimeout(timer); resolve(message.port); });
      child.once("error", () => { clearTimeout(timer); reject(new Error("child_start_failed")); });
      child.once("exit", () => { clearTimeout(timer); reject(new Error("child_start_failed")); });
    });
    base = `http://127.0.0.1:${port}`;
  }
  const request = (path, options = {}) => fetch(base + path, { redirect: "manual", signal: AbortSignal.timeout(3000), ...options });
  const form = (path, values, cookie) => request(path, {
    method: "POST", headers: { "content-type": "application/x-www-form-urlencoded", ...(cookie ? { cookie } : {}) },
    body: new URLSearchParams(values),
  });
  const mcp = async (token) => {
    const client = new Client({ name: "durability-test", version: "1" }, { capabilities: {} });
    try {
      await client.connect(new StreamableHTTPClientTransport(new URL(`${base}/mcp`), { requestInit: { headers: { Authorization: `Bearer ${token}` } } }));
      assert.equal((await client.listTools()).tools[0].name, "get_config");
      assert.equal((await client.callTool({ name: "get_config", arguments: {} })).content[0].text, "synthetic-upstream-ok");
    } finally { await client.close(); }
  };

  await restart();
  const registration = await request("/oauth/register", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ redirect_uris: [REDIRECT], scope: "alice-dev offline_access" }),
  });
  assert.equal(registration.status, 201);
  const { client_id } = await registration.json();
  await restart();
  const consent = await request(`/oauth/authorize?${new URLSearchParams({
    client_id, redirect_uri: REDIRECT, response_type: "code", resource: `${PUBLIC}/mcp`,
    scope: "alice-dev offline_access", code_challenge_method: "S256", code_challenge: hash(VERIFIER),
  })}`);
  assert.equal(consent.status, 200);
  const cookie = consent.headers.get("set-cookie").split(";")[0];
  const transaction = (await consent.text()).match(/name="transaction" value="([^"]+)"/)[1];
  await restart();
  const approved = await form("/oauth/authorize", { transaction, password: SHORT }, cookie);
  assert.equal(approved.status, 302);
  const code = new URL(approved.headers.get("location")).searchParams.get("code");
  await restart();
  const exchange = await form("/oauth/token", { grant_type: "authorization_code", client_id, code, code_verifier: VERIFIER, redirect_uri: REDIRECT, resource: `${PUBLIC}/mcp` });
  assert.equal(exchange.status, 200);
  const first = await exchange.json();
  await mcp(first.access_token);
  await restart();
  await mcp(first.access_token);
  const refreshed = await form("/oauth/token", { grant_type: "refresh_token", client_id, refresh_token: first.refresh_token, resource: `${PUBLIC}/mcp` });
  assert.equal(refreshed.status, 200);
  const second = await refreshed.json();
  await restart();
  await mcp(second.access_token);
  const replay = await form("/oauth/token", { grant_type: "refresh_token", client_id, refresh_token: first.refresh_token, resource: `${PUBLIC}/mcp` });
  assert.equal(replay.status, 400);
  assert.equal((await replay.json()).error, "invalid_grant");
  await restart();
  const denied = await request("/mcp", { method: "POST", headers: { Authorization: `Bearer ${second.access_token}` }, body: "{}" });
  assert.equal(denied.status, 401);
  const revoked = await form("/oauth/token", { grant_type: "refresh_token", client_id, refresh_token: second.refresh_token, resource: `${PUBLIC}/mcp` });
  assert.equal(revoked.status, 400);
  assert.equal((await revoked.json()).error, "invalid_grant");
  assert.equal(revision, 7);
});
