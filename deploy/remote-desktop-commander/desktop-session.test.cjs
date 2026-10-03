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
  return { calls, signals, spawn, probe: async () => true, sleep: async () => {} };
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
  assert.equal(h.calls[0].options.stdio, "ignore");
  assert(h.calls[1].args.includes("/opt/desktop-commander/pairing-handoff.cjs"));
  assert(h.calls.every(({ child }) => child.killedWith.includes("SIGTERM")));
  assert.equal(h.signals.listenerCount("SIGTERM"), 0);
});

test("browser startup failure does not start RDC or pairing", async () => {
  const h = harness((child) => child.emit("error", new Error("private detail")));
  assert.equal(await supervise(["remote"], h), 1);
  assert.equal(h.calls.length, 1);
});

test("readiness timeout stops browser and never starts RDC", async () => {
  const h = harness();
  assert.equal(await supervise(["remote"], { ...h, startupTimeoutMs: 0 }), 1);
  assert.equal(h.calls.length, 1);
  assert(h.calls[0].child.killedWith.includes("SIGTERM"));
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
