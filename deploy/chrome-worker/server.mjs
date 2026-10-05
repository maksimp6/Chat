import { timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";
import { dirname, join, relative } from "node:path";
import { createIdpAuth } from "./idp-auth.mjs";
import { createPlaywrightMcp } from "./mcp.mjs";
import { createOAuth } from "./oauth.mjs";
import { createChromeStateStore } from "./state.mjs";
import { createTakeover } from "./takeover.mjs";

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
  const expected = Buffer.from(token);
  const actual = Buffer.from(supplied.slice(7));
  return actual.length === expected.length && timingSafeEqual(expected, actual);
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
  if (allowedHosts.size && !allowedHosts.has(target.hostname.toLowerCase())) {
    throw Object.assign(new Error("host_not_allowed"), { status: 403 });
  }
  return target.href;
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
  const authDir = options.authDir ?? process.env.BROWSER_OAUTH_STATE_DIR
    ?? dirname(process.env.BROWSER_OAUTH_STATE_FILE ?? "/tmp/chrome-auth/oauth.json");
  const oauthStateFile = options.oauth?.stateFile ?? process.env.BROWSER_OAUTH_STATE_FILE
    ?? join(authDir, "oauth.json");
  const authRelative = relative(authDir, oauthStateFile);
  if ((options.stateDir ?? process.env.CHROME_STATE_DIR) && (authRelative.startsWith("..") || !authRelative)) {
    throw new Error("oauth_state_outside_checkpoint_directory");
  }
  const stateStore = options.stateStore ?? createChromeStateStore({
    profileDir,
    stateDir: options.stateDir ?? process.env.CHROME_STATE_DIR,
    authDir,
  });
  // Central Alice IdP (optional): verifies its tokens for the MCP endpoint only.
  const publicUrl = options.publicUrl ?? process.env.BROWSER_PUBLIC_URL ?? "";
  const idp = createIdpAuth({
    issuer: process.env.BROWSER_IDP_ISSUER,
    resource: publicUrl ? `${new URL(publicUrl).origin}/browser/v1/mcp` : undefined,
    ownerIds: (process.env.BROWSER_GITHUB_ALLOWED_ID ?? process.env.ALICE_GITHUB_ALLOWED_IDS ?? "").split(","),
    requiredScopes: ["browser.control"],
    ...options.idp,
  });
  const takeover = createTakeover(options.takeover);
  let oauth;
  const ready = (async () => {
    takeover.startDisplay();
    await stateStore.restore();
    oauth = createOAuth({
      ...options.oauth,
      stateFile: oauthStateFile,
      onPersist: () => stateStore.checkpointAuth(),
    });
  })();
  // Keep lifecycle operations serialized when multiple MCP clients wake at once.
  let lifecycle = Promise.resolve();
  const transition = (operation) => {
    const next = lifecycle.then(operation);
    lifecycle = next.catch(() => {});
    return next;
  };
  let context;
  let page;
  let state = "sleeping";
  let generation = 0;

  async function wake() {
    await ready;
    return transition(async () => {
      if (context) return;
      const chromium = options.chromium ?? (await import("playwright-core")).chromium;
      context = await chromium.launchPersistentContext(profileDir, {
        executablePath: options.executablePath ?? process.env.CHROME_EXECUTABLE_PATH ?? "/usr/bin/google-chrome-stable",
        headless: false,
        chromiumSandbox: false,
        args: ["--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check"],
      });
      context.on?.("close", () => {
        context = undefined;
        page = undefined;
        state = "sleeping";
      });
      page = context.pages()[0] ?? (await context.newPage());
      generation += 1;
      state = "awake";
    });
  }

  async function sleep() {
    await ready;
    return transition(async () => {
      if (context) await context.close();
      context = undefined;
      page = undefined;
      state = "sleeping";
      await stateStore.checkpoint();
    });
  }

  async function callTool(name, args = {}) {
    await ready;
    if (name === "browser_status") {
      return { state, generation, url: page?.url() ?? null, title: page ? await page.title() : null, profile: stateStore.status() };
    }
    if (name === "wake") {
      await wake();
      return { state, generation, profile: stateStore.status() };
    }
    if (name === "sleep") {
      await sleep();
      return { state, generation, profile: stateStore.status() };
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

  const mcp = createPlaywrightMcp(async () => {
    await wake();
    return context;
  }, `${profileDir}/mcp-output`);

  async function handler(request, response) {
    const url = new URL(request.url, "http://worker.invalid");
    const pathname = url.pathname;
    try {
      await ready;
      if (request.method === "GET" && pathname === "/healthz") {
        return json(response, 200, {
          status: "ok",
          deployment_sha: process.env.BROWSER_DEPLOYMENT_SHA ?? null,
          state_ready: true,
          oauth_ready: oauth.enabled,
          idp_ready: idp.enabled,
          state_error: stateStore.status().lastError ?? null,
        });
      }
      if (idp.enabled && request.method === "GET" && ["/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/browser/v1/mcp"].includes(pathname)) {
        return json(response, 200, idp.resourceMetadata());
      }
      if (await oauth.handle(request, response, url)) return;
      const isMcp = pathname === "/browser/v1/mcp";
      const idpGrant = isMcp && idp.enabled ? await idp.authorize(request) : false;
      const idpControlAccess = Boolean(idpGrant && idpGrant.scopes.includes("browser.control"));
      const mcpAccess = isMcp && (oauth.authorize(request) || idpControlAccess);
      if (!authorized(request, token) && !mcpAccess) {
        if (isMcp && idp.enabled) response.setHeader("www-authenticate", idp.challenge());
        else if (isMcp && oauth.enabled) response.setHeader("www-authenticate", oauth.challenge());
        return json(response, 401, { error: "unauthorized" });
      }
      if (pathname === "/browser/v1/mcp") {
        const message = request.method === "POST" ? await body(request) : undefined;
        return await mcp.handle(request, response, message);
      }
      if (request.method === "POST" && pathname === "/browser/v1/takeover/start") {
        await wake();
        const grant = takeover.start();
        const origin = publicUrl ? new URL(publicUrl).origin : `http://${request.headers.host}`;
        return json(response, 200, {
          url: `${origin}/browser/v1/takeover/vnc.html?autoconnect=1&resize=scale&path=${encodeURIComponent(`browser/v1/takeover/websockify?takeover_token=${grant.token}`)}&takeover_token=${grant.token}`,
          expiresAt: grant.expiresAt,
        });
      }
      if (request.method === "POST" && pathname === "/browser/v1/takeover/stop") {
        takeover.stop();
        return json(response, 200, { active: false });
      }
      if (request.method === "GET" && pathname === "/browser/v1/takeover/status") {
        return json(response, 200, takeover.status());
      }
      const operation = routeTools.get(`${request.method} ${pathname}`);
      if (!operation) return json(response, 404, { error: "not_found" });
      const args = operation[1] ? await body(request) : {};
      return json(response, 200, await callTool(operation[0], args));
    } catch (error) {
      if (!error.status) process.stderr.write(`${JSON.stringify({ event: "browser_operation_failed", path: pathname, error: /^[a-z0-9_:A-Z]{1,80}$/.test(error?.message ?? "") ? error.message : "unexpected" })}\n`);
      return json(response, error.status ?? 500, { error: error.message === "browser_sleeping" ? error.message : "browser_operation_failed" });
    }
  }

  return { handler, callTool, ready, takeover, async close() { await mcp.close(); takeover.close(); await sleep(); } };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const worker = createWorker();
  await worker.ready;
  const server = createServer(worker.handler);
  server.on("upgrade", (request, socket, head) => worker.takeover.proxyUpgrade(request, socket, head));
  server.listen(Number(process.env.PORT ?? 8080), "0.0.0.0");
  let stopping = false;
  async function stop() {
    if (stopping) return;
    stopping = true;
    server.close();
    try {
      await worker.close();
      process.exit(0);
    } catch {
      process.stderr.write("chrome_checkpoint_failed\n");
      process.exit(1);
    }
  }
  process.once("SIGTERM", stop);
  process.once("SIGINT", stop);
}
