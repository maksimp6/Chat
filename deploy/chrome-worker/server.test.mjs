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

async function fixture() {
  const fake = fakeChromium();
  const worker = createWorker({ token: "test-token", allowedHosts: "example.test", profileDir: "/state/profile", chromium: fake.chromium });
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
  return { fake, worker, request, close: () => new Promise((resolve) => server.close(resolve)) };
}

test("worker rejects anonymous calls and wakes and sleeps a persistent profile", async (t) => {
  const app = await fixture();
  t.after(app.close);
  assert.equal((await app.request("/browser/v1/status", undefined, "wrong")).status, 401);
  assert.deepEqual(await (await app.request("/browser/v1/status")).json(), { state: "sleeping", generation: 0, url: null, title: null });
  assert.equal((await app.request("/browser/v1/wake", {})).status, 200);
  assert.equal(app.fake.profile.directory, "/state/profile");
  assert.equal((await app.request("/browser/v1/sleep", {})).status, 200);
  assert.equal(app.fake.profile.closed, true);
});

test("worker bounds navigation and exposes the same operations over MCP", async (t) => {
  const app = await fixture();
  t.after(app.close);
  await app.request("/browser/v1/wake", {});
  assert.equal((await app.request("/browser/v1/navigate", { url: "http://127.0.0.1/private" })).status, 403);
  assert.equal((await app.request("/browser/v1/navigate", { url: "https://example.test/form" })).status, 200);

  let response = await app.request("/browser/v1/mcp", { jsonrpc: "2.0", id: 1, method: "tools/list" });
  const names = (await response.json()).result.tools.map((tool) => tool.name);
  assert.deepEqual(names, ["browser_status", "wake", "sleep", "navigate", "click", "type", "extract", "screenshot"]);

  response = await app.request("/browser/v1/mcp", { jsonrpc: "2.0", id: 2, method: "tools/call", params: { name: "type", arguments: { locator: "#name", text: "Alice" } } });
  assert.equal((await response.json()).result.structuredContent.ok, true);
  assert.equal(app.fake.profile.value, "Alice");
});
