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

export async function runLiveSmoke(options = {}) {
  const phase = options.phase ?? process.argv[2];
  const endpoint = new URL(options.endpoint ?? `${process.env.BROWSER_PUBLIC_URL ?? ""}/browser/v1/mcp`);
  const token = options.token ?? process.env.BROWSER_API_TOKEN ?? "";
  const marker = options.marker ?? process.env.BROWSER_SMOKE_MARKER ?? "";
  requireCheck(["smoke", "seed", "verify"].includes(phase), "invalid_smoke_phase");
  if (phase !== "smoke" && (typeof marker !== "string" || !/^[a-f0-9]{32}$/.test(marker))) {
    throw Object.assign(new Error("invalid_smoke_marker"), { smokeCode: "invalid_smoke_marker" });
  }
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
  try {
    await client.connect(transport, { timeout: 30_000 });
    requireCheck(Boolean(transport.sessionId), "mcp_session_missing");
    const tools = await client.listTools();
    for (const name of ["browser_tabs", "browser_navigate", "browser_snapshot", "browser_type", "browser_click", "browser_evaluate", "browser_take_screenshot"]) requireCheck(tools.tools.some((tool) => tool.name === name), `smoke_tool_missing_${name}`);
    await call("browser_tabs", { action: "new" });
    tabOpened = true;
    // Carry test data in a URL fragment, never in executable JavaScript source.
    // The fragment is not sent to example.com's HTTP server.
    await call("browser_navigate", { url: phase === "smoke" ? "https://example.com/" : `https://example.com/#${marker}` });
    if (phase === "smoke") {
      const snapshot = await call("browser_snapshot");
      requireCheck(JSON.stringify(snapshot).includes("Example Domain"), "smoke_snapshot_missing");
      const screenshot = await call("browser_take_screenshot", { type: "png" });
      requireCheck(screenshot.content.some((item) => item.type === "image"), "smoke_screenshot_missing");
    } else if (phase === "seed") {
      const seeded = await evaluate(`() => {
        if (location.origin !== "https://example.com") return { origin: location.origin };
        const marker = location.hash.slice(1);
        if (!/^[a-f0-9]{32}$/.test(marker)) throw new Error("invalid_smoke_fragment");
        const key = "alice_mcp_smoke_" + marker;
        document.title = "Chrome deployment acceptance";
        document.body.innerHTML = '<h1>Browser acceptance</h1><input aria-label="Acceptance input"><button>Save acceptance</button><output></output>';
        document.querySelector('button').onclick = () => { document.querySelector('output').textContent = document.querySelector('input').value; };
        document.cookie = key + "=" + marker + "; Path=/; Max-Age=3600; Secure; SameSite=Lax";
        localStorage.setItem(key, marker);
        return { origin: location.origin, marker, storage: localStorage.getItem(key) };
      }`);
      requireCheck(seeded.origin === "https://example.com" && seeded.marker === marker && seeded.storage === marker, "smoke_seed_failed");
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
    const persisted = phase === "smoke" ? null : await evaluate(`() => {
      if (location.origin !== "https://example.com") return { origin: location.origin };
      const marker = location.hash.slice(1);
      if (!/^[a-f0-9]{32}$/.test(marker)) throw new Error("invalid_smoke_fragment");
      const key = "alice_mcp_smoke_" + marker;
      return { origin: location.origin, marker, storage: localStorage.getItem(key), cookie: document.cookie.split('; ').find(value => value.startsWith(key + "="))?.slice(key.length + 1) ?? null };
    }`);
    if (phase !== "smoke") requireCheck(persisted.origin === "https://example.com" && persisted.marker === marker && persisted.storage === marker && persisted.cookie === marker, phase === "seed" ? "smoke_seed_marker_missing" : "smoke_persistent_profile_marker_mismatch");
    if (phase === "verify") {
      const cleaned = await evaluate(`() => {
        if (location.origin !== "https://example.com") return { origin: location.origin };
        const marker = location.hash.slice(1);
        if (!/^[a-f0-9]{32}$/.test(marker)) throw new Error("invalid_smoke_fragment");
        const key = "alice_mcp_smoke_" + marker;
        localStorage.removeItem(key);
        document.cookie = key + "=; Path=/; Max-Age=0; Secure; SameSite=Lax";
        return { marker, storage: localStorage.getItem(key), cookie: document.cookie.split('; ').some(value => value.startsWith(key + "=")) };
      }`);
      requireCheck(cleaned.marker === marker && cleaned.storage === null && cleaned.cookie === false, "smoke_marker_cleanup_failed");
    }
    requireCheck(Object.values(seen).every(Boolean), "mcp_gateway_headers_missing");
    await call("browser_tabs", { action: "close" });
    tabOpened = false;
    await transport.terminateSession();
    return { status: "passed", phase, tool_count: tools.tools.length, calls, transport_headers: "passed", oauth: "covered_by_short_token_oauth_test" };
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
