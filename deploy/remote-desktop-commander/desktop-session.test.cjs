"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { browserArgs, healthy, waitHealthy, supervise } = require("./desktop-session.cjs");

function harness(onLaunch = () => {}) {
  const signals = new EventEmitter();
  const calls = [];
  function spawn(command, args, options) {
    const child = new EventEmitter();
    child.exitCode = null;
    child.signalCode = null;
    child.killedWith = [];
    child.kill = (signal) => {
      child.killedWith.push(signal);
      if (child.exitCode === null && child.signalCode === null) {
        child.signalCode = signal;
        queueMicrotask(() => child.emit("exit", null, signal));
      }
      return true;
    };
    calls.push({ command, args, options, child });
    queueMicrotask(() => onLaunch(child, calls.length, signals));
    return child;
  }
  let closes = 0;
  let probes = 0;
  return { calls, signals, spawn, probe: async () => ++probes > 1, sleep: async () => {},
    closeBrowser: async () => { closes++; }, get closes() { return closes; } };
}

test("browser keeps its sandbox, private control endpoint and durable profile", () => {
  assert(!browserArgs.includes("--no-sandbox"));
  assert(browserArgs.includes("--remote-debugging-address=127.0.0.1"));
  assert(browserArgs.includes("--user-data-dir=/home/node/.config/chromium"));
});

test("health probe handles private errors and malformed responses without output", async () => {
  assert(await healthy(async () => ({ ok: true, json: async () => ({ Browser: "Chrome/123" }) })));
  assert(!(await healthy(async () => ({ ok: false }))));
  assert(!(await healthy(async () => ({ ok: true, json: async () => ({ Browser: "other" }) }))));
  assert(!(await healthy(async () => { throw new Error("synthetic-private-url"); })));
  assert(!(await waitHealthy(async () => false, 0)));
  assert(await waitHealthy(async () => true, 50));
});

test("TERM stops both children and removes handlers", async () => {
  const h = harness((_child, number, signals) => { if (number === 2) signals.emit("SIGTERM"); });
  assert.equal(await supervise(["remote", "--disable-no-sleep"], h), 0);
  assert.equal(h.calls.length, 2);
  assert.equal(h.closes, 1);
  assert.equal(h.calls[0].options.stdio, "ignore");
  assert(h.calls[1].args.includes("/opt/desktop-commander/pairing-handoff.cjs"));
  assert(h.calls.every(({ child }) => child.killedWith.includes("SIGTERM")));
  assert.equal(h.signals.listenerCount("SIGTERM"), 0);
});

test("browser startup failure does not start RDC or pairing", async () => {
  const h = harness((child) => child.emit("error", new Error("private detail")));
  assert.equal(await supervise(["remote"], h), 1);
  assert.equal(h.calls.length, 1);
  assert.equal(h.closes, 0);
});

test("readiness timeout stops browser and never starts RDC", async () => {
  const h = harness();
  assert.equal(await supervise(["remote"], { ...h, startupTimeoutMs: 0 }), 1);
  assert.equal(h.calls.length, 1);
  assert(h.calls[0].child.killedWith.includes("SIGTERM"));
});

test("an existing browser is not taken over or closed", async () => {
  const h = harness();
  assert.equal(await supervise(["remote"], { ...h, probe: async () => true }), 1);
  assert.equal(h.calls.length, 0);
  assert.equal(h.closes, 0);
});

test("unexpected child completion tears down the session as failure", async () => {
  const h = harness((child, number) => {
    if (number === 2) { child.exitCode = 0; child.emit("exit", 0); }
  });
  assert.equal(await supervise(["remote"], h), 1);
  assert(h.calls[0].child.killedWith.includes("SIGTERM"));
});

test("smoke starts the synthetic test instead of the authenticated RDC client", async () => {
  const h = harness((child, number) => {
    if (number === 2) { child.exitCode = 0; child.emit("exit", 0); }
  });
  assert.equal(await supervise(["--browser-smoke-test"], h), 0);
  assert.deepEqual(h.calls[1].args, ["/opt/desktop-commander/browser-smoke.cjs"]);
  assert(h.calls.every(({ args }) => !args.includes("remote")));
});

test("cloud probe serves readiness only after synthetic rendering, never starts RDC", { timeout: 5000 }, async () => {
  let port;
  let release;
  const rendered = new Promise((resolve) => { release = resolve; });
  const h = harness((child, number) => {
    if (number === 2) release(child);
  });
  const running = supervise(["--cloud-probe"], {
    ...h, port: 0, onListening: (value) => { port = value; },
  });
  const smoke = await rendered;
  try {
    let response = await fetch(`http://127.0.0.1:${port}/healthz`);
    assert.equal(response.status, 503);
    assert.equal((await response.json()).browser_ready, false);
    smoke.exitCode = 0;
    smoke.emit("exit", 0);
    response = await fetch(`http://127.0.0.1:${port}/healthz`);
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), {
      mode: "cloud-probe", browser_ready: true, smoke_passed: true,
    });
    for (const path of ["/json/version", "/rdc", "/healthz?secret=x"]) {
      assert.equal((await fetch(`http://127.0.0.1:${port}${path}`)).status, 404);
    }
    assert.equal((await fetch(`http://127.0.0.1:${port}/healthz`, { method: "POST" })).status, 404);
    assert.deepEqual(h.calls[1].args, ["/opt/desktop-commander/browser-smoke.cjs"]);
    assert(h.calls.every(({ args }) => !args.includes("remote")));
  } finally {
    h.signals.emit("SIGTERM");
    assert.equal(await running, 0);
  }
});

test("cloud probe rejects invalid port and stops on failed smoke", async () => {
  const badPort = harness();
  assert.equal(await supervise(["--cloud-probe"], { ...badPort, port: -1 }), 1);
  assert.equal(badPort.calls.length, 0);
  const failed = harness((child, number) => {
    if (number === 2) { child.exitCode = 1; child.emit("exit", 1); }
  });
  assert.equal(await supervise(["--cloud-probe"], { ...failed, port: 0 }), 1);
  assert(failed.calls[0].child.killedWith.includes("SIGTERM"));
});
