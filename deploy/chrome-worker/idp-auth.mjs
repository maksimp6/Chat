import { publicKeyFromJwk, verifyJwt } from "./jwt-verify.mjs";

export const BROWSER_SCOPES = ["browser.read", "browser.control"];
const REFRESH_COOLDOWN_SECONDS = 60;
const MAX_JWKS_BYTES = 16 * 1024;

/**
 * Resource-server side of the central Alice IdP: verifies its EdDSA access tokens
 * against the published key set. Fails closed on any error.
 */
export function createIdpAuth(options = {}) {
  const { issuer, resource, ownerIds = [], requiredScopes = BROWSER_SCOPES } = options;
  const owners = new Set(ownerIds.map(String).filter((id) => /^\d+$/.test(id)));
  if (!issuer || !resource || !owners.size) return { enabled: false, authorize: async () => false };
  const issuerUrl = new URL(issuer);
  if (issuerUrl.protocol !== "https:" || issuerUrl.pathname !== "/" || issuerUrl.search) throw new Error("invalid_idp_issuer");
  const base = issuerUrl.origin;
  const now = () => Math.floor((options.now?.() ?? Date.now()) / 1000);
  const fetchRemote = options.fetch ?? fetch;
  let keys = new Map();
  let lastFetch = -Infinity;
  let inflight;

  async function refresh() {
    // Unknown kids come from the caller, so bound how often they can cost a fetch.
    if (now() - lastFetch < REFRESH_COOLDOWN_SECONDS) return;
    lastFetch = now();
    inflight ??= (async () => {
      try {
        const response = await fetchRemote(`${base}/jwks.json`, { signal: AbortSignal.timeout(5000) });
        if (!response.ok) return;
        const text = await response.text();
        if (text.length > MAX_JWKS_BYTES) return;
        const next = new Map();
        for (const jwk of JSON.parse(text).keys ?? []) {
          try {
            const { kid, key } = publicKeyFromJwk(jwk);
            next.set(kid, key);
          } catch {
            // Skip keys we cannot use; the rest of the set stays valid.
          }
        }
        if (next.size) keys = next;
      } catch {
        // Unreachable or malformed: keep the previous set and fail closed per request.
      }
    })().finally(() => {
      inflight = undefined;
    });
    await inflight;
  }

  async function authorize(request) {
    const header = request.headers?.authorization;
    if (typeof header !== "string" || !header.startsWith("Bearer ")) return false;
    const token = header.slice(7);
    const verify = () => verifyJwt(token, { keys, issuer: base, audience: resource, now: now() });
    let claims;
    try {
      try {
        claims = verify();
      } catch (error) {
        if (error.message !== "unknown_kid") throw error;
        await refresh();
        claims = verify();
      }
    } catch {
      return false;
    }
    if (!owners.has(claims.sub)) return false;
    const scopes = String(claims.scope ?? "").split(" ").filter((scope) => BROWSER_SCOPES.includes(scope));
    if (!scopes.length) return false;
    return { sub: claims.sub, scopes };
  }

  return {
    enabled: true,
    authorize,
    resourceMetadata: () => ({ resource, authorization_servers: [base], scopes_supported: requiredScopes, bearer_methods_supported: ["header"] }),
    challenge: () => `Bearer resource_metadata="${new URL(resource).origin}/.well-known/oauth-protected-resource/browser/v1/mcp", scope="${requiredScopes.join(" ")}"`,
  };
}
