"use strict";

// Request dispatcher on the BrowserShim: real static/core_api.js and static/dispatcher.js,
// with fetch responses that the test resolves by hand.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

function bootDispatcher() {
  const pending = [];
  const fetch = (url) =>
    new Promise((resolve, reject) => {
      pending.push({ url, resolve, reject });
    });
  const shim = new BrowserShim("<body></body>");
  const { window } = shim.load(["static/core_api.js", "static/dispatcher.js"], { fetch });
  return { api: window.LogosCoreAPI, dispatcher: window.LogosDispatcher, pending };
}

async function settle() {
  for (let i = 0; i < 10; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

test("a response that fails while being reported releases its request slot only once", async () => {
  const { api, dispatcher, pending } = bootDispatcher();
  dispatcher.request("/a");
  const failing = dispatcher.request("/b");
  const outcome = failing.then(
    () => "resolved",
    () => "rejected",
  );

  // The response's ok flag is read by the trace (twice) and then by the status report.
  // The third read throws, after the slot has been released.
  let reads = 0;
  pending[1].resolve({
    get ok() {
      reads += 1;
      if (reads === 3) throw new Error("unreadable response");
      return true;
    },
    status: 200,
  });
  await settle();
  assert.equal(await outcome, "rejected");

  // The first request is still in flight, so a new one must still count as running.
  dispatcher.request("/c");
  pending[2].resolve({ ok: true, status: 200 });
  await settle();
  assert.equal(api.status.snapshot().network.status, "running");
});

test("generic requests time out at 10 seconds while a 14-second chat stays active", async () => {
  let elapsed = 0;
  const pending = [];
  const shim = new BrowserShim("<body></body>");
  const { window } = shim.load(["static/core_api.js", "static/dispatcher.js"], {
    AbortSignal: { timeout: (ms) => ({ deadline: elapsed + ms, timeoutMs: ms }) },
    fetch: (url, init) => new Promise((resolve, reject) => pending.push({ url, signal: init.signal, resolve, reject })),
  });
  const dispatcher = window.LogosDispatcher;
  const generic = dispatcher.request("/api/conversations").catch((error) => error.message);
  const chat = dispatcher.request("/api/chat", { method: "POST" }, { timeoutMs: 300000 });
  assert.equal(dispatcher.isBusy(), true);
  assert.equal(pending[0].signal.timeoutMs, 10000);
  assert.equal(pending[1].signal.timeoutMs, 300000);
  elapsed = 14000;
  for (const request of pending) {
    if (request.signal.deadline <= elapsed) request.reject(new Error("timeout"));
  }
  assert.equal(await generic, "timeout");
  assert.equal(dispatcher.isBusy(), true);
  pending[1].resolve({ ok: true, status: 200 });
  assert.equal((await chat).status, 200);
  assert.equal(dispatcher.isBusy(), false);
});

test("invalid timeouts use the default and oversized timeouts stay bounded", async () => {
  const seen = [];
  const shim = new BrowserShim("<body></body>");
  const { window } = shim.load(["static/dispatcher.js"], {
    AbortSignal: { timeout: (ms) => (seen.push(ms), {}) },
    fetch: () => Promise.resolve({ ok: true, status: 200 }),
  });
  for (const timeoutMs of [NaN, "invalid", -1, Infinity, 999999]) {
    await window.LogosDispatcher.request("/fixture", {}, { timeoutMs });
  }
  assert.deepEqual(seen, [10000, 10000, 10000, 10000, 300000]);
  assert.equal(window.LogosDispatcher.isBusy(), false);
});
