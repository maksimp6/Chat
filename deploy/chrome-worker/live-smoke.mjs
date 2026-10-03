import { createHash, randomBytes } from "node:crypto";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

function requireCheck(condition, code) {
  if (!condition) throw Object.assign(new Error(code), { smokeCode: code });
}

// Playwright also echoes evaluated source in another section. Only the Result
// section is evidence of execution; matching a marker in the whole text is unsafe.
export function evaluationResult(result) {
  const text = result.content?.filter((item) => item.type === "text").map((item) => item.text).join("\n") ?? "";
  const match = text.match(/(?:^|\n)### Result\r?\n([\s\S]*?)(?=\r?\n### |$)/);
  requireCheck(Boolean(match), "evaluation_result_missing");
  let payload = match[1].trim();
  if (payload.startsWith("```")) payload = payload.replace(/^```(?:json)?\s*\n/, "").replace(/\n```$/, "");
  try { return JSON.parse(payload); }
  catch { throw Object.assign(new Error("evaluation_result_invalid"), { smokeCode: "evaluation_result_invalid" }); }
}

async function oauthDiscovery(origin, fetchHttp) {
  const readJson = async (url, init) => {
    const response = await fetchHttp(url, { ...init, redirect: "manual", signal: AbortSignal.timeout(30_000) });
    requireCheck(response.ok, "oauth_discovery_http_failed");
    return response.json();
  };
  const resource = await readJson(`${origin}/.well-known/oauth-protected-resource/browser/v1/mcp`);
  requireCheck(resource.resource === `${origin}/browser/v1/mcp`, "oauth_resource_mismatch");
  requireCheck(resource.authorization_servers?.length === 1 && resource.authorization_servers[0] === `${origin}/browser/oauth`, "oauth_issuer_mismatch");
  const metadata = await readJson(`${origin}/.well-known/oauth-authorization-server/browser/oauth`);
  requireCheck(metadata.issuer === resource.authorization_servers[0] && metadata.code_challenge_methods_supported?.includes("S256"), "oauth_metadata_mismatch");
  for (const field of ["registration_endpoint", "authorization_endpoint", "token_endpoint"]) requireCheck(new URL(metadata[field]).origin === origin, "oauth_endpoint_mismatch");
  const redirectUri = "https://chatgpt.com/connector_platform_oauth_redirect";
  const registered = await readJson(metadata.registration_endpoint, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ client_name: "Chrome deployment acceptance", redirect_uris: [redirectUri], token_endpoint_auth_method: "none", grant_types: ["authorization_code", "refresh_token"], response_types: ["code"] }) });
  requireCheck(typeof registered.client_id === "string", "oauth_registration_failed");
  const verifier = randomBytes(32).toString("base64url");
  const authorize = new URL(metadata.authorization_endpoint);
  authorize.search = new URLSearchParams({ client_id: registered.client_id, redirect_uri: redirectUri, response_type: "code", resource: resource.resource, scope: "browser", state: randomBytes(24).toString("hex"), code_challenge: createHash("sha256").update(verifier).digest("base64url"), code_challenge_method: "S256" }).toString();
  const consent = await fetchHttp(authorize, { redirect: "manual", signal: AbortSignal.timeout(30_000) });
  requireCheck(consent.status === 200, "oauth_consent_failed");
  const cookie = consent.headers.get("set-cookie") ?? "";
  requireCheck(/HttpOnly/i.test(cookie) && /SameSite=Lax/i.test(cookie) && (origin.startsWith("http:") || /Secure/i.test(cookie)), "oauth_cookie_missing");
  const transaction = (await consent.text()).match(/name="transaction" value="([A-Za-z0-9_-]+)"/)?.[1];
  requireCheck(Boolean(transaction), "oauth_consent_transaction_missing");
  const login = await fetchHttp(authorize.origin + authorize.pathname, { method: "POST", redirect: "manual", signal: AbortSignal.timeout(30_000), headers: { "content-type": "application/x-www-form-urlencoded", cookie: cookie.split(";")[0] }, body: new URLSearchParams({ transaction }) });
  requireCheck(login.status === 302, "oauth_browser_cookie_not_forwarded");
  const github = new URL(login.headers.get("location") ?? "about:blank");
  requireCheck(github.origin === "https://github.com" && github.pathname === "/login/oauth/authorize" && github.searchParams.get("redirect_uri") === `${origin}/browser/oauth/github/callback` && github.searchParams.get("code_challenge_method") === "S256" && github.searchParams.get("state") === transaction && /^[A-Za-z0-9_-]{43}$/.test(github.searchParams.get("code_challenge") ?? ""), "oauth_github_redirect_mismatch");
  // This proves discovery/DCR/cookie/redirect transport, not the user's GitHub consent.
}

export async function runLiveSmoke(options = {}) {
  const phase = options.phase ?? process.argv[2];
  const endpoint = new URL(options.endpoint ?? `${process.env.BROWSER_PUBLIC_URL ?? ""}/browser/v1/mcp`);
  const token = options.token ?? process.env.BROWSER_API_TOKEN ?? "";
  const marker = (options.marker ?? process.env.BROWSER_SMOKE_MARKER ?? "").replaceAll("-", "").toLowerCase();
  requireCheck(["seed", "verify"].includes(phase), "invalid_smoke_phase");
  requireCheck(/^[a-f0-9]{32}$/.test(marker), "invalid_smoke_marker");
  requireCheck(Boolean(token), "smoke_token_missing");
  requireCheck(endpoint.pathname === "/browser/v1/mcp", "invalid_smoke_endpoint");
  requireCheck(endpoint.protocol === "https:" && !endpoint.username && !endpoint.password && !endpoint.search && !endpoint.hash, "invalid_smoke_endpoint");
  const fetchHttp = options.fetch ?? fetch;
  const seen = { session: false, protocol: false, accept: false, responseSession: false };
  const transport = new StreamableHTTPClientTransport(endpoint, {
    requestInit: { headers: { authorization: `Bearer ${token}` } },
    fetch: async (url, init) => {
      const headers = new Headers(init?.headers ?? (url instanceof Request ? url.headers : undefined));
      seen.session ||= Boolean(headers.get("mcp-session-id"));
      seen.protocol ||= Boolean(headers.get("mcp-protocol-version"));
      seen.accept ||= (headers.get("accept") ?? "").includes("application/json") && (headers.get("accept") ?? "").includes("text/event-stream");
      const response = await fetchHttp(url, init);
      seen.responseSession ||= Boolean(response.headers.get("mcp-session-id"));
      return response;
    },
  });
  const client = new Client({ name: "chrome-live-acceptance", version: "1.0.0" });
  let tabOpened = false;
  let calls = 0;
  async function call(name, args = {}) {
    const result = await client.callTool({ name, arguments: args }, undefined, { timeout: 60_000 });
    requireCheck(result.isError !== true, `smoke_${name}_failed`);
    calls += 1;
    return result;
  }
  const evaluate = async (source) => evaluationResult(await call("browser_evaluate", { function: source }));
  const key = `alice_mcp_smoke_${marker}`;
  try {
    await client.connect(transport, { timeout: 30_000 });
    requireCheck(Boolean(transport.sessionId), "mcp_session_missing");
    const tools = await client.listTools();
    for (const name of ["browser_tabs", "browser_navigate", "browser_snapshot", "browser_type", "browser_click", "browser_evaluate", "browser_take_screenshot"]) requireCheck(tools.tools.some((tool) => tool.name === name), `smoke_tool_missing_${name}`);
    if (options.checkOAuth !== false && phase === "seed") await oauthDiscovery(endpoint.origin, fetchHttp);
    await call("browser_tabs", { action: "new" });
    tabOpened = true;
    await call("browser_navigate", { url: "https://example.com" });
    if (phase === "seed") {
      const seeded = await evaluate(`() => {
        if (location.origin !== "https://example.com") return { origin: location.origin };
        document.title = "Chrome deployment acceptance";
        document.body.innerHTML = '<h1>Browser acceptance</h1><input aria-label="Acceptance input"><button>Save acceptance</button><output></output>';
        document.querySelector('button').onclick = () => { document.querySelector('output').textContent = document.querySelector('input').value; };
        document.cookie = ${JSON.stringify(`${key}=${marker}; Path=/; Max-Age=3600; Secure; SameSite=Lax`)};
        localStorage.setItem(${JSON.stringify(key)}, ${JSON.stringify(marker)});
        return { origin: location.origin, storage: localStorage.getItem(${JSON.stringify(key)}) };
      }`);
      requireCheck(seeded.origin === "https://example.com" && seeded.storage === marker, "smoke_seed_failed");
      const snapshot = await call("browser_snapshot");
      const text = snapshot.content.filter((item) => item.type === "text").map((item) => item.text).join("\n");
      const input = text.match(/textbox "Acceptance input" \[ref=([\w-]+)\]/)?.[1];
      const button = text.match(/button "Save acceptance" \[ref=([\w-]+)\]/)?.[1];
      requireCheck(Boolean(input && button), "smoke_snapshot_controls_missing");
      await call("browser_type", { target: input, text: marker });
      await call("browser_click", { target: button });
      const clicked = await evaluate("() => ({ input: document.querySelector('input')?.value, output: document.querySelector('output')?.textContent })");
      requireCheck(clicked.input === marker && clicked.output === marker, "smoke_interaction_failed");
      const screenshot = await call("browser_take_screenshot", { type: "png" });
      requireCheck(screenshot.content.some((item) => item.type === "image"), "smoke_screenshot_missing");
    }
    const persisted = await evaluate(`() => ({ origin: location.origin, storage: localStorage.getItem(${JSON.stringify(key)}), cookie: document.cookie.split('; ').find(value => value.startsWith(${JSON.stringify(`${key}=`)}))?.slice(${key.length + 1}) ?? null })`);
    requireCheck(persisted.origin === "https://example.com" && persisted.storage === marker && persisted.cookie === marker, phase === "seed" ? "smoke_seed_marker_missing" : "smoke_persistent_profile_marker_mismatch");
    if (phase === "verify") {
      const cleaned = await evaluate(`() => { localStorage.removeItem(${JSON.stringify(key)}); document.cookie = ${JSON.stringify(`${key}=; Path=/; Max-Age=0; Secure; SameSite=Lax`)}; return { storage: localStorage.getItem(${JSON.stringify(key)}), cookie: document.cookie.split('; ').some(value => value.startsWith(${JSON.stringify(`${key}=`)})) }; }`);
      requireCheck(cleaned.storage === null && cleaned.cookie === false, "smoke_marker_cleanup_failed");
    }
    requireCheck(Object.values(seen).every(Boolean), "mcp_gateway_headers_missing");
    await call("browser_tabs", { action: "close" });
    tabOpened = false;
    await transport.terminateSession();
    return { status: "passed", phase, tool_count: tools.tools.length, calls, transport_headers: "passed", oauth: options.checkOAuth !== false && phase === "seed" ? "discovery_and_github_redirect_passed" : "not_exercised" };
  } finally {
    if (tabOpened) await call("browser_tabs", { action: "close" }).catch(() => {});
    if (transport.sessionId) await transport.terminateSession().catch(() => {});
    await client.close().catch(() => {});
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try { process.stdout.write(`${JSON.stringify(await runLiveSmoke())}\n`); }
  catch (error) {
    // SDK errors can contain upstream responses, URLs, page content or credentials.
    process.stderr.write(`${JSON.stringify({ status: "failed", reason: error.smokeCode ?? "upstream_request_failed" })}\n`);
    process.exitCode = 1;
  }
}
