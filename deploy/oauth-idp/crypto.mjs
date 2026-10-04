// Key derivation, sealed (encrypted) blobs and cookie MACs for the identity
// provider. Everything is derived from one secret, so the service stores nothing:
// clients, authorization transactions, codes and refresh tokens are all sealed
// values that only this secret can open.
import {
  createCipheriv,
  createDecipheriv,
  createHash,
  createHmac,
  createPrivateKey,
  createPublicKey,
  hkdfSync,
  randomBytes,
  scryptSync,
  timingSafeEqual,
} from "node:crypto";

export const MIN_SECRET_LENGTH = 32;
const SALT = Buffer.from("alice-oauth-idp/v1");
// DER prefix of an Ed25519 PKCS#8 private key; the 32-byte seed follows it.
const PKCS8_ED25519_PREFIX = Buffer.from("302e020100300506032b657004220420", "hex");
const BASE64URL = /^[A-Za-z0-9_-]*$/;

export const b64u = (value) => Buffer.from(value).toString("base64url");
export const randomToken = (bytes = 32) => b64u(randomBytes(bytes));
export const sha256b64u = (value) => b64u(createHash("sha256").update(value).digest());

// Slow, salted hash for the owner passphrase (never a plain digest of a password).
export const hashPassphrase = (passphrase, salt) =>
  b64u(scryptSync(String(passphrase), Buffer.from(String(salt)), 32, { N: 16384, r: 8, p: 1 }));

export function fromB64u(value) {
  if (typeof value !== "string" || !BASE64URL.test(value)) throw new Error("invalid_encoding");
  return Buffer.from(value, "base64url");
}

export function safeEqual(left, right) {
  const a = Buffer.from(String(left));
  const b = Buffer.from(String(right));
  return a.length === b.length && timingSafeEqual(a, b);
}

const derive = (secret, info) => Buffer.from(hkdfSync("sha256", Buffer.from(secret, "utf8"), SALT, info, 32));

export function createKeys(secret) {
  if (typeof secret !== "string" || secret.length < MIN_SECRET_LENGTH) throw new Error("idp_secret_too_short");
  const privateKey = createPrivateKey({
    key: Buffer.concat([PKCS8_ED25519_PREFIX, derive(secret, "ed25519")]),
    format: "der",
    type: "pkcs8",
  });
  const publicKey = createPublicKey(privateKey);
  const { crv, kty, x } = publicKey.export({ format: "jwk" });
  // RFC 7638 thumbprint input: required members in lexicographic order.
  const kid = b64u(createHash("sha256").update(JSON.stringify({ crv, kty, x })).digest()).slice(0, 16);
  return {
    sealKey: derive(secret, "seal"),
    cookieKey: derive(secret, "cookie"),
    privateKey,
    publicKey,
    kid,
    jwk: { kty, crv, x, kid, use: "sig", alg: "EdDSA" },
  };
}

// The value type is authenticated as AAD, so a sealed "code" can never be
// replayed as a "refresh" token or a "client" registration.
export function seal(keys, type, payload) {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", keys.sealKey, iv, { authTagLength: 16 });
  cipher.setAAD(Buffer.from(type));
  const body = Buffer.concat([cipher.update(JSON.stringify(payload), "utf8"), cipher.final()]);
  return [b64u(iv), b64u(body), b64u(cipher.getAuthTag())].join(".");
}

// Returns the payload, or null for anything malformed, tampered, of another
// type, or past its mandatory numeric `exp` (seconds).
export function open(keys, type, token, nowSeconds) {
  try {
    const parts = String(token).split(".");
    if (parts.length !== 3) return null;
    const [iv, body, tag] = parts.map(fromB64u);
    if (iv.length !== 12 || tag.length !== 16) return null;
    const decipher = createDecipheriv("aes-256-gcm", keys.sealKey, iv, { authTagLength: 16 });
    decipher.setAAD(Buffer.from(type));
    decipher.setAuthTag(tag);
    const payload = JSON.parse(Buffer.concat([decipher.update(body), decipher.final()]).toString("utf8"));
    if (!payload || typeof payload !== "object" || typeof payload.exp !== "number") return null;
    return payload.exp > nowSeconds ? payload : null;
  } catch {
    return null;
  }
}

export const cookieMac = (keys, nonce) => b64u(createHmac("sha256", keys.cookieKey).update(nonce).digest());
