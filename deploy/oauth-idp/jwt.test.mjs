import assert from "node:assert/strict";
import { createHmac, sign } from "node:crypto";
import test from "node:test";
import { b64u, createKeys } from "./crypto.mjs";
import { signJwt } from "./jwt-sign.mjs";
import { publicKeyFromJwk, verifyJwt } from "./jwt-verify.mjs";

const SECRET = "test-secret-with-at-least-32-characters!!";
const ISSUER = "https://oauth.example.test";
const AUDIENCE = "https://chrome.example.test/browser/v1/mcp";
const NOW = 1_800_000_000;

const keys = createKeys(SECRET);
const trusted = new Map([[publicKeyFromJwk(keys.jwk).kid, publicKeyFromJwk(keys.jwk).key]]);
const claims = (overrides = {}) => ({
  iss: ISSUER,
  sub: "293531601",
  aud: AUDIENCE,
  scope: "browser",
  iat: NOW,
  exp: NOW + 3600,
  ...overrides,
});
const check = (token, overrides = {}) =>
  verifyJwt(token, { keys: trusted, issuer: ISSUER, audience: AUDIENCE, now: NOW, ...overrides });
const rejected = (token, overrides, code = "invalid_token") =>
  assert.throws(() => check(token, overrides), new RegExp(`^Error: ${code}$`));

test("a signed access token verifies and returns its claims", () => {
  const verified = check(signJwt(keys, claims()), { requiredScope: "browser" });
  assert.equal(verified.sub, "293531601");
  assert.equal(verified.aud, AUDIENCE);
});

test("audience may be an array that contains the service", () => {
  assert.equal(check(signJwt(keys, claims({ aud: ["https://other.example", AUDIENCE] }))).sub, "293531601");
});

test("wrong issuer, audience, scope and subject claims are rejected", () => {
  rejected(signJwt(keys, claims({ iss: "https://evil.example" })));
  rejected(signJwt(keys, claims({ aud: "https://other.example/mcp" })));
  rejected(signJwt(keys, claims({ aud: undefined })));
  rejected(signJwt(keys, claims({ scope: "other" })), { requiredScope: "browser" });
  rejected(signJwt(keys, claims({ scope: undefined })), { requiredScope: "browser" });
  rejected(signJwt(keys, claims({ sub: "" })));
  rejected(signJwt(keys, claims({ sub: undefined })));
});

test("expiry and not-before are enforced with a bounded leeway", () => {
  check(signJwt(keys, claims({ exp: NOW - 59 })));
  rejected(signJwt(keys, claims({ exp: NOW - 60 })));
  rejected(signJwt(keys, claims({ exp: undefined })));
  rejected(signJwt(keys, claims({ exp: "9999999999" })));
  check(signJwt(keys, claims({ nbf: NOW + 59 })));
  rejected(signJwt(keys, claims({ nbf: NOW + 61 })));
});

test("only EdDSA with the expected typ is accepted", () => {
  rejected(signJwt(keys, claims(), "JWT"));
  rejected(signJwt(keys, claims(), "code+jwt"));
  const header = (value) => b64u(JSON.stringify(value));
  const body = b64u(JSON.stringify(claims()));
  const kid = keys.kid;
  rejected(`${header({ alg: "none", typ: "at+jwt", kid })}.${body}.`);
  rejected(`${header({ alg: "none", typ: "at+jwt", kid })}.${body}.${b64u("x")}`);
  const hs256 = header({ alg: "HS256", typ: "at+jwt", kid });
  const mac = b64u(createHmac("sha256", keys.jwk.x).update(`${hs256}.${body}`).digest());
  rejected(`${hs256}.${body}.${mac}`);
  rejected(`${header({ alg: "ES256", typ: "at+jwt", kid })}.${body}.${b64u("x")}`);
});

test("a validly signed token that declares another algorithm is rejected", () => {
  // The signature is genuine, so only the explicit algorithm check can refuse it.
  for (const alg of ["none", "HS256", "ES256", "RS256", "eddsa"]) {
    const head = b64u(JSON.stringify({ alg, typ: "at+jwt", kid: keys.kid }));
    const body = b64u(JSON.stringify(claims()));
    const signature = b64u(sign(null, Buffer.from(`${head}.${body}`), keys.privateKey));
    rejected(`${head}.${body}.${signature}`);
  }
});

test("a forged or altered signature is rejected", () => {
  const token = signJwt(keys, claims());
  const [head, body, signature] = token.split(".");
  const flip = `${signature.slice(0, -2)}${signature.endsWith("AA") ? "BB" : "AA"}`;
  rejected([head, body, flip].join("."));
  rejected([head, b64u(JSON.stringify(claims({ sub: "1" }))), signature].join("."));
  const stranger = createKeys(`${SECRET}-stranger`);
  rejected(signJwt({ ...stranger, kid: keys.kid }, claims()));
});

test("an unknown signing key is reported distinctly so callers can refresh their key set", () => {
  const other = createKeys(`${SECRET}-rotated`);
  rejected(signJwt(other, claims()), undefined, "unknown_kid");
});

test("malformed tokens never verify", () => {
  const token = signJwt(keys, claims());
  for (const bad of ["", "a.b", `${token}.extra`, "a.b.c.d", `${token.slice(0, 10)}+${token.slice(11)}`, "x".repeat(5000), undefined, 42, null]) {
    rejected(bad);
  }
});

test("only Ed25519 public JWKs with a kid are accepted as keys", () => {
  assert.throws(() => publicKeyFromJwk({ kty: "RSA", n: "x", e: "AQAB", kid: "k" }), /invalid_jwk/);
  assert.throws(() => publicKeyFromJwk({ kty: "OKP", crv: "Ed448", x: keys.jwk.x, kid: "k" }), /invalid_jwk/);
  assert.throws(() => publicKeyFromJwk({ kty: "OKP", crv: "Ed25519", x: keys.jwk.x }), /invalid_jwk/);
  assert.throws(() => publicKeyFromJwk(undefined), /invalid_jwk/);
});
