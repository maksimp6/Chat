"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { browserArgs, healthy, waitHealthy, supervise, superviseCloudRdc, pairingHandoff, processGroupGone, containerWritersGone } = require("./desktop-session.cjs");

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

function cloudHarness(options = {}) {
  const h = harness();
  const commands = [];
  const summary = { state_ready: true, paired: false, device_id: null, checkpoint_generation: 0 };
  let started;
  const rdcStarted = new Promise((resolve) => { started = resolve; });
  const launch = h.spawn;
  h.spawn = (command, args, config) => {
    const child = launch(command, args, config);
    child.kill = (signal) => {
      child.killedWith.push(signal);
      if (signal === "SIGTERM" && h.calls.length === 2 && child === h.calls[1].child && !options.hungRdc) {
        child.exitCode = options.rdcExitCode ?? 0;
        queueMicrotask(() => child.emit("exit", child.exitCode, null));
      } else if (signal === "SIGKILL") {
        child.signalCode = signal;
        queueMicrotask(() => child.emit("exit", null, signal));
      }
      return true;
    };
    if (h.calls.length === 2) queueMicrotask(() => started(child));
    return child;
  };
  h.closeBrowser = async () => {
    if (options.hungBrowser) return;
    const child = h.calls[0].child;
    child.exitCode = options.browserExitCode ?? 0;
    queueMicrotask(() => child.emit("exit", child.exitCode, null));
  };
  h.authSignature = () => null;
  h.processGroupGone = () => !options.lingeringExecutor;
  h.containerWritersGone = () => !options.escapedExecutor;
  h.stateHelper = async (command) => {
    commands.push(command);
    if (command === "status") return { ...summary };
    if (command === "save-auth") {
      if (options.failAuth) throw new Error("synthetic-private-token");
      summary.paired = true;
      summary.device_id = "11111111-1111-1111-1111-111111111111";
      return { status: "AUTH_SAVED", generation: 1 };
    }
    assert.equal(command, "checkpoint");
    assert(h.calls.every(({ child }) => child.exitCode === 0 && child.signalCode === null));
    summary.checkpoint_generation++;
    return { status: "CHECKPOINT_COMPLETE", generation: summary.checkpoint_generation };
  };
  return { h, commands, rdcStarted };
}

test("persistent cloud session reports only durable summary and suppresses raw RDC output", { timeout: 5000 }, async () => {
  const { h, commands, rdcStarted } = cloudHarness();
  let port;
  const running = superviseCloudRdc(["remote", "--disable-no-sleep"], {
    ...h, port: 0, onListening: (value) => { port = value; },
  });
  const rdc = await rdcStarted;
  try {
    const response = await fetch(`http://127.0.0.1:${port}/healthz`);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(await response.json(), {
      mode: "cloud-rdc", browser_ready: true, rdc_running: true, state_ready: true,
      paired: false, device_id: null, checkpoint_generation: 0,
    });
    assert.deepEqual(h.calls[1].options.stdio, ["ignore", "ignore", "ignore", "ipc"]);
    assert(h.calls.every(({ options }) => options.detached === true));
    for (const route of ["/json/version", "/healthz?secret=x", "/checkpoint?secret=x"]) {
      assert.equal((await fetch(`http://127.0.0.1:${port}${route}`)).status, 404);
    }
    rdc.emit("message", { type: "rdc-auth-committed", access_token: "synthetic-private-token" });
    assert(!commands.includes("save-auth"));
    rdc.emit("message", { type: "rdc-auth-committed" });
    for (let attempt = 0; attempt < 10; attempt++) {
      const status = await (await fetch(`http://127.0.0.1:${port}/healthz`)).json();
      if (status.paired) {
        assert.equal(status.device_id, "11111111-1111-1111-1111-111111111111");
        break;
      }
      await new Promise((resolve) => setImmediate(resolve));
    }
    assert(commands.includes("save-auth"));
  } finally {
    h.signals.emit("SIGTERM");
    assert.equal(await running, 0);
  }
});

test("checkpoint closes both children before snapshot, keeps server quiesced and never restarts", { timeout: 5000 }, async () => {
  const { h, commands, rdcStarted } = cloudHarness();
  let port;
  const running = superviseCloudRdc(["remote"], { ...h, port: 0, onListening: (value) => { port = value; } });
  await rdcStarted;
  try {
    const endpoint = `http://127.0.0.1:${port}`;
    const invalid = await fetch(endpoint + "/checkpoint", { method: "POST", body: "private" });
    assert.equal(invalid.status, 400);
    assert.equal(commands.filter((value) => value === "checkpoint").length, 0);
    const response = await fetch(endpoint + "/checkpoint", { method: "POST" });
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { status: "CHECKPOINT_COMPLETE", generation: 1 });
    assert.deepEqual(await (await fetch(endpoint + "/checkpoint", { method: "POST" })).json(), {
      status: "CHECKPOINT_COMPLETE", generation: 1,
    });
    const health = await fetch(endpoint + "/healthz");
    assert.equal(health.status, 503);
    const state = await health.json();
    assert.equal(state.browser_ready, false);
    assert.equal(state.rdc_running, false);
    assert.equal(state.checkpoint_generation, 1);
    assert.equal(h.calls.length, 2);
    assert.equal(commands.filter((value) => value === "checkpoint").length, 1);
  } finally {
    h.signals.emit("SIGTERM");
    assert.equal(await running, 0);
  }
});

test("forced or failed child shutdown never creates a fresh checkpoint", { timeout: 5000 }, async () => {
  for (const settings of [{ hungRdc: true }, { hungBrowser: true }, { rdcExitCode: 1 },
    { browserExitCode: 1 }, { lingeringExecutor: true }, { escapedExecutor: true }]) {
    const { h, commands, rdcStarted } = cloudHarness(settings);
    const running = superviseCloudRdc(["remote"], { ...h, port: 0, shutdownTimeoutMs: 10 });
    await rdcStarted;
    h.signals.emit("SIGTERM");
    assert.equal(await running, 1);
    assert(!commands.includes("checkpoint"));
  }
});

test("upstream auth write failure or journal failure terminates the cloud session", { timeout: 5000 }, async () => {
  for (const event of ["rdc-auth-write-failed", "rdc-auth-committed"]) {
    const { h, commands, rdcStarted } = cloudHarness({ failAuth: true });
    const running = superviseCloudRdc(["remote"], { ...h, port: 0 });
    const rdc = await rdcStarted;
    rdc.emit("message", { type: event });
    assert.equal(await running, 1);
    assert(!commands.includes("checkpoint"));
    assert(h.calls.every(({ child }) => child.killedWith.includes("SIGKILL")));
  }
});

test("mount or state recovery failure starts no browser or RDC", async () => {
  const { h } = cloudHarness();
  const result = await superviseCloudRdc(["remote"], {
    ...h, stateHelper: async () => ({ state_ready: false }), port: 0,
  });
  assert.equal(result, 1);
  assert.equal(h.calls.length, 0);
});

test("process group fence permits dead zombies and rejects living executor descendants", () => {
  const stat = (state, group) => `123 (name with ) parentheses) ${state} 1 ${group} ${Array(17).fill("0").join(" ")}`;
  const inspect = { kill: () => {}, entries: () => ["self", "123"], read: () => stat("Z", 456) };
  assert.equal(processGroupGone(456, inspect), true);
  assert.equal(processGroupGone(456, { ...inspect, read: () => stat("S", 456) }), false);
  assert.equal(processGroupGone(456, { ...inspect, read: () => stat("S", 789) }), false);
  assert.equal(processGroupGone(456, { ...inspect, kill: () => { throw Object.assign(new Error(), { code: "ESRCH" }); } }), true);
  assert.throws(() => processGroupGone(456, { ...inspect, kill: () => { throw Object.assign(new Error(), { code: "EPERM" }); } }));
  assert.throws(() => processGroupGone(456, { ...inspect, read: () => "malformed" }));
  let scans = 0;
  assert.equal(processGroupGone(456, { ...inspect, entries: () => ++scans === 1 ? ["123"] : ["124"] }), false);
});

test("container fence rejects a setsid writer after both original process groups are closed", { timeout: 5000 }, async () => {
  const stat = (pid, state, group) => `${pid} (executor) ${state} 1 ${group} ${Array(17).fill("0").join(" ")}`;
  const inspect = {
    entries: () => ["321", "777"],
    read: (pid, file) => file === "status" ? "Uid:\t1000\t1000\t1000\t1000\n" :
      stat(pid, pid === "321" ? "R" : "S", Number(pid)),
  };
  const { h, commands, rdcStarted } = cloudHarness();
  h.processGroupGone = () => true;
  h.containerWritersGone = () => containerWritersGone(1000, 321, inspect);
  const running = superviseCloudRdc(["remote"], { ...h, port: 0 });
  await rdcStarted;
  h.signals.emit("SIGTERM");
  assert.equal(await running, 1);
  assert(!commands.includes("checkpoint"));
  assert(h.calls.every(({ child }) => child.exitCode === 0 && !child.killedWith.includes("SIGKILL")));
});

test("container writer fence admits only known supervisor or stable dead UID1000 processes", () => {
  const stat = (pid, state, start = "1") => `${pid} (name with ) parentheses) ${state} 1 99 ${Array(16).fill("0").join(" ")} ${start}`;
  const inspect = {
    entries: () => ["self", "123", "456", "789"],
    read: (pid, file) => file === "status" ? (pid === "789" ? "Uid:\t0\t0\t0\t0\n" : "Uid:\t1000\t1000\t1000\t1000\n") :
      stat(pid, pid === "456" ? "Z" : "S"),
  };
  assert.equal(containerWritersGone(1000, 123, inspect), true);
  assert.equal(containerWritersGone(1000, 123, { ...inspect, read: (pid, file) => file === "stat" ? stat(pid, "R") : inspect.read(pid, file) }), false);
  assert.throws(() => containerWritersGone(0, 123, inspect));
  assert.throws(() => containerWritersGone(1000, 123, { ...inspect, read: () => "malformed" }));
  assert.throws(() => containerWritersGone(1000, 123, { ...inspect, read: () => "Uid:\t0\t1000\t1000\t1000\n" }));
  assert.throws(() => containerWritersGone(1000, 123, { ...inspect, read: () => { throw Object.assign(new Error(), { code: "EACCES" }); } }));
  assert.equal(containerWritersGone(1000, 123, { ...inspect, entries: () => ["456"] }), false);
  let reads = 0;
  assert.equal(containerWritersGone(1000, 123, {
    ...inspect, entries: () => ["123"], read: (_pid, file) => file === "stat" ? stat("123", "R", String(++reads)) : inspect.read("123", file),
  }), false);
});

test("checkpoint drains pending auth helpers before scanning container writers", { timeout: 5000 }, async () => {
  const { h, commands, rdcStarted } = cloudHarness();
  const helper = h.stateHelper;
  let release;
  let active = false;
  const saved = new Promise((resolve) => { release = resolve; });
  h.stateHelper = async (command) => {
    if (command === "save-auth") { active = true; await saved; active = false; }
    return helper(command);
  };
  let scans = 0;
  h.containerWritersGone = () => { assert.equal(active, false); scans++; return true; };
  let port;
  const running = superviseCloudRdc(["remote"], { ...h, port: 0, onListening: (value) => { port = value; } });
  const rdc = await rdcStarted;
  rdc.emit("message", { type: "rdc-auth-committed" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(active, true);
  const response = fetch(`http://127.0.0.1:${port}/checkpoint`, { method: "POST" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(scans, 0);
  release();
  assert.equal((await response).status, 200);
  assert.equal(scans, 1);
  assert(commands.includes("save-auth"));
  h.signals.emit("SIGTERM");
  assert.equal(await running, 0);
});

test("pairing redirect accepts only current bounded official HTTPS handoff", { timeout: 5000 }, async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "rdc-pair-server-"));
  const file = path.join(directory, "handoff.json");
  const value = { expires_at: Date.now() / 1000 + 60,
    verification_uri_complete: "https://mcp.desktopcommander.app/device/verify?code=synthetic" };
  const { h, rdcStarted } = cloudHarness();
  let port;
  const running = superviseCloudRdc(["remote"], {
    ...h, port: 0, pairingFile: file, onListening: (entry) => { port = entry; },
  });
  await rdcStarted;
  try {
    fs.writeFileSync(file, JSON.stringify(value), { mode: 0o600 });
    const response = await fetch(`http://127.0.0.1:${port}/rdc/pair`, { redirect: "manual" });
    assert.equal(response.status, 303);
    assert.equal(response.headers.get("location"), value.verification_uri_complete);
    assert.equal(response.headers.get("referrer-policy"), "no-referrer");
    assert.equal(response.headers.get("cache-control"), "no-store");
    for (const change of [
      { expires_at: Date.now() / 1000 - 1 }, { expires_at: Date.now() / 1000 + 601 },
      { verification_uri_complete: "https://mcp.desktopcommander.app.evil.example/" },
      { verification_uri_complete: "https://user@mcp.desktopcommander.app/" },
      { verification_uri_complete: "http://mcp.desktopcommander.app/" },
      { verification_uri_complete: "https://mcp.desktopcommander.app/#private" },
      { access_token: "synthetic-private-token" },
    ]) {
      fs.writeFileSync(file, JSON.stringify({ ...value, ...change }));
      assert.equal(pairingHandoff(file), null);
      assert.equal((await fetch(`http://127.0.0.1:${port}/rdc/pair`, { redirect: "manual" })).status, 404);
    }
    fs.rmSync(file);
    const elsewhere = path.join(directory, "other.json");
    fs.writeFileSync(elsewhere, JSON.stringify(value));
    fs.symlinkSync(elsewhere, file);
    assert.equal(pairingHandoff(file), null);
  } finally {
    h.signals.emit("SIGTERM");
    assert.equal(await running, 0);
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
