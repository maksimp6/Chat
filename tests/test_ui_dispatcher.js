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
