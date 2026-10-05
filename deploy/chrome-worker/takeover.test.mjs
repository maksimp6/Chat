import assert from "node:assert/strict";
import test from "node:test";
import { createTakeover } from "./takeover.mjs";

function fakeChild() {
  return { killed: false, kill() { this.killed = true; } };
}

test("takeover is single-owner, short-lived, and raw services stay process-local", () => {
  let clock = 1_000;
  const calls = [];
  const children = [];
  const takeover = createTakeover({
    now: () => clock,
    ttlMs: 600_000,
    spawnProcess(command, args) {
      calls.push([command, args]);
      const child = fakeChild();
      children.push(child);
      return child;
    },
  });

  takeover.startDisplay();
  takeover.startDisplay();
  assert.equal(calls.length, 3);
  assert.deepEqual(calls.map(([command]) => command), ["Xvfb", "x11vnc", "websockify"]);
  assert.ok(calls[1][1].includes("-localhost"));
  assert.ok(calls[2][1].includes("127.0.0.1:6080"));

  const first = takeover.start();
  assert.equal(takeover.status().active, true);
  assert.throws(() => takeover.start(), /takeover_already_active/);

  const request = { headers: { cookie: `browser_takeover=${first.token}` } };
  assert.equal(takeover.authorized(new URL("https://worker/browser/v1/takeover/app/ui.js"), request), true);
  assert.equal(takeover.authorized(new URL("https://worker/browser/v1/takeover/app/ui.js"), { headers: {} }), false);

  takeover.stop();
  assert.equal(takeover.status().active, false);
  const second = takeover.start();
  assert.notEqual(second.token, first.token);

  clock = second.expiresAt + 1;
  assert.equal(takeover.status().active, false);
  assert.equal(children.some((child) => child.killed), false);
  takeover.close();
  assert.equal(children.every((child) => child.killed), true);
});
