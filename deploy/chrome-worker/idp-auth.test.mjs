import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createKeys } from "../oauth-idp/crypto.mjs";
import { signJwt } from "../oauth-idp/jwt-sign.mjs";
import { createIdpAuth } from "./idp-auth.mjs";

const ISSUER = "https://oauth.example.test";
const RESOURCE = "https://chrome.example.test/browser/v1/mcp";
const OWNER = "12345";
const NOW = 1_800_000_000;
const keys = createKeys("k".repeat(40));
const otherKeys = createKeys("z".repeat(40));

function claims(extra = {}) {
  return { iss: ISSUER, sub: OWNER, aud: RESOURCE, scope: "browser.read", iat: NOW, exp: NOW + 3600, ...extra };
}

function setup({ jwks = [keys.jwk], now = () => NOW * 1000, owners = [OWNER] } = {}) {
  const calls = { jwks: 0 };
  const state = { jwks };
  const auth = createIdpAuth({
    issuer: ISSUER,
    resource: RESOURCE,
    ownerIds: owners,
    now,
    fetch: async (url) => {
      assert.equal(String(url), `${ISSUER}/jwks.json`);
      calls.jwks += 1;
      return new Response(JSON.stringify({ keys: state.jwks }), { status: 200, headers: { "content-type": "application/json" } });
    },
  });
  return { auth, calls, state };
}

const request = (token) => ({ headers: token === undefined ? {} : { authorization: `Bearer ${token}` } });

test("accepts a valid token and exposes its scopes", async () => {
  const { auth } = setup();
  const result = await auth.authorize(request(signJwt(keys, claims({ scope: "browser.read browser.control" }))));
  assert.deepEqual(result.scopes, ["browser.read", "browser.control"]);
});

test("rejects missing, malformed and foreign-signed tokens", async () => {
  const { auth } = setup();
  assert.equal(await auth.authorize(request()), false);
  assert.equal(await auth.authorize(request("not-a-jwt")), false);
  assert.equal(await auth.authorize(request(signJwt(otherKeys, claims()))), false);
});

test("rejects wrong issuer, audience, expiry and non-owner subject", async () => {
  const { auth } = setup();
  for (const bad of [{ iss: "https://evil.test" }, { aud: "https://other.test/mcp" }, { exp: NOW - 3600 }, { sub: "999" }]) {
    assert.equal(await auth.authorize(request(signJwt(keys, claims(bad)))), false, JSON.stringify(bad));
  }
});

test("requires a browser scope and ignores unrelated scopes", async () => {
  const { auth } = setup();
  assert.equal(await auth.authorize(request(signJwt(keys, claims({ scope: "mcp" })))), false);
  assert.equal(await auth.authorize(request(signJwt(keys, claims({ scope: "" })))), false);
  const control = await auth.authorize(request(signJwt(keys, claims({ scope: "browser.control" }))));
  assert.deepEqual(control.scopes, ["browser.control"]);
});

test("caches the key set and refreshes once for an unknown kid", async () => {
  let time = NOW * 1000;
  const { auth, calls, state } = setup({ now: () => time });
  await auth.authorize(request(signJwt(keys, claims())));
  await auth.authorize(request(signJwt(keys, claims())));
  assert.equal(calls.jwks, 1);
  // The IdP rotated: the old set is cached, the new key appears only on refresh.
  state.jwks = [otherKeys.jwk];
  time += 61_000;
  assert.ok(await auth.authorize(request(signJwt(otherKeys, claims()))));
  assert.equal(calls.jwks, 2);
});

test("bogus kids cannot trigger unbounded JWKS fetches", async () => {
  let time = NOW * 1000;
  const { auth, calls } = setup({ now: () => time });
  await auth.authorize(request(signJwt(keys, claims())));
  time += 61_000;
  const before = calls.jwks;
  for (let i = 0; i < 20; i += 1) await auth.authorize(request(signJwt(otherKeys, claims())));
  assert.equal(calls.jwks - before, 1, `fetched ${calls.jwks - before} times`);
  time += 61_000;
  await auth.authorize(request(signJwt(otherKeys, claims())));
  assert.equal(calls.jwks - before, 2);
});

test("fails closed when the IdP is unreachable or returns garbage", async () => {
  const down = createIdpAuth({ issuer: ISSUER, resource: RESOURCE, ownerIds: [OWNER], now: () => NOW * 1000, fetch: async () => { throw new Error("offline"); } });
  assert.equal(await down.authorize(request(signJwt(keys, claims()))), false);
  const garbage = createIdpAuth({ issuer: ISSUER, resource: RESOURCE, ownerIds: [OWNER], now: () => NOW * 1000, fetch: async () => new Response("<html>", { status: 200 }) });
  assert.equal(await garbage.authorize(request(signJwt(keys, claims()))), false);
  const bad = createIdpAuth({ issuer: ISSUER, resource: RESOURCE, ownerIds: [OWNER], now: () => NOW * 1000, fetch: async () => new Response("{}", { status: 500 }) });
  assert.equal(await bad.authorize(request(signJwt(keys, claims()))), false);
});

test("is disabled unless issuer, resource and owners are configured", () => {
  for (const options of [{}, { issuer: ISSUER }, { issuer: ISSUER, resource: RESOURCE, ownerIds: [] }, { issuer: ISSUER, resource: RESOURCE, ownerIds: ["abc"] }]) {
    assert.equal(createIdpAuth(options).enabled, false);
  }
  assert.throws(() => createIdpAuth({ issuer: "http://insecure.test", resource: RESOURCE, ownerIds: [OWNER] }), /invalid_idp_issuer/);
});

test("advertises the IdP and the browser scopes in protected-resource metadata", () => {
  const { auth } = setup();
  assert.deepEqual(auth.resourceMetadata(), {
    resource: RESOURCE,
    authorization_servers: [ISSUER],
    scopes_supported: ["browser.read", "browser.control"],
    bearer_methods_supported: ["header"],
  });
  assert.match(auth.challenge(), /^Bearer resource_metadata="https:\/\/chrome\.example\.test\/\.well-known\/oauth-protected-resource\/browser\/v1\/mcp"/);
});

test("vendored verifier is byte-identical to the IdP copy", () => {
  assert.equal(readFileSync(new URL("./jwt-verify.mjs", import.meta.url), "utf8"), readFileSync(new URL("../oauth-idp/jwt-verify.mjs", import.meta.url), "utf8"));
});
