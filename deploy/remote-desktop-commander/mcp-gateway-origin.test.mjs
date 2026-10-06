import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";
import { startGateway } from "./mcp-gateway.mjs";

const PUBLIC = "https://alice-dev.example.invalid";

async function options(t) {
  const root = await mkdtemp(join(tmpdir(), "alice-dev-origin-test-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  return {
    token: "synthetic-test-token",
    publicUrl: PUBLIC,
    host: "127.0.0.1",
    port: 0,
    oauthStateFile: join(root, "oauth.json"),
    credentialFile: join(root, "credentials.json"),
    upstream: { async connect() {}, async close() {} },
    upstreamTransport: {},
  };
}

for (const publicUrl of ["", "http://service.internal:8080", "https://x.invalid/path", "https://x.invalid/?q=1", "https://x.invalid/#fragment", "https://user:pass@x.invalid/"]) {
  test(`invalid public origin fails before opening the listener: ${publicUrl}`, async (t) => {
    const opts = { ...await options(t), publicUrl };
    let opened;
    await assert.rejects(async () => {
      opened = await startGateway(opts);
      await opened.close();
    }, /public.*url|public.*origin/i);
  });
}

for (const headers of [
  { host: "service.internal:8080", "x-forwarded-proto": "http", "x-forwarded-host": "" },
  { host: "service.internal:8080", "x-forwarded-proto": "", "x-forwarded-host": "" },
  { host: "service.internal:8080", "x-forwarded-proto": "https", "x-forwarded-host": "attacker.example.invalid" },
]) {
  test(`configured OAuth origin survives proxy headers ${JSON.stringify(headers)}`, async (t) => {
    const gateway = await startGateway(await options(t));
    t.after(() => gateway.close());
    const local = `http://127.0.0.1:${gateway.server.address().port}`;
    const response = await fetch(`${local}/mcp`, { method: "POST", headers,
      body: "{}", signal: AbortSignal.timeout(3000) });
    assert.equal(response.status, 401);
    assert.match(response.headers.get("www-authenticate"), /resource_metadata=/);
    assert.ok(response.headers.get("www-authenticate").includes(`${PUBLIC}/.well-known/`));
    await response.text();
    const metadata = await fetch(`${local}/.well-known/oauth-authorization-server/oauth`, { headers });
    assert.equal(metadata.status, 200);
    assert.equal((await metadata.json()).issuer, `${PUBLIC}/oauth`);
    const health = await fetch(`${local}/healthz`);
    assert.equal(health.status, 200);
    await health.text();
  });
}

// Regression from #939: a GET/HEAD probe must not look like a missing MCP route.
for (const method of ["GET", "HEAD", "POST"]) {
  for (const authorization of [undefined, "Bearer invalid-test-token"]) {
    test(`${method} MCP probe exposes auth challenge before dispatch`, async (t) => {
      const gateway = await startGateway(await options(t));
      t.after(() => gateway.close());
      const local = `http://127.0.0.1:${gateway.server.address().port}`;
      const response = await fetch(`${local}/mcp`, {
        method,
        headers: {
          accept: "application/json, text/event-stream",
          ...(authorization ? { authorization } : {}),
        },
        signal: AbortSignal.timeout(3000),
      });
      assert.equal(response.status, 401);
      assert.equal(response.headers.get("cache-control"), "no-store");
      assert.ok(response.headers.get("www-authenticate").includes(`${PUBLIC}/.well-known/oauth-protected-resource/mcp`));
      const text = await response.text();
      if (method === "HEAD") assert.equal(text, "");
      else assert.equal(JSON.parse(text).status, "unauthorized");
    });
  }
}

for (const method of ["GET", "HEAD", "DELETE", "PUT"]) {
  test(`authenticated unsupported ${method} is 405, not a missing route`, async (t) => {
    const gateway = await startGateway(await options(t));
    t.after(() => gateway.close());
    const local = `http://127.0.0.1:${gateway.server.address().port}`;
    const response = await fetch(`${local}/mcp`, {
      method,
      headers: { authorization: "Bearer synthetic-test-token", accept: "text/event-stream" },
      signal: AbortSignal.timeout(3000),
    });
    assert.equal(response.status, 405);
    assert.equal(response.headers.get("allow"), "POST");
    const text = await response.text();
    if (method === "HEAD") assert.equal(text, "");
    else assert.equal(JSON.parse(text).status, "method_not_allowed");
  });
}

test("unknown paths remain 404 and do not advertise a different MCP endpoint", async (t) => {
  const gateway = await startGateway(await options(t));
  t.after(() => gateway.close());
  const response = await fetch(`http://127.0.0.1:${gateway.server.address().port}/not-mcp`, {
    signal: AbortSignal.timeout(3000),
  });
  assert.equal(response.status, 404);
  assert.equal(response.headers.get("www-authenticate"), null);
  await response.text();
});

test("authenticated POST still negotiates MCP and invokes the upstream tool", async (t) => {
  const { Client } = await import("@modelcontextprotocol/sdk/client/index.js");
  const { StreamableHTTPClientTransport } = await import("@modelcontextprotocol/sdk/client/streamableHttp.js");
  let called = 0;
  const opts = await options(t);
  opts.upstream = {
    async connect() {}, async close() {},
    async listTools() { return { tools: [{ name: "get_config", inputSchema: { type: "object" } }] }; },
    async callTool(request) {
      assert.equal(request.name, "get_config");
      called += 1;
      return { content: [{ type: "text", text: "synthetic-config" }] };
    },
  };
  const gateway = await startGateway(opts);
  t.after(() => gateway.close());
  const client = new Client({ name: "method-regression-client", version: "1.0" });
  t.after(() => client.close());
  const transport = new StreamableHTTPClientTransport(
    new URL(`http://127.0.0.1:${gateway.server.address().port}/mcp`),
    { requestInit: { headers: { authorization: "Bearer synthetic-test-token" } } },
  );
  await client.connect(transport);
  assert.deepEqual((await client.listTools()).tools.map(tool => tool.name), ["get_config"]);
  const result = await client.callTool({ name: "get_config", arguments: {} });
  assert.equal(result.content[0].text, "synthetic-config");
  assert.equal(called, 1);
});
