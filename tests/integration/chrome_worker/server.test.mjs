import assert from "node:assert/strict";
import { once } from "node:events";
import { createServer } from "node:http";
import { createConnection, createServer as createTcpServer } from "node:net";
import test from "node:test";

import { createWorker } from "./server.mjs";
import { createTakeover } from "./takeover.mjs";

function listen(server) {
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server.address().port)));
}

async function waitFor(predicate, ms = 2000) {
  const deadline = Date.now() + ms;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error("condition_not_met");
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
}

function fakeChromium() {
  const profile = { value: "" };
  const page = {
    currentUrl: "about:blank",
    async goto(url) { this.currentUrl = url; },
    url() { return this.currentUrl; },
    async title() { return "Synthetic page"; },
    locator(locator) {
      return {
        async click() { profile.clicked = locator; },
        async fill(value) { profile.value = value; },
        async innerText() { return profile.value || "page text"; },
      };
    },
    async screenshot() { return Buffer.from("png"); },
  };
  return {
    profile,
    chromium: {
      async launchPersistentContext(directory) {
        profile.directory = directory;
        return { pages: () => [page], async close() { profile.closed = true; } };
      },
    },
  };
}

async function fixture(options = {}) {
  const fake = fakeChromium();
  const worker = createWorker({ token: "test-token", allowedHosts: "example.test", profileDir: "/state/profile", chromium: fake.chromium, ...options });
  const server = createServer(worker.handler).listen(0, "127.0.0.1");
  await once(server, "listening");
  const base = `http://127.0.0.1:${server.address().port}`;
  async function request(path, payload, token = "test-token") {
    return fetch(base + path, {
      method: payload === undefined ? "GET" : "POST",
      headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
      body: payload === undefined ? undefined : JSON.stringify(payload),
    });
  }
  const workerProfile = (await worker.callTool("browser_status")).profile;
  return { base, fake, worker, workerProfile, request, close: () => new Promise((resolve) => server.close(resolve)) };
}

test("worker rejects anonymous calls and wakes and sleeps a persistent profile", async (t) => {
  const app = await fixture();
  t.after(app.close);
  for (const invalidToken of ["", "wrong", "test-tokem"]) {
    assert.equal((await app.request("/browser/v1/status", undefined, invalidToken)).status, 401);
  }
  assert.deepEqual(await (await app.request("/browser/v1/status")).json(), { state: "sleeping", generation: 0, url: null, title: null, profile: app.workerProfile });
  assert.equal((await app.request("/browser/v1/wake", {})).status, 200);
  assert.equal(app.fake.profile.directory, "/state/profile");
  assert.equal((await app.request("/browser/v1/sleep", {})).status, 200);
  assert.equal(app.fake.profile.closed, true);
});

test("worker preserves its REST navigation and input operations", async (t) => {
  const app = await fixture();
  t.after(app.close);
  await app.request("/browser/v1/wake", {});
  assert.equal((await app.request("/browser/v1/navigate", { url: "http://127.0.0.1/private" })).status, 403);
  assert.equal((await app.request("/browser/v1/navigate", { url: "https://example.test/form" })).status, 200);

  assert.equal((await app.request("/browser/v1/type", { locator: "#name", text: "Alice" })).status, 200);
  assert.equal(app.fake.profile.value, "Alice");
});


test("human takeover requires worker auth and returns only an ephemeral browser URL", async (t) => {
  const takeover = {
    startDisplay() {},
    start() { return { token: "ephemeral-grant", expiresAt: 123456 }; },
    stop() {},
    close() {},
    status() { return { active: true, expiresAt: 123456 }; },
    async proxyHttp() { return false; },
    proxyUpgrade() {},
  };
  const app = await fixture({ takeoverEnabled: true, takeoverRuntime: takeover });
  t.after(app.close);

  assert.equal((await app.request("/browser/v1/takeover/start", {}, "wrong")).status, 401);
  const response = await app.request("/browser/v1/takeover/start", {});
  assert.equal(response.status, 200);
  const grant = await response.json();
  assert.equal(grant.expiresAt, 123456);
  assert.match(grant.url, /^http:\/\/127\.0\.0\.1:\d+\/browser\/v1\/takeover\/vnc\.html\?/);
  assert.match(grant.url, /takeover_token=ephemeral-grant/);
  assert.equal(JSON.stringify(grant).includes("test-token"), false);
});


test("human takeover entry page never exposes browser UI before owner authentication", async (t) => {
  const takeover = {
    startDisplay() {}, start() { throw new Error("must_not_start"); }, stop() {}, close() {},
    status() { return { active: false }; }, async proxyHttp() { return false; }, proxyUpgrade() {},
  };
  const app = await fixture({ takeoverEnabled: true, takeoverRuntime: takeover });
  t.after(app.close);
  const response = await app.request("/browser/v1/takeover", undefined, "wrong");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Введите short token владельца/);
  assert.equal(html.includes("takeover_token"), false);
});


test("secret fill resolves inside worker and never returns plaintext", async (t) => {
  const calls = [];
  const app = await fixture({
    secretResolver: async (alias, purpose) => {
      calls.push({ alias, purpose });
      assert.equal(alias, "github");
      assert.equal(purpose, "browser.password");
      return "canary-browser-secret";
    },
  });
  t.after(app.close);
  await app.request("/browser/v1/wake", {});

  const response = await app.request("/browser/v1/secret/fill", {
    alias: "github",
    locator: "#password",
    purpose: "browser.password",
  });

  assert.equal(response.status, 200);
  const result = await response.json();
  assert.deepEqual(result, { ok: true });
  assert.equal(JSON.stringify(result).includes("canary-browser-secret"), false);
  assert.equal(app.fake.profile.value, "canary-browser-secret");
  assert.deepEqual(calls, [{ alias: "github", purpose: "browser.password" }]);
});

test("secret fill fails closed without leaking resolver values", async (t) => {
  const app = await fixture({
    secretResolver: async () => {
      throw new Error("provider said canary-browser-secret");
    },
  });
  t.after(app.close);
  await app.request("/browser/v1/wake", {});

  const response = await app.request("/browser/v1/secret/fill", {
    alias: "github",
    locator: "#password",
    purpose: "browser.password",
  });

  assert.equal(response.status, 503);
  const body = await response.text();
  assert.equal(body.includes("canary-browser-secret"), false);
  assert.deepEqual(JSON.parse(body), { error: "browser_operation_failed" });
});

test("secret fill rejects non-browser purpose before resolver use", async (t) => {
  let resolved = false;
  const app = await fixture({
    secretResolver: async () => {
      resolved = true;
      return "canary-browser-secret";
    },
  });
  t.after(app.close);
  await app.request("/browser/v1/wake", {});

  const response = await app.request("/browser/v1/secret/fill", {
    alias: "github",
    locator: "#password",
    purpose: "rdc.github",
  });

  assert.equal(response.status, 400);
  assert.equal(resolved, false);
});


test("worker automatically checkpoints and sleeps after browser inactivity", async (t) => {
  const checkpoints = [];
  const stateStore = {
    async restore() {},
    async checkpoint() { checkpoints.push("checkpoint"); },
    async checkpointAuth() {},
    status() { return { enabled: true, restored: true, generation: checkpoints.length }; },
  };
  const app = await fixture({ idleSleepMs: 25, stateStore });
  t.after(async () => { await app.worker.close(); await app.close(); });

  assert.equal((await app.request("/browser/v1/wake", {})).status, 200);
  await new Promise((resolve) => setTimeout(resolve, 60));

  const status = await app.worker.callTool("browser_status");
  assert.equal(status.state, "sleeping");
  assert.equal(app.fake.profile.closed, true);
  assert.equal(checkpoints.length, 1);
});

test("idle sleep does not interrupt a browser operation that is still running", async (t) => {
  const fake = fakeChromium();
  const launch = fake.chromium.launchPersistentContext;
  fake.chromium.launchPersistentContext = async (directory) => {
    const context = await launch(directory);
    context.pages()[0].screenshot = async () => {
      await new Promise((resolve) => setTimeout(resolve, 80));
      return Buffer.from("png");
    };
    return context;
  };
  const app = await fixture({ idleSleepMs: 25, chromium: fake.chromium });
  t.after(async () => { await app.worker.close(); await app.close(); });

  assert.equal((await app.request("/browser/v1/wake", {})).status, 200);
  const screenshot = await app.worker.callTool("screenshot");
  assert.equal(screenshot.mediaType, "image/png");
  // trackActivity cancels the idle timer that wake armed, so Chrome stays open for the whole
  // 80 ms screenshot even though a 25 ms idle window would have passed in that time.
  assert.notEqual(fake.profile.closed, true);

  // After the operation settled, the browser sleeps once the idle window passes again.
  for (let attempt = 0; attempt < 200 && fake.profile.closed !== true; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.equal(fake.profile.closed, true);
});

test("idle sleep waits for every overlapping browser operation, not only the first to settle", async (t) => {
  const fake = fakeChromium();
  const launch = fake.chromium.launchPersistentContext;
  let screenshots = 0;
  fake.chromium.launchPersistentContext = async (directory) => {
    const context = await launch(directory);
    context.pages()[0].screenshot = async () => {
      screenshots += 1;
      // The first screenshot settles while the second is still running.
      await new Promise((resolve) => setTimeout(resolve, screenshots === 1 ? 5 : 150));
      if (fake.profile.closed) throw new Error("Target closed");
      return Buffer.from("png");
    };
    return context;
  };
  const worker = createWorker({ token: "test-token", profileDir: "/state/profile", chromium: fake.chromium, idleSleepMs: 25 });
  t.after(() => worker.close());

  await worker.callTool("wake");
  const results = await Promise.allSettled([worker.callTool("screenshot"), worker.callTool("screenshot")]);
  assert.deepEqual(results.map((result) => result.status), ["fulfilled", "fulfilled"]);
  // The first settle re-armed the idle timer; it must not have put Chrome to sleep under the second screenshot.
  assert.notEqual(fake.profile.closed, true);
  await waitFor(() => fake.profile.closed === true);
});

test("idle sleep defers while a takeover grant is active or a takeover websocket is open", async (t) => {
  let grantActive = true;
  let openSockets = 0;
  const fake = fakeChromium();
  const takeover = {
    startDisplay() {},
    start() { throw new Error("must_not_start"); },
    stop() {},
    close() {},
    status() { return grantActive ? { active: true, expiresAt: 0 } : { active: false }; },
    openConnectionCount() { return openSockets; },
    async proxyHttp() { return false; },
    proxyUpgrade() {},
  };
  const worker = createWorker({ token: "test-token", profileDir: "/state/profile", chromium: fake.chromium, takeoverEnabled: true, takeoverRuntime: takeover, idleSleepMs: 10 });
  t.after(() => worker.close());

  await worker.callTool("wake");
  // Grant issued, human not yet connected: Chrome stays awake.
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.notEqual(fake.profile.closed, true);

  // Grant status cleared but a websocket is still connected: Chrome stays awake.
  grantActive = false;
  openSockets = 1;
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.notEqual(fake.profile.closed, true);

  openSockets = 0;
  await waitFor(() => fake.profile.closed === true);
});

test("idle sleep keeps Chrome open for a connected takeover websocket and ends both when the grant expires", async (t) => {
  const upstreamSockets = new Set();
  const upstream = createTcpServer((socket) => {
    upstreamSockets.add(socket);
    socket.on("error", () => {});
    socket.on("close", () => upstreamSockets.delete(socket));
  });
  const upstreamPort = await listen(upstream);
  t.after(() => {
    for (const socket of upstreamSockets) socket.destroy();
    upstream.close();
  });

  const fake = fakeChromium();
  const takeover = createTakeover({ ttlMs: 120, upstreamPort, spawnProcess: () => ({ kill() {} }) });
  const worker = createWorker({ token: "test-token", profileDir: "/state/profile", chromium: fake.chromium, takeoverEnabled: true, takeoverRuntime: takeover, idleSleepMs: 20 });
  t.after(() => worker.close());

  await worker.callTool("wake");
  const grant = takeover.start();
  const frontend = createTcpServer();
  const frontendPort = await listen(frontend);
  const accepted = once(frontend, "connection");
  const client = createConnection({ host: "127.0.0.1", port: frontendPort });
  client.on("error", () => {});
  t.after(() => client.destroy());
  const [serverSocket] = await accepted;
  frontend.close();
  worker.takeover.proxyUpgrade(
    { method: "GET", url: `/browser/v1/takeover/websockify?takeover_token=${grant.token}`, headers: {} },
    serverSocket,
    Buffer.alloc(0),
  );

  // Chrome must stay open for as long as the human socket is connected; the idle timer keeps re-arming.
  let closedWhileConnected = false;
  const deadline = Date.now() + 1000;
  while (!serverSocket.destroyed && Date.now() < deadline) {
    if (fake.profile.closed) closedWhileConnected = true;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.equal(closedWhileConnected, false);
  assert.equal(serverSocket.destroyed, true);
  await waitFor(() => fake.profile.closed === true);
});

test("a request that arrives while Chrome closes for sleep gets browser_sleeping, not a failure", async (t) => {
  const profile = { closing: false, closed: false };
  let releaseClose;
  const closeGate = new Promise((resolve) => { releaseClose = resolve; });
  const page = {
    url() { return "about:blank"; },
    async title() { return "Synthetic page"; },
    async screenshot() {
      if (profile.closing) throw new Error("Target page, context or browser has been closed");
      return Buffer.from("png");
    },
  };
  const chromium = {
    async launchPersistentContext() {
      return {
        pages: () => [page],
        async close() {
          profile.closing = true;
          await closeGate;
          profile.closed = true;
        },
      };
    },
  };
  const app = await fixture({ chromium });
  t.after(async () => { releaseClose(); await app.close(); });

  assert.equal((await app.request("/browser/v1/wake", {})).status, 200);
  const sleeping = app.request("/browser/v1/sleep", {});
  await waitFor(() => profile.closing);

  const during = await app.request("/browser/v1/screenshot", {});
  assert.equal(during.status, 409);
  assert.deepEqual(await during.json(), { error: "browser_sleeping" });

  releaseClose();
  assert.equal((await sleeping).status, 200);
  assert.equal(profile.closed, true);
});

test("idle sleep re-checks activity after MCP sessions close and keeps Chrome for an operation that started meanwhile", async (t) => {
  const fake = fakeChromium();
  let mcpClosing = false;
  let releaseMcp;
  const mcpGate = new Promise((resolve) => { releaseMcp = resolve; });
  let releaseScreenshot;
  const screenshotGate = new Promise((resolve) => { releaseScreenshot = resolve; });
  const launch = fake.chromium.launchPersistentContext;
  fake.chromium.launchPersistentContext = async (directory) => {
    const context = await launch(directory);
    context.pages()[0].screenshot = async () => {
      await screenshotGate;
      if (fake.profile.closed) throw new Error("Target closed");
      return Buffer.from("png");
    };
    return context;
  };
  const worker = createWorker({
    token: "test-token",
    profileDir: "/state/profile",
    chromium: fake.chromium,
    idleSleepMs: 25,
    createMcp: () => ({
      async handle() {},
      async close() {
        mcpClosing = true;
        await mcpGate;
      },
    }),
  });
  t.after(async () => { releaseMcp(); releaseScreenshot(); await worker.close(); });

  await worker.callTool("wake");
  await waitFor(() => mcpClosing);

  // A browser operation arrives while the idle sleep is still closing MCP sessions.
  const screenshot = worker.callTool("screenshot");
  await new Promise((resolve) => setTimeout(resolve, 10));
  releaseMcp();
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.notEqual(fake.profile.closed, true);

  releaseScreenshot();
  assert.equal((await screenshot).mediaType, "image/png");
  await waitFor(() => fake.profile.closed === true);
});

test("a standing MCP GET stream is not a running operation, so idle Chrome still sleeps", async (t) => {
  const fake = fakeChromium();
  const app = await fixture({ idleSleepMs: 25, chromium: fake.chromium });
  const stream = new AbortController();
  t.after(async () => { stream.abort(); await app.worker.close(); await app.close(); });
  const mcpHeaders = { authorization: "Bearer test-token", "content-type": "application/json", accept: "application/json, text/event-stream" };
  assert.equal((await app.request("/browser/v1/wake", {})).status, 200);

  // The handshake is request-bound work and completes, as the SDK client does.
  const initialize = await fetch(`${app.base}/browser/v1/mcp`, {
    method: "POST",
    headers: mcpHeaders,
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "probe", version: "1.0.0" } } }),
  });
  assert.equal(initialize.status, 200);
  const sessionId = initialize.headers.get("mcp-session-id");
  assert.ok(sessionId);
  await initialize.text();
  const initialized = await fetch(`${app.base}/browser/v1/mcp`, {
    method: "POST",
    headers: { ...mcpHeaders, "mcp-session-id": sessionId, "mcp-protocol-version": "2025-06-18" },
    body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }),
  });
  assert.equal(initialized.status, 202);
  await initialized.text();

  // The SDK keeps a standing GET SSE stream open for server-initiated messages. It sends nothing and
  // never ends on its own, so it must not count as a running operation.
  const standing = await fetch(`${app.base}/browser/v1/mcp`, {
    headers: { authorization: "Bearer test-token", accept: "text/event-stream", "mcp-session-id": sessionId, "mcp-protocol-version": "2025-06-18" },
    signal: stream.signal,
  });
  assert.equal(standing.status, 200);
  assert.match(standing.headers.get("content-type") ?? "", /text\/event-stream/);

  await waitFor(() => fake.profile.closed === true);
  assert.equal((await app.worker.callTool("browser_status")).state, "sleeping");
});

test("a REST browser call after idle sleep wakes Chrome instead of returning browser_sleeping", async (t) => {
  const fake = fakeChromium();
  let launches = 0;
  const launch = fake.chromium.launchPersistentContext;
  fake.chromium.launchPersistentContext = async (directory) => {
    launches += 1;
    return launch(directory);
  };
  const app = await fixture({ idleSleepMs: 25, chromium: fake.chromium });
  t.after(async () => { await app.worker.close(); await app.close(); });

  assert.equal((await app.request("/browser/v1/wake", {})).status, 200);
  await waitFor(() => fake.profile.closed === true);
  assert.equal(launches, 1);

  const navigate = await app.request("/browser/v1/navigate", { url: "https://example.test/after-sleep" });
  assert.equal(navigate.status, 200);
  assert.deepEqual(await navigate.json(), { url: "https://example.test/after-sleep", title: "Synthetic page" });
  assert.equal(launches, 2);
});

test("a failed Chrome close keeps the persistent context so the next wake does not launch a second one", async (t) => {
  const fake = fakeChromium();
  let launches = 0;
  let closeAttempts = 0;
  let checkpoints = 0;
  const launch = fake.chromium.launchPersistentContext;
  fake.chromium.launchPersistentContext = async (directory) => {
    launches += 1;
    const context = await launch(directory);
    return {
      pages: context.pages,
      close() {
        closeAttempts += 1;
        // First attempt throws synchronously, second rejects asynchronously, third succeeds.
        if (closeAttempts === 1) throw new Error("synthetic close failure");
        if (closeAttempts === 2) return Promise.reject(new Error("synthetic async close failure"));
        return context.close();
      },
    };
  };
  const stateStore = {
    async restore() {},
    async checkpoint() { checkpoints += 1; },
    async checkpointAuth() {},
    status() { return { enabled: true, restored: true, generation: checkpoints }; },
  };
  const app = await fixture({ chromium: fake.chromium, stateStore });
  t.after(async () => { await app.worker.close(); await app.close(); });

  assert.equal((await app.request("/browser/v1/wake", {})).status, 200);
  const failed = await app.request("/browser/v1/sleep", {});
  assert.equal(failed.status, 500);
  assert.deepEqual(await failed.json(), { error: "browser_operation_failed" });
  // The checkpoint is skipped because Chrome did not close cleanly.
  assert.equal(checkpoints, 0);

  // The browser stays awake and retriable: waking does not launch a second persistent context on the profile.
  assert.equal((await app.request("/browser/v1/wake", {})).status, 200);
  assert.equal(launches, 1);
  assert.equal((await app.worker.callTool("browser_status")).state, "awake");
  // The restored page serves REST calls directly; the failed close did not leave the browser in browser_sleeping.
  assert.equal((await app.request("/browser/v1/navigate", { url: "https://example.test/retry" })).status, 200);

  // The same holds for an asynchronous rejection, and a REST call never gets stuck in browser_sleeping.
  assert.equal((await app.request("/browser/v1/sleep", {})).status, 500);
  assert.equal(checkpoints, 0);
  assert.equal((await app.request("/browser/v1/wake", {})).status, 200);
  assert.equal(launches, 1);

  assert.equal((await app.request("/browser/v1/sleep", {})).status, 200);
  assert.equal(checkpoints, 1);
  assert.equal((await app.request("/browser/v1/wake", {})).status, 200);
  assert.equal(launches, 2);
});
