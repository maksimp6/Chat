import { setTimeout as delay } from "node:timers/promises";
import { pathToFileURL } from "node:url";
import { canonicalPublicOrigin } from "./request-origin.mjs";

class DiscoveryFailure extends Error {}
function requireEvidence(condition, code) {
  if (!condition) throw new DiscoveryFailure(code);
}

/** Read-only public acceptance, with one total deadline for all HTTP stages. */
export async function verifyOAuthDiscovery(publicUrl, options = {}) {
  const origin = canonicalPublicOrigin(publicUrl);
  const fetchImpl = options.fetch ?? fetch;
  const timeoutMs = options.timeoutMs ?? 60000;
  const requestTimeoutMs = options.requestTimeoutMs ?? 5000;
  const retryDelayMs = options.retryDelayMs ?? 1000;
  requireEvidence(Number.isFinite(timeoutMs) && timeoutMs > 0 &&
    Number.isFinite(requestTimeoutMs) && requestTimeoutMs > 0 &&
    Number.isFinite(retryDelayMs) && retryDelayMs >= 0, "invalid_timeout_config");
  const deadline = performance.now() + timeoutMs;
  const report = options.report ?? (() => {});

  async function request(path, expected, init = {}, json = true) {
    for (let attempt = 1; attempt <= 30; attempt += 1) {
      const remaining = deadline - performance.now();
      requireEvidence(remaining > 0, `deadline_exceeded:${path}`);
      try {
        const response = await fetchImpl(origin + path, {
          ...init,
          redirect: "manual",
          signal: AbortSignal.timeout(Math.max(1, Math.ceil(Math.min(requestTimeoutMs, remaining)))),
        });
        report({ stage: path, method: init.method ?? "GET", attempt, status: response.status });
        if ([502, 503, 504].includes(response.status)) {
          await response.body?.cancel();
        } else {
          requireEvidence(response.status === expected, `unexpected_http_status:${path}`);
          let data;
          if (json) {
            requireEvidence((response.headers.get("content-type") ?? "").includes("application/json"),
              `invalid_content_type:${path}`);
            try {
              data = await response.json();
            } catch (error) {
              if (error instanceof SyntaxError) throw new DiscoveryFailure(`invalid_json:${path}`);
              throw error;
            }
          } else {
            await response.body?.cancel();
          }
          return { response, data };
        }
      } catch (error) {
        if (error instanceof DiscoveryFailure) throw error;
        report({ stage: path, method: init.method ?? "GET", attempt, status: null, error: "transport_or_timeout" });
      }
      const remainingAfter = deadline - performance.now();
      requireEvidence(remainingAfter > 0, `deadline_exceeded:${path}`);
      await delay(Math.min(retryDelayMs, remainingAfter));
    }
    throw new DiscoveryFailure(`attempt_limit_exceeded:${path}`);
  }

  // Start on the MCP endpoint itself, not a /healthz pre-warm shortcut.
  const resourcePath = "/.well-known/oauth-protected-resource/mcp";
  for (const method of ["GET", "HEAD", "POST"]) {
    const init = {
      method,
      headers: { accept: "application/json, text/event-stream" },
    };
    if (method === "POST") {
      init.headers["content-type"] = "application/json";
      init.headers["mcp-protocol-version"] = "2025-06-18";
      init.body = JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: {
        protocolVersion: "2025-06-18", capabilities: {},
        clientInfo: { name: "alice-dev-discovery-smoke", version: "1.0" },
      } });
    }
    const mcp = await request("/mcp", 401, init, false);
    const metadataUrl = (mcp.response.headers.get("www-authenticate") ?? "")
      .match(/resource_metadata="([^"]+)"/)?.[1];
    // Never follow discovery to an unexpected origin, even without credentials.
    requireEvidence(metadataUrl === origin + resourcePath, "invalid_resource_metadata_challenge");
  }
  const health = await request("/healthz", 200);
  requireEvidence(health.data?.status === "ok" && health.data?.mode === "mcp-gateway", "invalid_health");
  const { data: resource } = await request(resourcePath, 200);
  const issuer = `${origin}/oauth`;
  requireEvidence(resource?.resource === `${origin}/mcp` &&
    Array.isArray(resource.authorization_servers) && resource.authorization_servers.length === 1 &&
    resource.authorization_servers[0] === issuer && resource.scopes_supported?.includes("alice-dev"),
    "invalid_protected_resource_metadata");
  const { data: metadata } = await request("/.well-known/oauth-authorization-server/oauth", 200);
  requireEvidence(metadata?.issuer === issuer &&
    metadata.authorization_endpoint === `${issuer}/authorize` &&
    metadata.token_endpoint === `${issuer}/token` &&
    metadata.registration_endpoint === `${issuer}/register` &&
    metadata.code_challenge_methods_supported?.includes("S256") &&
    metadata.grant_types_supported?.includes("authorization_code"), "invalid_authorization_metadata");
  return { status: "OAUTH_DISCOVERY_OK", origin };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    await verifyOAuthDiscovery(process.env.ALICE_DEV_SMOKE_ORIGIN, {
      report: (record) => console.log(JSON.stringify(record)),
    });
    console.log("alice-dev-oauth-discovery-live-ok");
  } catch (error) {
    console.error(error instanceof DiscoveryFailure ? error.message : "oauth_discovery_failed");
    process.exitCode = 1;
  }
}
