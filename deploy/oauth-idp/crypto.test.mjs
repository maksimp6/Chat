import assert from "node:assert/strict";
import test from "node:test";
import { cookieMac, createKeys, fromB64u, open, safeEqual, seal } from "./crypto.mjs";

const SECRET = "test-secret-with-at-least-32-characters!!";
const NOW = 1_800_000_000;

test("keys are derived deterministically from the single secret", () => {
  const first = createKeys(SECRET);
  const second = createKeys(SECRET);
  assert.equal(first.kid, second.kid);
  assert.deepEqual(first.jwk, second.jwk);
  assert.equal(first.jwk.kty, "OKP");
  assert.equal(first.jwk.crv, "Ed25519");
  assert.notEqual(createKeys(`${SECRET}-other`).kid, first.kid);
  assert.equal(first.jwk.d, undefined, "the public JWK never carries private key material");
});

test("short or missing secrets are refused", () => {
  assert.throws(() => createKeys("short"), /idp_secret_too_short/);
  assert.throws(() => createKeys(undefined), /idp_secret_too_short/);
});

test("sealed values round-trip and are opaque", () => {
  const keys = createKeys(SECRET);
  const token = seal(keys, "code", { sub: "1", secret: "plaintext-marker", exp: NOW + 60 });
  assert.ok(!token.includes("plaintext-marker"));
  assert.deepEqual(open(keys, "code", token, NOW), { sub: "1", secret: "plaintext-marker", exp: NOW + 60 });
});

test("a sealed value only opens as the type it was sealed as", () => {
  const keys = createKeys(SECRET);
  const token = seal(keys, "code", { exp: NOW + 60 });
  for (const other of ["refresh", "client", "tx", "gh"]) assert.equal(open(keys, other, token, NOW), null);
});

test("expired, tampered, truncated and foreign values are rejected", () => {
  const keys = createKeys(SECRET);
  const token = seal(keys, "refresh", { exp: NOW + 60 });
  assert.equal(open(keys, "refresh", token, NOW + 60), null, "expired exactly at exp");
  assert.equal(open(keys, "refresh", token, NOW + 61), null);
  const [iv, body, tag] = token.split(".");
  const flip = (value) => `${value.slice(0, -2)}${value.endsWith("AA") ? "BB" : "AA"}`;
  assert.equal(open(keys, "refresh", [iv, flip(body), tag].join("."), NOW), null);
  assert.equal(open(keys, "refresh", [iv, body, flip(tag)].join("."), NOW), null);
  assert.equal(open(keys, "refresh", [flip(iv), body, tag].join("."), NOW), null);
  assert.equal(open(keys, "refresh", [iv, body, tag.slice(0, 10)].join("."), NOW), null, "truncated tag");
  assert.equal(open(keys, "refresh", `${iv}.${body}`, NOW), null);
  assert.equal(open(keys, "refresh", "", NOW), null);
  assert.equal(open(keys, "refresh", undefined, NOW), null);
  assert.equal(open(createKeys(`${SECRET}-other`), "refresh", token, NOW), null, "another secret cannot open it");
});

test("a sealed value without a numeric exp is never accepted", () => {
  const keys = createKeys(SECRET);
  assert.equal(open(keys, "code", seal(keys, "code", { sub: "1" }), NOW), null);
  assert.equal(open(keys, "code", seal(keys, "code", { exp: "9999999999" }), NOW), null);
});

test("cookie MACs are deterministic per nonce and secret", () => {
  const keys = createKeys(SECRET);
  assert.equal(cookieMac(keys, "nonce-a"), cookieMac(keys, "nonce-a"));
  assert.notEqual(cookieMac(keys, "nonce-a"), cookieMac(keys, "nonce-b"));
  assert.notEqual(cookieMac(createKeys(`${SECRET}-other`), "nonce-a"), cookieMac(keys, "nonce-a"));
});

test("base64url decoding is strict and comparison is length-safe", () => {
  assert.throws(() => fromB64u("a+b"), /invalid_encoding/);
  assert.throws(() => fromB64u("ab=="), /invalid_encoding/);
  assert.throws(() => fromB64u(42), /invalid_encoding/);
  assert.equal(safeEqual("abc", "abc"), true);
  assert.equal(safeEqual("abc", "abd"), false);
  assert.equal(safeEqual("abc", "abcd"), false);
});
