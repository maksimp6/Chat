import assert from "node:assert/strict";

const origin = process.env.ALICE_DEV_SMOKE_ORIGIN;
if (!origin) throw new Error("ALICE_DEV_SMOKE_ORIGIN is required");

const mcp = await fetch(`${origin}/mcp`, {
  method: "POST",
  headers: {
    "content-type": "application/json",
    "mcp-protocol-version": "2025-06-18",
  },
  body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }),
});
assert.equal(mcp.status, 401);
const challenge = mcp.headers.get("www-authenticate") ?? "";
const match = challenge.match(/resource_metadata="([^"]+)"/);
assert.ok(match, "missing resource_metadata challenge");

const resourceResponse = await fetch(match[1]);
assert.equal(resourceResponse.status, 200);
const resource = await resourceResponse.json();
assert.equal(resource.resource, `${origin}/mcp`);
assert.deepEqual(resource.authorization_servers, [`${origin}/oauth`]);
assert.ok(resource.scopes_supported.includes("alice-dev"));

const metadataResponse = await fetch(`${origin}/.well-known/oauth-authorization-server/oauth`);
assert.equal(metadataResponse.status, 200);
const metadata = await metadataResponse.json();
assert.equal(metadata.issuer, `${origin}/oauth`);
assert.equal(metadata.authorization_endpoint, `${origin}/oauth/authorize`);
assert.equal(metadata.token_endpoint, `${origin}/oauth/token`);
assert.equal(metadata.registration_endpoint, `${origin}/oauth/register`);
assert.ok(metadata.code_challenge_methods_supported.includes("S256"));
assert.ok(metadata.grant_types_supported.includes("authorization_code"));

console.log("alice-dev-oauth-discovery-live-ok");
