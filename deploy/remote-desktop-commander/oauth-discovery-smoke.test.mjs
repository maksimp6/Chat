import assert from "node:assert/strict";
import { createServer } from "node:http";
import { once } from "node:events";
import test from "node:test";
import { verifyOAuthDiscovery } from "./oauth-discovery-smoke.mjs";

const ORIGIN = "https://alice-dev.example.invalid";
const RESOURCE_PATH = "/.well-known/oauth-protected-resource/mcp";
const asJson = (body) => new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
function goodReply(url) {
  switch (new URL(url).pathname) {
    case "/healthz": return asJson({ status: "ok", mode: "mcp-gateway" });
    case "/mcp": return new Response(null, { status: 401, headers: {
      "www-authenticate": `Bearer resource_metadata="${ORIGIN}${RESOURCE_PATH}"`,
    } });
    case RESOURCE_PATH: return asJson({ resource: `${ORIGIN}/mcp`, authorization_servers: [`${ORIGIN}/oauth`], scopes_supported: ["alice-dev"] });
    default: return asJson({ issuer: `${ORIGIN}/oauth`, authorization_endpoint: `${ORIGIN}/oauth/authorize`,
      token_endpoint: `${ORIGIN}/oauth/token`, registration_endpoint: `${ORIGIN}/oauth/register`,
      code_challenge_methods_supported: ["S256"], grant_types_supported: ["authorization_code"] });
  }
}

test("complete discovery verifies the challenge and both metadata documents", async () => {
  const calls = [];
  const result = await verifyOAuthDiscovery(ORIGIN, { fetch: async (url, init) => {
    calls.push(new URL(url).pathname);
    assert.equal(init.redirect, "manual");
    assert.ok(init.signal);
    return goodReply(url);
  } });
  assert.equal(result.status, "OAUTH_DISCOVERY_OK");
  assert.deepEqual(calls, ["/healthz", "/mcp", RESOURCE_PATH, "/.well-known/oauth-authorization-server/oauth"]);
});

for (const status of [502, 503, 504]) {
  test(`cold-start ${status} retries without relaxing the required 401`, async () => {
    let cold = true;
    let tries = 0;
    await verifyOAuthDiscovery(ORIGIN, { retryDelayMs: 0, fetch: async (url) => {
      if (url.endsWith("/mcp")) {
        tries += 1;
        if (cold) { cold = false; return new Response(null, { status }); }
      }
      return goodReply(url);
    } });
    assert.equal(tries, 3); // MCP request twice, protected resource path once.
  });
}

for (const status of [200, 403, 404]) {
  test(`incorrect MCP HTTP ${status} fails immediately, never becomes a retry/pass`, async () => {
    let calls = 0;
    await assert.rejects(verifyOAuthDiscovery(ORIGIN, { fetch: async (url) => {
      calls += 1;
      return url.endsWith("/mcp") ? new Response(null, { status }) : goodReply(url);
    } }), /unexpected_http_status/);
    assert.equal(calls, 2);
  });
}

test("missing or foreign resource metadata is not followed", async () => {
  for (const challenge of ["Bearer", 'Bearer resource_metadata="https://foreign.invalid/data"']) {
    let calls = 0;
    await assert.rejects(verifyOAuthDiscovery(ORIGIN, { fetch: async (url) => {
      calls += 1;
      return url.endsWith("/mcp") ? new Response(null, { status: 401,
        headers: { "www-authenticate": challenge } }) : goodReply(url);
    } }), /invalid_resource_metadata_challenge/);
    assert.equal(calls, 2);
  }
});

test("a health 200 without the gateway contract does not pass", async () => {
  await assert.rejects(verifyOAuthDiscovery(ORIGIN, { fetch: async () => asJson({ status: "ok" }) }), /invalid_health/);
});

test("a hanging HTTP server is interrupted by the total deadline", async (t) => {
  const server = createServer((_request, _response) => {}).listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(async () => {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  });
  const started = performance.now();
  await assert.rejects(verifyOAuthDiscovery(`http://127.0.0.1:${server.address().port}`, {
    timeoutMs: 80, requestTimeoutMs: 20, retryDelayMs: 1,
  }), /deadline_exceeded/);
  assert.ok(performance.now() - started < 1500);
});

test("bad metadata fails without logging response content", async () => {
  const reports = [];
  await assert.rejects(verifyOAuthDiscovery(ORIGIN, { report: (r) => reports.push(r), fetch: async (url) => {
    if (new URL(url).pathname === RESOURCE_PATH) return asJson({ private_value: "must-not-be-logged" });
    return goodReply(url);
  } }), /invalid_protected_resource_metadata/);
  assert.ok(!JSON.stringify(reports).includes("must-not-be-logged"));
});

test("a hanging JSON response body shares the HTTP deadline", async (t) => {
  const server = createServer((_request, response) => {
    response.writeHead(200, { "content-type": "application/json" });
    response.write("{");
  }).listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(async () => {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  });
  await assert.rejects(verifyOAuthDiscovery(`http://127.0.0.1:${server.address().port}`, {
    timeoutMs: 80, requestTimeoutMs: 20, retryDelayMs: 1,
  }), /deadline_exceeded/);
});
