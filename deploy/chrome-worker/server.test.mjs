import assert from "node:assert/strict";
import { once } from "node:events";
import { createServer } from "node:http";
import test from "node:test";

import { createWorker } from "./server.mjs";

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
  return { fake, worker, workerProfile, request, close: () => new Promise((resolve) => server.close(resolve)) };
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
