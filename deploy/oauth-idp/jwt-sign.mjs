import { sign } from "node:crypto";
import { b64u } from "./crypto.mjs";

// Issues an EdDSA JWT (RFC 8037) with an explicit `typ`; the matching verifier
// lives in jwt-verify.mjs.
export function signJwt(keys, claims, typ = "at+jwt") {
  const header = b64u(JSON.stringify({ alg: "EdDSA", typ, kid: keys.kid }));
  const payload = b64u(JSON.stringify(claims));
  const signature = sign(null, Buffer.from(`${header}.${payload}`), keys.privateKey);
  return `${header}.${payload}.${b64u(signature)}`;
}
