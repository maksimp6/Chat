"use strict";

// Core startup on the BrowserShim: real static/core_api.js and static/core.js,
// mocked dispatcher responses keyed by URL.

const assert = require("node:assert/strict");
const test = require("node:test");
const vm = require("node:vm");
const { BrowserShim } = require("../browser/emulator/browser_dom");

function reply(status, body) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

function boot({ routes = {}, storage = {} } = {}) {
  const requests = [];
  const dispatcher = {
    request: (url, options = {}) => {
      requests.push({ url, method: options.method || "GET" });
      const route = routes[url];
      if (!route) return reply(500, { error: "no mocked response for " + url });
      return reply(route.status || 200, route.body);
    },
  };
  const shim = new BrowserShim("<body></body>");
  for (const [key, value] of Object.entries(storage)) {
    shim.window.localStorage.setItem(key, value);
  }
  const { window, context } = shim.load(["static/core_api.js", "static/core.js"], {
    LogosDispatcher: dispatcher,
  });
  return { window, context, requests };
}

async function settle() {
  for (let i = 0; i < 10; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

function coreValue(context, name) {
  return vm.runInContext(name, context);
}

const HAIKU = "claude-haiku-5-5";
const CONVERSATIONS = { conversations: [{ id: "c1", title: "Диалог", model: HAIKU }] };
const CATALOG = { text: { [HAIKU]: { multimodal: false } }, voice: {} };

test("a working catalog restores the conversation's saved model", async () => {
  const { window, context } = boot({
    storage: { current_conv_id: "c1" },
    routes: {
      "/api/conversations": { body: CONVERSATIONS },
      "/api/models": { body: CATALOG },
    },
  });
  await settle();

  assert.equal(coreValue(context, "currentModel"), HAIKU);
  assert.equal(window.localStorage.getItem("current_model"), HAIKU);
});

test("a missing model in a loaded catalog falls back to the default model", async () => {
  const { window, context } = boot({
    storage: { current_conv_id: "c1" },
    routes: {
      "/api/conversations": {
        body: { conversations: [{ id: "c1", model: "retired-model" }] },
      },
      "/api/models": { body: CATALOG },
    },
  });
  await settle();

  assert.equal(coreValue(context, "currentModel"), "logos-runtime");
});

test("a degraded catalog keeps the conversation's saved model instead of overwriting it", async () => {
  const { window, context } = boot({
    storage: { current_conv_id: "c1" },
    routes: {
      "/api/conversations": { body: CONVERSATIONS },
      "/api/models": { status: 500, body: { error: "discovery offline" } },
    },
  });
  await settle();

  assert.equal(coreValue(context, "currentModel"), HAIKU);
  assert.equal(window.localStorage.getItem("current_model"), HAIKU);
});

test("a failed conversation list starts from the cached conversations", async () => {
  const cached = JSON.stringify([{ id: "c9", title: "Из кэша" }]);
  const { context } = boot({
    storage: { conversations: cached },
    routes: {
      "/api/conversations": { status: 503, body: { error: "unavailable" } },
      "/api/models": { body: CATALOG },
    },
  });
  await settle();

  assert.equal(coreValue(context, "conversations.length"), 1);
  assert.equal(coreValue(context, "conversations[0].title"), "Из кэша");
});
