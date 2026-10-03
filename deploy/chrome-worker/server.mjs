import { createHash, timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";
import { lookup } from "node:dns/promises";
import { BlockList, isIP } from "node:net";

const MAX_BODY_BYTES = 64 * 1024;
const MAX_TEXT_CHARS = 32 * 1024;

function json(response, status, value) {
  const body = JSON.stringify(value);
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
    "cache-control": "no-store",
  });
  response.end(body);
}

function authorized(request, token) {
  if (!token) return false;
  const supplied = request.headers.authorization;
  if (typeof supplied !== "string" || !supplied.startsWith("Bearer ")) return false;
  const expectedHash = createHash("sha256").update(token).digest();
  const suppliedHash = createHash("sha256").update(supplied.slice(7)).digest();
  return timingSafeEqual(expectedHash, suppliedHash);
}

async function body(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) throw Object.assign(new Error("request_too_large"), { status: 413 });
    chunks.push(chunk);
  }
  if (!chunks.length) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw Object.assign(new Error("invalid_json"), { status: 400 });
  }
}

function safeUrl(value, allowedHosts) {
  let target;
  try {
    target = new URL(value);
  } catch {
    throw Object.assign(new Error("invalid_url"), { status: 400 });
  }
  if (!new Set(["http:", "https:"]).has(target.protocol)) {
    throw Object.assign(new Error("unsupported_url_scheme"), { status: 400 });
  }
  if (target.username || target.password || !allowedHosts.has(target.hostname.toLowerCase())) {
    throw Object.assign(new Error("host_not_allowed"), { status: 403 });
  }
  return target.href;
}

const privateNetworks = new BlockList();
for (const [address, prefix] of [
  ["0.0.0.0", 8], ["10.0.0.0", 8], ["100.64.0.0", 10], ["127.0.0.0", 8],
  ["169.254.0.0", 16], ["172.16.0.0", 12], ["192.0.0.0", 24],
  ["192.0.2.0", 24], ["192.168.0.0", 16], ["198.18.0.0", 15],
  ["198.51.100.0", 24], ["203.0.113.0", 24], ["224.0.0.0", 3],
]) privateNetworks.addSubnet(address, prefix, "ipv4");

// Pin public IPv4 answers for this browser lifetime. No later DNS lookup can
// rebind an approved hostname to a private address; IPv6 is intentionally closed.
export async function resolverRules(hosts, resolve = lookup) {
  const rules = [];
  for (const host of hosts) {
    if (!/^[a-z0-9]+(?:[.-][a-z0-9]+)*$/.test(host) || isIP(host)) {
      throw new Error("invalid_allowed_host");
    }
    const answers = await resolve(host, { all: true, family: 4 });
    if (!answers.length || answers.some(({ address }) => isIP(address) !== 4 || privateNetworks.check(address, "ipv4"))) {
      throw new Error("non_public_allowed_host");
    }
    rules.push(`MAP ${host} ${answers[0].address}`);
  }
  return [...rules, "MAP * ~NOTFOUND"].join(", ");
}

export function createWorker(options = {}) {
  const token = options.token ?? process.env.BROWSER_API_TOKEN ?? "";
  const allowedHosts = new Set(
    (options.allowedHosts ?? process.env.BROWSER_ALLOWED_HOSTS ?? "")
      .split(",")
      .map((host) => host.trim().toLowerCase())
      .filter(Boolean),
  );
  const profileDir = options.profileDir ?? process.env.CHROME_PROFILE_DIR ?? "/state/profile";
  let context;
  let page;
  let state = "sleeping";
  let generation = 0;

  async function wake() {
    if (context) return;
    if (!allowedHosts.size) throw Object.assign(new Error("allowed_hosts_required"), { status: 503 });
    const rules = await resolverRules(allowedHosts, options.resolve);
    const chromium = options.chromium ?? (await import("playwright-core")).chromium;
    context = await chromium.launchPersistentContext(profileDir, {
      executablePath: options.executablePath ?? process.env.CHROME_EXECUTABLE_PATH ?? "/usr/bin/google-chrome-stable",
      headless: process.env.CHROME_HEADLESS !== "0",
      chromiumSandbox: true,
      serviceWorkers: "block",
      args: ["--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check",
        "--no-proxy-server", "--disable-quic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        `--host-resolver-rules=${rules}`],
    });
    await context.route("**/*", async (route) => {
      try {
        safeUrl(route.request().url(), allowedHosts);
        await route.continue();
      } catch {
        await route.abort("blockedbyclient");
      }
    });
    await context.routeWebSocket("**/*", (socket) => socket.close());
    page = context.pages()[0] ?? (await context.newPage());
    generation += 1;
    state = "awake";
  }

  async function sleep() {
    if (context) await context.close();
    context = undefined;
    page = undefined;
    state = "sleeping";
  }

  async function callTool(name, args = {}) {
    if (name === "browser_status") {
      return { state, generation, url: page?.url() ?? null, title: page ? await page.title() : null };
    }
    if (name === "wake") {
      await wake();
      return { state, generation };
    }
    if (name === "sleep") {
      await sleep();
      return { state, generation };
    }
    if (!page) throw Object.assign(new Error("browser_sleeping"), { status: 409 });
    if (name === "navigate") {
      await page.goto(safeUrl(args.url, allowedHosts), { waitUntil: "domcontentloaded" });
      return { url: page.url(), title: await page.title() };
    }
    if (name === "click") {
      await page.locator(String(args.locator ?? "")).click();
      return { ok: true };
    }
    if (name === "type") {
      await page.locator(String(args.locator ?? "")).fill(String(args.text ?? ""));
      return { ok: true };
    }
    if (name === "extract") {
      const text = await page.locator(String(args.locator ?? "body")).innerText();
      return { text: text.slice(0, MAX_TEXT_CHARS), truncated: text.length > MAX_TEXT_CHARS };
    }
    if (name === "screenshot") {
      const bytes = await page.screenshot({ type: "png" });
      return { mediaType: "image/png", data: bytes.toString("base64") };
    }
    throw Object.assign(new Error("unknown_operation"), { status: 404 });
  }

  const routeTools = new Map([
    ["GET /browser/v1/status", ["browser_status", false]],
    ["POST /browser/v1/wake", ["wake", true]],
    ["POST /browser/v1/sleep", ["sleep", true]],
    ["POST /browser/v1/navigate", ["navigate", true]],
    ["POST /browser/v1/click", ["click", true]],
    ["POST /browser/v1/type", ["type", true]],
    ["POST /browser/v1/extract", ["extract", true]],
    ["POST /browser/v1/screenshot", ["screenshot", true]],
  ]);

  const tools = ["browser_status", "wake", "sleep", "navigate", "click", "type", "extract", "screenshot"].map(
    (name) => ({ name, description: `Controlled Chrome operation: ${name}`, inputSchema: { type: "object" } }),
  );

  async function handler(request, response) {
    const pathname = new URL(request.url, "http://worker.invalid").pathname;
    if (!authorized(request, token)) return json(response, 401, { error: "unauthorized" });
    try {
      if (request.method === "POST" && pathname === "/browser/v1/mcp") {
        const message = await body(request);
        if (message.method === "initialize") {
          return json(response, 200, { jsonrpc: "2.0", id: message.id, result: { protocolVersion: "2025-06-18", capabilities: { tools: {} }, serverInfo: { name: "alice-chrome-worker", version: "0.1.0" } } });
        }
        if (message.method === "tools/list") return json(response, 200, { jsonrpc: "2.0", id: message.id, result: { tools } });
        if (message.method === "tools/call") {
          const result = await callTool(message.params?.name, message.params?.arguments);
          return json(response, 200, { jsonrpc: "2.0", id: message.id, result: { content: [{ type: "text", text: JSON.stringify(result) }], structuredContent: result } });
        }
        return json(response, 200, { jsonrpc: "2.0", id: message.id ?? null, error: { code: -32601, message: "Method not found" } });
      }
      const operation = routeTools.get(`${request.method} ${pathname}`);
      if (!operation) return json(response, 404, { error: "not_found" });
      const args = operation[1] ? await body(request) : {};
      return json(response, 200, await callTool(operation[0], args));
    } catch (error) {
      return json(response, error.status ?? 500, { error: error.message === "browser_sleeping" ? error.message : "browser_operation_failed" });
    }
  }

  return { handler, callTool, close: sleep };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const worker = createWorker();
  createServer(worker.handler).listen(Number(process.env.PORT ?? 8080), "0.0.0.0");
}
