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
