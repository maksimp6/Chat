"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { browserArgs, healthy, waitHealthy, supervise, superviseCloudRdc, pairingHandoff, processGroupGone, containerWritersGone, helperTimeout, stateHelper, CHECKPOINT_TIMEOUT_MS, controlPermit, receiveGitKey } = require("./desktop-session.cjs");

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
  h.authorizeControl = () => true;
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
      paired: false, device_id: null, checkpoint_generation: 0, quiesced: false,
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
    assert.equal(state.quiesced, true);
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

function permitFixture() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "rdc-control-"));
  const file = path.join(directory, "control-permit.json");
  const projectId = "94ae3a86-671f-40ae-9323-e81d3626135e";
  const nonce = "ab".repeat(32);
  const issued = Math.floor(Date.now() / 1000) - 1;
  const permit = {
    schema: 1, action: "checkpoint", project_id: projectId, container_name: "rdc-94ae3a86671f",
    issued_at: issued, expires_at: issued + 300, sha256: createHash("sha256").update(nonce, "ascii").digest("hex"),
  };
  const canonical = (value) => JSON.stringify(Object.fromEntries(Object.keys(value).sort().map((key) => [key, value[key]]))) + "\n";
  const write = (changes = {}) => fs.writeFileSync(file, canonical({ ...permit, ...changes }), { mode: 0o600 });
  const request = (value = nonce) => ({ rawHeaders: ["X-Alice-Rdc-Control", value], headers: { "x-alice-rdc-control": value } });
  write();
  return { directory, file, projectId, nonce, permit, canonical, write, request,
    authorize: (value) => controlPermit(value, { file, projectId }),
    remove: () => fs.rmSync(directory, { recursive: true, force: true }) };
}

test("control permit requires a unique nonce header and canonical bounded project checkpoint grant", () => {
  const p = permitFixture();
  try {
    assert.equal(p.authorize(p.request()), true);
    assert.equal(p.authorize({ rawHeaders: [], headers: {} }), false);
    const duplicate = p.request();
    duplicate.rawHeaders.push("x-alice-rdc-control", p.nonce);
    assert.equal(p.authorize(duplicate), false);
    for (const nonce of [p.nonce.toUpperCase(), p.nonce + "00", "cd".repeat(32), [p.nonce]]) {
      assert.equal(p.authorize(p.request(nonce)), false);
    }
    for (const changes of [
      { schema: true }, { action: "stop" }, { project_id: "11111111-1111-1111-1111-111111111111" },
      { container_name: "rdc-other" }, { issued_at: Math.floor(Date.now() / 1000) + 1 },
      { expires_at: Math.floor(Date.now() / 1000) }, { expires_at: p.permit.issued_at + 301 },
      { issued_at: 1.5 }, { sha256: "00".repeat(32) }, { extra: "synthetic-private-value" },
    ]) {
      p.write(changes);
      assert.equal(p.authorize(p.request()), false);
    }
    p.write();
    const canonical = fs.readFileSync(p.file, "utf8");
    for (const raw of [canonical.trim(), canonical + "\n", canonical.replace('"action":"checkpoint",', '"action":"checkpoint","action":"checkpoint",'), " ".repeat(4097)]) {
      fs.writeFileSync(p.file, raw);
      assert.equal(p.authorize(p.request()), false);
    }
    assert.equal(controlPermit(p.request(), { file: p.file, projectId: p.projectId.toUpperCase() }), false);
  } finally { p.remove(); }
});

test("control permit rejects symlinks, hardlinks, non-files and missing grants", () => {
  const p = permitFixture();
  try {
    const original = path.join(p.directory, "original.json");
    fs.renameSync(p.file, original);
    fs.symlinkSync(original, p.file);
    assert.equal(p.authorize(p.request()), false);
    fs.rmSync(p.file);
    fs.linkSync(original, p.file);
    assert.equal(p.authorize(p.request()), false);
    fs.rmSync(p.file);
    fs.mkdirSync(p.file);
    assert.equal(p.authorize(p.request()), false);
    fs.rmSync(p.file, { recursive: true });
    assert.equal(p.authorize(p.request()), false);
  } finally { p.remove(); }
});

test("HTTP checkpoint validates each fresh permit before cached completion or shutdown", { timeout: 5000 }, async () => {
  const p = permitFixture();
  const { h, commands, rdcStarted } = cloudHarness();
  h.authorizeControl = p.authorize;
  let port;
  const running = superviseCloudRdc(["remote"], { ...h, port: 0, onListening: (value) => { port = value; } });
  await rdcStarted;
  const endpoint = `http://127.0.0.1:${port}`;
  const post = (nonce) => fetch(endpoint + "/checkpoint", {
    method: "POST", headers: nonce ? { "X-Alice-Rdc-Control": nonce } : {},
  });
  try {
    for (const nonce of [undefined, "cd".repeat(32)]) {
      const response = await post(nonce);
      assert.equal(response.status, 403);
      assert.deepEqual(await response.json(), { status: "control_denied" });
    }
    assert(h.calls.every(({ child }) => child.killedWith.length === 0));
    assert.deepEqual(commands, ["status"]);
    const live = await (await fetch(endpoint + "/healthz")).json();
    assert.equal(live.quiesced, false);
    const completed = await post(p.nonce);
    assert.deepEqual(await completed.json(), { status: "CHECKPOINT_COMPLETE", generation: 1 });
    p.write({ expires_at: Math.floor(Date.now() / 1000) });
    assert.equal((await post(p.nonce)).status, 403);
    const newNonce = "cd".repeat(32);
    p.write({ sha256: createHash("sha256").update(newNonce, "ascii").digest("hex") });
    assert.equal((await post(p.nonce)).status, 403);
    const cached = await post(newNonce);
    assert.equal(cached.status, 200);
    assert.deepEqual(await cached.json(), { status: "CHECKPOINT_COMPLETE", generation: 1 });
    assert.equal(commands.filter((command) => command === "checkpoint").length, 1);
    const health = await fetch(endpoint + "/healthz");
    assert.equal(health.status, 503);
    assert.deepEqual(await health.json(), {
      mode: "cloud-rdc", browser_ready: false, rdc_running: false, quiesced: true,
      state_ready: true, paired: false, device_id: null, checkpoint_generation: 1,
    });
  } finally {
    h.signals.emit("SIGTERM");
    assert.equal(await running, 0);
    p.remove();
  }
});

test("checkpoint helpers get operation budgets capped by the complete phase deadline", async () => {
  assert.equal(CHECKPOINT_TIMEOUT_MS, 240000);
  assert.equal(helperTimeout("checkpoint", 300000, 0), 120000);
  assert.equal(helperTimeout("status", 300000, 0), 60000);
  assert.equal(helperTimeout("checkpoint", 1000, 250), 750);
  assert.throws(() => helperTimeout("checkpoint", 1000, 1000));
  const calls = [];
  await stateHelper("checkpoint", {}, async (...args) => { calls.push(args); return { stdout: "{}" }; });
  await stateHelper("status", { timeoutMs: 750 }, async (...args) => { calls.push(args); return { stdout: "{}" }; });
  assert.equal(calls[0][2].timeout, 120000);
  assert.equal(calls[1][2].timeout, 750);
  assert.equal(calls[0][2].killSignal, "SIGKILL");
});

test("expired full checkpoint phase returns failure without claiming a late helper snapshot", { timeout: 5000 }, async () => {
  const { h, commands, rdcStarted } = cloudHarness();
  const helper = h.stateHelper;
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  h.stateHelper = async (command, options) => {
    if (command === "checkpoint") {
      assert(options.timeoutMs > 0 && options.timeoutMs <= 50);
      await pending;
    }
    return helper(command);
  };
  let port;
  const running = superviseCloudRdc(["remote"], {
    ...h, port: 0, checkpointTimeoutMs: 50, onListening: (value) => { port = value; },
  });
  await rdcStarted;
  const response = await fetch(`http://127.0.0.1:${port}/checkpoint`, { method: "POST" });
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { status: "checkpoint_failed" });
  release();
  assert.equal(await running, 1);
  assert.equal(commands.filter((command) => command === "status").length, 1);
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


test("receiveGitKey writes a private 0600 file without returning secret material", async () => {
  const { Readable } = require("node:stream");
  const { mkdtempSync, readFileSync, statSync, rmSync } = require("node:fs");
  const { tmpdir } = require("node:os");
  const { join } = require("node:path");
  const root = mkdtempSync(join(tmpdir(), "rdc-key-"));
  const target = join(root, "secrets", "id_ed25519");
  const key = "-----BEGIN OPENSSH PRIVATE KEY-----\nsynthetic-canary\n-----END OPENSSH PRIVATE KEY-----\n";
  const request = Readable.from([Buffer.from(key)]);
  request.headers = { "content-length": String(Buffer.byteLength(key)) };
  try {
    const result = await receiveGitKey(request, { target });
    assert.equal(result, undefined);
    assert.equal(readFileSync(target, "utf8"), key);
    assert.equal(statSync(target).mode & 0o777, 0o600);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("receiveGitKey rejects invalid or oversized bodies", async () => {
  const { Readable } = require("node:stream");
  const invalid = Readable.from([Buffer.from("not-a-key")]);
  invalid.headers = { "content-length": "9" };
  await assert.rejects(() => receiveGitKey(invalid, { target: "/tmp/unused-rdc-key" }));

  const oversized = Readable.from([Buffer.from("x".repeat(20))]);
  oversized.headers = { "content-length": "20" };
  await assert.rejects(() => receiveGitKey(oversized, { limit: 10, target: "/tmp/unused-rdc-key" }));
});
