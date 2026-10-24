"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");
const OLD = "a".repeat(64), NEW = "b".repeat(64);

function boot({ draft = "", busy = false, modal = false, fail = false } = {}) {
  const shim = new BrowserShim('<div id="app-version"></div><input id="msg-input"><div id="dialog" class="modal"></div>');
  shim.document.currentScript = { dataset: { staticVersion: OLD, appRevision: "1".repeat(40) } };
  shim.document.getElementById("msg-input").value = draft;
  const dialog = shim.document.getElementById("dialog");
  dialog.hidden = !modal;
  if (modal) dialog.classList.add("visible");
  const state = { version: OLD, busy, fail, reloads: 0, calls: [], intervals: [] };
  shim.window.location.href = "http://localhost/?conversation_id=retained";
  shim.window.location.reload = () => { state.reloads += 1; };
  const loaded = shim.load(["static/boot.js"], {
    navigator: {}, Headers,
    setInterval: (callback, delay) => { state.intervals.push({ callback, delay }); return 1; },
    LogosDispatcher: { isBusy: () => state.busy },
    fetch: async (url, options) => {
      state.calls.push({ url, options });
      if (state.fail) throw new Error("offline");
      return { ok: true, json: async () => ({ ui_version: state.version }) };
    },
  });
  return { ...loaded, state };
}
async function settle() { for (let i = 0; i < 5; i++) await new Promise(resolve => setTimeout(resolve, 0)); }

test("new UI reloads the same selected-conversation URL only when idle", async () => {
  const { window, document, state } = boot();
  await settle();
  assert.equal(state.reloads, 0);
  assert.equal(document.getElementById("app-version").getAttribute("data-update-state"), "current");
  assert.equal(state.intervals[0].delay, 30000);
  state.version = NEW;
  await window.LogosVersion.check();
  assert.equal(state.reloads, 1);
  assert.equal(window.location.href, "http://localhost/?conversation_id=retained");
  assert.equal(state.calls.at(-1).url, "/api/version");
  assert.equal(state.calls.at(-1).options.cache, "no-store");
});

test("a typed draft survives and defers the automatic update", async () => {
  const { window, document, state } = boot({ draft: "unsent words" });
  await settle(); state.version = NEW;
  await window.LogosVersion.check();
  assert.equal(state.reloads, 0);
  assert.equal(document.getElementById("msg-input").value, "unsent words");
  assert.equal(document.getElementById("app-version").getAttribute("data-update-state"), "pending");
  document.getElementById("msg-input").value = "";
  await window.LogosVersion.check();
  assert.equal(state.reloads, 1);
});

test("an in-flight response and open settings keep the current UI alive", async () => {
  const { window, document, state } = boot({ busy: true, modal: true });
  await settle(); state.version = NEW;
  await window.LogosVersion.check();
  assert.equal(state.reloads, 0);
  state.busy = false;
  await window.LogosVersion.check();
  assert.equal(state.reloads, 0);
  const dialog = document.getElementById("dialog"); dialog.hidden = true; dialog.classList.remove("visible");
  await window.LogosVersion.check();
  assert.equal(state.reloads, 1);
});

test("saved text in closed settings does not block an otherwise idle update", async () => {
  const { window, document, state } = boot();
  const saved = document.createElement("textarea");
  saved.value = "saved model instructions";
  document.getElementById("dialog").appendChild(saved);
  await settle(); state.version = NEW;
  await window.LogosVersion.check();
  assert.equal(state.reloads, 1);
});

test("a failed version request is unknown, never a false current state", async () => {
  const { window, document, state } = boot({ fail: true });
  await settle();
  assert.equal(state.reloads, 0);
  assert.equal(document.getElementById("app-version").getAttribute("data-update-state"), "unknown");
  state.fail = false;
  await window.LogosVersion.check();
  assert.equal(document.getElementById("app-version").getAttribute("data-update-state"), "current");
});

test("retired service worker deletes only Logos caches and always uses the network", async () => {
  const listeners = {}, deleted = [], requests = [];
  let claimed = false, unregistered = false, skipped = false;
  const context = {
    self: {
      addEventListener: (name, callback) => { listeners[name] = callback; },
      skipWaiting: async () => { skipped = true; },
      clients: { claim: async () => { claimed = true; } },
      registration: { unregister: async () => { unregistered = true; } },
    },
    caches: { keys: async () => ["logos-pro-v2", "unrelated"], delete: async key => { deleted.push(key); } },
    fetch: async (request, options) => { requests.push({ request, options }); return "fresh"; },
  };
  vm.runInNewContext(fs.readFileSync("static/sw.js", "utf8"), context);
  let completion;
  listeners.install({ waitUntil: promise => { completion = promise; } }); await completion;
  listeners.activate({ waitUntil: promise => { completion = promise; } }); await completion;
  assert.equal(skipped, true); assert.equal(claimed, true); assert.equal(unregistered, true);
  assert.deepEqual(deleted, ["logos-pro-v2"]);
  listeners.fetch({ request: "/", respondWith: promise => { completion = promise; } });
  assert.equal(await completion, "fresh");
  assert.equal(requests[0].options.cache, "no-store");
});
