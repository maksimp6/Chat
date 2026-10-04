import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { createOAuth } from "./oauth.mjs";

const hash = (value) => createHash("sha256").update(value).digest("base64url");
const PUBLIC = "https://browser.example.test";
const RESOURCE = `${PUBLIC}/browser/v1/mcp`;
const REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect";
const VERIFIER = "v".repeat(43);

async function fixture(t, overrides = {}) {
  const directory = mkdtempSync(join(tmpdir(), "browser-oauth-"));
  let clock = 1_800_000_000_000;
  let owner = "12345";
  let checkpoints = 0;
  const upstream = [];
  const options = {
    env: {}, publicUrl: PUBLIC, githubClientId: "synthetic-client", githubClientSecret: "synthetic-secret",
    ownerId: "12345", stateFile: join(directory, "oauth.json"), now: () => clock,
    onPersist: async () => { checkpoints += 1; },
    fetch: async (url, init) => {
      upstream.push({ url, init });
      if (url.endsWith("/access_token")) return Response.json({ access_token: "synthetic-upstream-token" });
      if (url === "https://api.github.com/user") return Response.json({ id: owner, login: "owner" });
      throw new Error("unexpected_upstream");
    },
    ...overrides,
  };
  if (overrides.realState) options.stateFile = join(directory, "auth", "oauth.json");
  let oauth = createOAuth(options);
  let worker;
  if (overrides.integration) {
    const { createWorker } = await import("./server.mjs");
    let stateStore = { restore: async () => {}, checkpoint: async () => {}, checkpointAuth: options.onPersist, status: () => ({}) };
    if (overrides.realState) {
      const { createChromeStateStore } = await import("./state.mjs");
      mkdirSync(join(directory, "durable"));
      stateStore = createChromeStateStore({ profileDir: join(directory, "profile"), stateDir: join(directory, "durable"), authDir: join(directory, "auth") });
    }
    worker = createWorker({
      token: "machine-only-token", profileDir: join(directory, "profile"), oauth: options,
      stateStore,
    });
    await worker.ready;
  }
  const server = createServer(async (request, response) => {
    if (worker) return worker.handler(request, response);
    if (await oauth.handle(request, response, new URL(request.url, PUBLIC))) return;
    response.writeHead(oauth.authorize(request) ? 200 : 401, { "www-authenticate": oauth.challenge() });
    response.end();
  }).listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(async () => { await worker?.close(); await new Promise((resolve) => server.close(resolve)); rmSync(directory, { recursive: true, force: true }); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const request = (path, init = {}) => fetch(base + path, { redirect: "manual", ...init });
  const form = (path, values, cookie) => request(path, {
    method: "POST", headers: { "content-type": "application/x-www-form-urlencoded", ...(cookie ? { cookie } : {}) }, body: new URLSearchParams(values),
  });
  const register = (values = {}) => request("/browser/oauth/register", {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ client_name: "ChatGPT", redirect_uris: [REDIRECT], ...values }),
  });
  async function start(client, values = {}) {
    const query = new URLSearchParams({ client_id: client.client_id, redirect_uri: REDIRECT, response_type: "code", resource: RESOURCE, code_challenge: hash(VERIFIER), code_challenge_method: "S256", scope: "browser", state: "client-state", ...values });
    const response = await request(`/browser/oauth/authorize?${query}`);
    const cookie = response.headers.get("set-cookie")?.split(";")[0];
    const html = await response.text();
    const transaction = html.match(/name="transaction" value="([^"]+)"/)?.[1];
    return { response, cookie, transaction, html };
  }
  async function code(client = null) {
    client ??= await (await register()).json();
    const started = await start(client);
    assert.equal(started.response.status, 200);
    const login = await form("/browser/oauth/authorize", { transaction: started.transaction }, started.cookie);
    assert.equal(login.status, 302);
    const github = new URL(login.headers.get("location"));
    assert.equal(github.origin, "https://github.com");
    const callback = await request(`/browser/oauth/github/callback?${new URLSearchParams({ state: github.searchParams.get("state"), code: "synthetic-code" })}`, { headers: { cookie: started.cookie } });
    return { client, started, github, callback, location: new URL(callback.headers.get("location")) };
  }
  const exchange = (flow, values = {}) => form("/browser/oauth/token", {
    grant_type: "authorization_code", client_id: flow.client.client_id, redirect_uri: REDIRECT, code: flow.location.searchParams.get("code"), code_verifier: VERIFIER, resource: RESOURCE, ...values,
  });
  return { options, base, request, form, register, start, code, exchange, upstream, reload() { oauth = createOAuth(options); }, advance(seconds) { clock += seconds * 1000; }, setOwner(id) { owner = id; }, checkpoints: () => checkpoints };
}

test("OAuth remains disabled before first ingress configuration, and rejects ambiguous owners", () => {
  assert.equal(createOAuth({ env: {} }).enabled, false);
  assert.equal(createOAuth({ env: {}, publicUrl: PUBLIC, githubClientId: "id", githubClientSecret: "secret", ownerId: "123,456" }).enabled, false);
});

test("MCP discovery advertises the exact resource, issuer, PKCE and DCR endpoints", async (t) => {
  const app = await fixture(t);
  const resource = await (await app.request("/.well-known/oauth-protected-resource/browser/v1/mcp")).json();
  assert.equal(resource.resource, RESOURCE);
  assert.deepEqual(resource.authorization_servers, [`${PUBLIC}/browser/oauth`]);
  const metadata = await (await app.request("/.well-known/oauth-authorization-server/browser/oauth")).json();
  assert.deepEqual(metadata.code_challenge_methods_supported, ["S256"]);
  assert.deepEqual(metadata.token_endpoint_auth_methods_supported, ["none"]);
  assert.equal(metadata.authorization_response_iss_parameter_supported, true);
  const protectedResponse = await app.request("/browser/v1/mcp");
  assert.equal(protectedResponse.status, 401);
  assert.match(protectedResponse.headers.get("www-authenticate"), /oauth-protected-resource\/browser\/v1\/mcp/);
});

test("GitHub owner login issues durable tokens; token refresh works after a restart", async (t) => {
  const app = await fixture(t);
  const flow = await app.code();
  assert.equal(flow.location.origin, "https://chatgpt.com");
  assert.equal(flow.location.searchParams.get("state"), "client-state");
  assert.equal(flow.location.searchParams.get("iss"), `${PUBLIC}/browser/oauth`);
  assert.equal(flow.github.searchParams.get("redirect_uri"), `${PUBLIC}/browser/oauth/github/callback`);
  const githubExchange = app.upstream[0].init.body;
  assert.equal(hash(githubExchange.get("code_verifier")), flow.github.searchParams.get("code_challenge"));
  const response = await app.exchange(flow);
  assert.equal(response.status, 200);
  const tokens = await response.json();
  assert.equal((await app.request("/browser/v1/mcp", { headers: { authorization: `Bearer ${tokens.access_token}` } })).status, 200);
  assert.ok(app.checkpoints() >= 5);
  assert.equal(statSync(app.options.stateFile).mode & 0o777, 0o600);
  const disk = readFileSync(app.options.stateFile, "utf8");
  for (const secret of [tokens.access_token, tokens.refresh_token, "synthetic-upstream-token", "synthetic-secret"]) assert.equal(disk.includes(secret), false);
  app.reload();
  assert.equal((await app.request("/browser/v1/mcp", { headers: { authorization: `Bearer ${tokens.access_token}` } })).status, 200);
  const refreshed = await app.form("/browser/oauth/token", { grant_type: "refresh_token", client_id: flow.client.client_id, refresh_token: tokens.refresh_token, resource: RESOURCE });
  assert.equal(refreshed.status, 200);
  assert.notEqual((await refreshed.json()).refresh_token, tokens.refresh_token);
});

test("registered redirect, S256, resource and browser cookie prevent login/code substitution", async (t) => {
  const app = await fixture(t);
  for (const redirect of ["http://external.test/callback", "https://user:password@example.test/callback", "https://example.test/callback#fragment", "javascript:alert(1)"]) {
    assert.equal((await app.register({ redirect_uris: [redirect] })).status, 400);
  }
  assert.equal((await app.register({ redirect_uris: ["http://127.0.0.1:9000/callback"] })).status, 201);
  const client = await (await app.register()).json();
  for (const values of [{ redirect_uri: "https://attacker.test/callback" }, { code_challenge_method: "plain" }, { resource: "https://other.test/mcp" }]) assert.equal((await app.start(client, values)).response.status, 400);
  const started = await app.start(client);
  assert.equal((await app.form("/browser/oauth/authorize", { transaction: started.transaction })).status, 400);
  assert.equal((await app.form("/browser/oauth/authorize", { transaction: started.transaction }, "browser_oauth_transaction=short")).status, 400);
  const login = await app.form("/browser/oauth/authorize", { transaction: started.transaction }, started.cookie);
  assert.equal(login.status, 302);
  const callbackPath = `/browser/oauth/github/callback?state=${started.transaction}&code=synthetic-code`;
  assert.equal((await app.request(callbackPath)).status, 400);
  assert.equal(app.upstream.length, 0);
  const callback = await app.request(callbackPath, { headers: { cookie: started.cookie } });
  const flow = { client, location: new URL(callback.headers.get("location")) };
  assert.equal((await app.exchange(flow, { code_verifier: "x".repeat(43) })).status, 400);
  assert.equal((await app.exchange(flow, { resource: "https://other.test/mcp" })).status, 400);
  assert.equal((await app.exchange(flow)).status, 200);
  assert.equal((await app.request(callbackPath, { headers: { cookie: started.cookie } })).status, 400);
});

test("only the immutable configured GitHub owner can obtain a code", async (t) => {
  const app = await fixture(t);
  app.setOwner("9");
  const flow = await app.code();
  assert.equal(flow.location.searchParams.get("code"), null);
  assert.equal(flow.location.searchParams.get("error"), "access_denied");
  assert.equal(flow.location.searchParams.get("iss"), `${PUBLIC}/browser/oauth`);
});

test("authorization code replay revokes its token family", async (t) => {
  const app = await fixture(t);
  const flow = await app.code();
  const tokens = await (await app.exchange(flow)).json();
  app.reload();
  assert.equal((await app.exchange(flow)).status, 400);
  assert.equal((await app.request("/browser/v1/mcp", { headers: { authorization: `Bearer ${tokens.access_token}` } })).status, 401);
});

test("refresh rotation detects reuse across restarts and revokes all descendants", async (t) => {
  const app = await fixture(t);
  const flow = await app.code();
  const tokens = await (await app.exchange(flow)).json();
  const refresh = (token) => app.form("/browser/oauth/token", { grant_type: "refresh_token", client_id: flow.client.client_id, refresh_token: token, resource: RESOURCE });
  const next = await (await refresh(tokens.refresh_token)).json();
  app.reload();
  assert.equal((await refresh(tokens.refresh_token)).status, 400);
  assert.equal((await refresh(next.refresh_token)).status, 400);
  assert.equal((await app.request("/browser/v1/mcp", { headers: { authorization: `Bearer ${next.access_token}` } })).status, 401);
});

test("access expiry, refresh expiry and explicit revocation are enforced", async (t) => {
  const app = await fixture(t);
  const flow = await app.code();
  const tokens = await (await app.exchange(flow)).json();
  app.advance(901);
  assert.equal((await app.request("/browser/v1/mcp", { headers: { authorization: `Bearer ${tokens.access_token}` } })).status, 401);
  const next = await (await app.form("/browser/oauth/token", { grant_type: "refresh_token", client_id: flow.client.client_id, refresh_token: tokens.refresh_token, resource: RESOURCE })).json();
  await app.form("/browser/oauth/revoke", { client_id: flow.client.client_id, token: next.refresh_token });
  assert.equal((await app.request("/browser/v1/mcp", { headers: { authorization: `Bearer ${next.access_token}` } })).status, 401);
  const flow2 = await app.code();
  const tokens2 = await (await app.exchange(flow2)).json();
  app.advance(31 * 24 * 3600);
  assert.equal((await app.form("/browser/oauth/token", { grant_type: "refresh_token", client_id: flow2.client.client_id, refresh_token: tokens2.refresh_token, resource: RESOURCE })).status, 400);
});

test("a failed durable checkpoint never returns issued credentials", async (t) => {
  let fail = false;
  const app = await fixture(t, { onPersist: async () => { if (fail) throw new Error("synthetic storage failure with secret"); } });
  const flow = await app.code();
  fail = true;
  const result = await app.exchange(flow);
  assert.equal(result.status, 500);
  assert.deepEqual(await result.json(), { error: "oauth_unavailable" });
});

test("anonymous registration throttles recover and expired clients/pending requests cannot fill capacity permanently", async (t) => {
  const app = await fixture(t);
  for (let index = 0; index < 30; index += 1) assert.equal((await app.register()).status, 201);
  assert.equal((await app.register()).status, 429);
  app.advance(61);
  assert.equal((await app.register()).status, 201);
  const state = JSON.parse(readFileSync(app.options.stateFile, "utf8"));
  for (let index = 0; index < 1000; index += 1) {
    state.clients[`expired-${index}`] = { provisional_expires: 1 };
    state.pending[`expired-${index}`] = { expires: 1 };
  }
  writeFileSync(app.options.stateFile, JSON.stringify(state));
  app.reload();
  const registration = await app.register();
  assert.equal(registration.status, 201);
  assert.equal((await app.start(await registration.json())).response.status, 200);
  const flow = await app.code();
  const tokens = await (await app.exchange(flow)).json();
  app.advance(3600);
  app.reload();
  assert.equal((await app.form("/browser/oauth/token", { grant_type: "refresh_token", client_id: flow.client.client_id, refresh_token: tokens.refresh_token, resource: RESOURCE })).status, 200);
});

test("owner OAuth drives official Playwright MCP in the real worker but grants no lifecycle REST access", { skip: process.env.BROWSER_OAUTH_MCP_TEST !== "1" }, async (t) => {
  const { Client } = await import("@modelcontextprotocol/sdk/client/index.js");
  const { StreamableHTTPClientTransport } = await import("@modelcontextprotocol/sdk/client/streamableHttp.js");
  const app = await fixture(t, { integration: true });
  const flow = await app.code();
  const tokens = await (await app.exchange(flow)).json();
  const headers = { authorization: `Bearer ${tokens.access_token}` };
  assert.equal((await app.request("/browser/v1/status", { headers })).status, 401);
  assert.equal((await app.request("/browser/v1/sleep", { method: "POST", headers })).status, 401);
  assert.equal((await app.request("/browser/v1/status", { headers: { authorization: "Bearer machine-only-token" } })).status, 200);
  const client = new Client({ name: "oauth-browser-test", version: "1.0.0" });
  const transport = new StreamableHTTPClientTransport(new URL(`${app.base}/browser/v1/mcp`), { requestInit: { headers } });
  t.after(() => client.close());
  await client.connect(transport);
  assert.ok((await client.listTools()).tools.some((tool) => tool.name === "browser_navigate"));
  const site = createServer((request, response) => { response.setHeader("content-type", "text/html"); response.end("<title>Owner OAuth browser</title><h1>Browser connected</h1>"); }).listen(0, "127.0.0.1");
  await once(site, "listening");
  t.after(() => new Promise((resolve) => site.close(resolve)));
  const result = await client.callTool({ name: "browser_navigate", arguments: { url: `http://127.0.0.1:${site.address().port}/` } });
  assert.notEqual(result.isError, true, JSON.stringify(result));
  assert.match(JSON.stringify(await client.callTool({ name: "browser_snapshot", arguments: {} })), /Browser connected/);
  await transport.terminateSession();
});

test("concurrent real worker OAuth registrations checkpoint complete immutable state", async (t) => {
  const app = await fixture(t, { integration: true, realState: true });
  const results = await Promise.all(Array.from({ length: 20 }, () => app.register()));
  assert.deepEqual(results.map((response) => response.status), Array(20).fill(201));
  const clients = await Promise.all(results.map((response) => response.json()));
  const { createChromeStateStore } = await import("./state.mjs");
  const root = dirname(dirname(app.options.stateFile));
  const restoredAuth = join(root, "restored-auth");
  const restored = createChromeStateStore({ stateDir: join(root, "durable"), profileDir: join(root, "restored-profile"), authDir: restoredAuth });
  await restored.restore();
  const disk = JSON.parse(readFileSync(join(restoredAuth, "oauth.json"), "utf8"));
  for (const client of clients) assert.equal(disk.clients[client.client_id].client_id, client.client_id);
  assert.equal(restored.status().authGeneration, 20);
});

test("consent page CSP lets the form redirect to GitHub sign-in", async (t) => {
  // Browsers enforce form-action on the redirect after submit; 'self' alone
  // silently blocks the GitHub redirect and the consent page appears to hang.
  const app = await fixture(t);
  const client = await (await app.register()).json();
  const started = await app.start(client);
  const policy = started.response.headers.get("content-security-policy");
  const formAction = policy.split(";").map((part) => part.trim()).find((part) => part.startsWith("form-action"));
  assert.deepEqual(formAction.split(/\s+/).slice(1).sort(), ["'self'", "https://github.com"]);
  const login = await app.form("/browser/oauth/authorize", { transaction: started.transaction }, started.cookie);
  assert.equal(new URL(login.headers.get("location")).origin, "https://github.com");
});

test("unconfirmed clients survive retries for days but not past a week", async (t) => {
  // ChatGPT reuses its registered client_id; it must still be valid after a
  // failed or interrupted first sign-in.
  const app = await fixture(t);
  const client = await (await app.register()).json();
  app.advance(3 * 24 * 3600);
  assert.equal((await app.start(client)).response.status, 200);
  app.advance(5 * 24 * 3600);
  assert.equal((await app.start(client)).response.status, 400);
});

test("a registration flood evicts the oldest unconfirmed client and never a confirmed one", async (t) => {
  const app = await fixture(t);
  const confirmed = await app.code();
  const state = JSON.parse(readFileSync(app.options.stateFile, "utf8"));
  assert.equal(state.clients[confirmed.client.client_id].provisional_expires, undefined);
  for (let index = 0; index < 999; index += 1) state.clients[`flood-${index}`] = { provisional_expires: 5_000_000_000 + index, redirect_uris: [] };
  writeFileSync(app.options.stateFile, JSON.stringify(state));
  app.reload();
  const fresh = await app.register();
  assert.equal(fresh.status, 201);
  const after = JSON.parse(readFileSync(app.options.stateFile, "utf8"));
  assert.ok(after.clients[confirmed.client.client_id], "confirmed client survives");
  assert.equal(after.clients["flood-0"], undefined, "oldest unconfirmed client was evicted");
  assert.ok(after.clients["flood-998"]);
  assert.equal(Object.keys(after.clients).length, 1000);
  // With only confirmed clients left there is nothing safe to evict.
  for (const key of Object.keys(after.clients)) delete after.clients[key].provisional_expires;
  writeFileSync(app.options.stateFile, JSON.stringify(after));
  app.reload();
  assert.equal((await app.register()).status, 429);
});
