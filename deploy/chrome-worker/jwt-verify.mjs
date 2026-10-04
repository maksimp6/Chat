// Access-token verification shared by the identity provider and every service
// that trusts it. This file is vendored byte-for-byte into each consumer (for
// example deploy/chrome-worker/jwt-verify.mjs) and a test fails if a copy drifts.
import { createPublicKey, verify } from "node:crypto";

const BASE64URL = /^[A-Za-z0-9_-]+$/;
const MAX_TOKEN_LENGTH = 4096;

export function publicKeyFromJwk(jwk) {
  if (jwk?.kty !== "OKP" || jwk.crv !== "Ed25519" || typeof jwk.x !== "string" || typeof jwk.kid !== "string") {
    throw new Error("invalid_jwk");
  }
  return { kid: jwk.kid, key: createPublicKey({ key: { kty: "OKP", crv: "Ed25519", x: jwk.x }, format: "jwk" }) };
}

function decode(part) {
  if (!BASE64URL.test(part)) throw new Error("invalid_token");
  return Buffer.from(part, "base64url");
}

// keys: Map<kid, KeyObject>. Only EdDSA with the expected `typ` is accepted, so
// "alg":"none", HMAC confusion and tokens of another kind are all rejected.
// Throws Error("unknown_kid") when the signing key is not in `keys` (callers may
// refresh their key set) and Error("invalid_token") for everything else.
export function verifyJwt(
  token,
  { keys, issuer, audience, typ = "at+jwt", now = Math.floor(Date.now() / 1000), leeway = 60, requiredScope },
) {
  const invalid = () => {
    throw new Error("invalid_token");
  };
  if (typeof token !== "string" || token.length > MAX_TOKEN_LENGTH) invalid();
  const parts = token.split(".");
  if (parts.length !== 3) invalid();
  let header;
  let claims;
  try {
    header = JSON.parse(decode(parts[0]).toString("utf8"));
    claims = JSON.parse(decode(parts[1]).toString("utf8"));
  } catch {
    invalid();
  }
  if (!header || header.alg !== "EdDSA" || header.typ !== typ || typeof header.kid !== "string") invalid();
  const key = keys.get(header.kid);
  if (!key) throw new Error("unknown_kid");
  let valid = false;
  try {
    valid = verify(null, Buffer.from(`${parts[0]}.${parts[1]}`), key, decode(parts[2]));
  } catch {
    valid = false;
  }
  if (!valid) invalid();
  if (!claims || typeof claims !== "object" || claims.iss !== issuer) invalid();
  const audiences = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (!audiences.includes(audience)) invalid();
  if (!Number.isFinite(claims.exp) || claims.exp + leeway <= now) invalid();
  if (claims.nbf !== undefined && (!Number.isFinite(claims.nbf) || claims.nbf - leeway > now)) invalid();
  if (typeof claims.sub !== "string" || !claims.sub) invalid();
  if (requiredScope) {
    const granted = typeof claims.scope === "string" ? claims.scope.split(" ") : [];
    if (!granted.includes(requiredScope)) invalid();
  }
  return claims;
}
