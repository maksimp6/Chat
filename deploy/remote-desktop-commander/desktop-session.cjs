"use strict";

const { spawn, execFile } = require("node:child_process");
const { mkdirSync, chmodSync, lstatSync, readFileSync, readdirSync, openSync, closeSync, fstatSync, readSync, writeFileSync, constants } = require("node:fs");
const { setTimeout: delay } = require("node:timers/promises");
const { createServer } = require("node:http");
const { promisify } = require("node:util");
const { createHash, timingSafeEqual } = require("node:crypto");
const { join } = require("node:path");

const execute = promisify(execFile);
const HELPER_TIMEOUT_MS = Object.freeze({ status: 60000, "save-auth": 60000, checkpoint: 120000, restore: 120000 });
const CHECKPOINT_TIMEOUT_MS = 240000;
const SECRET_ALIAS = "rdc.git.ssh";
const SECRET_MAX_BYTES = 16 * 1024;

const PROFILE = "/home/node/.config/chromium";
const VERSION_URL = "http://127.0.0.1:9222/json/version";
const browserArgs = [
  "--headless=new",
  "--disable-setuid-sandbox",
  "--remote-debugging-address=127.0.0.1",
  "--remote-debugging-port=9222",
  `--user-data-dir=${PROFILE}`,
  "--no-first-run",
  "--no-default-browser-check",
  "--disable-background-networking",
  "--disable-component-update",
  "--disable-sync",
  "about:blank",
];

async function healthy(fetchImpl = fetch) {
  try {
    const response = await fetchImpl(VERSION_URL, { signal: AbortSignal.timeout(2000) });
    if (!response.ok) return false;
    const version = await response.json();
    return typeof version.Browser === "string" && /^(HeadlessChrome|Chrome)\//.test(version.Browser);
  } catch {
    return false;
  }
}

async function waitHealthy(probe = healthy, timeoutMs = 30000, sleep = delay) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await probe()) return true;
    await sleep(100);
  }
  return false;
}

async function closeBrowser() {
  try {
    const version = await (await fetch(VERSION_URL, { signal: AbortSignal.timeout(1000) })).json();
    const endpoint = new URL(version.webSocketDebuggerUrl);
    if (endpoint.protocol !== "ws:" || endpoint.hostname !== "127.0.0.1" || endpoint.port !== "9222") return;
    await new Promise((resolve) => {
      const socket = new WebSocket(endpoint);
      const timer = setTimeout(() => { socket.close(); resolve(); }, 2000);
      const finish = () => { clearTimeout(timer); resolve(); };
      socket.addEventListener("open", () => socket.send(JSON.stringify({ id: 1, method: "Browser.close" })), { once: true });
      socket.addEventListener("close", finish, { once: true });
      socket.addEventListener("error", finish, { once: true });
    });
  } catch {
    // Process termination below remains the bounded fallback.
  }
}

function helperTimeout(command, deadline, now = Date.now()) {
  const budget = HELPER_TIMEOUT_MS[command];
  if (!budget) throw new Error("State operation unavailable");
  const remaining = deadline === undefined ? budget : deadline - now;
  if (!Number.isFinite(remaining) || remaining <= 0) throw new Error("State operation timed out");
  return Math.min(budget, remaining);
}

async function stateHelper(command, options = {}, executeImpl = execute) {
  const timeout = options.timeoutMs ?? helperTimeout(command);
  if (!Number.isInteger(timeout) || timeout < 1 || timeout > helperTimeout(command)) throw new Error("State operation timed out");
  const { stdout } = await executeImpl("python3", ["/opt/desktop-commander/state-store.py", command], {
    timeout, killSignal: "SIGKILL", maxBuffer: 8192,
  });
  return JSON.parse(stdout);
}

async function beforeDeadline(pending, deadline) {
  const remaining = deadline - Date.now();
  if (remaining <= 0) throw new Error("Checkpoint timed out");
  let timer;
  try {
    return await Promise.race([pending, new Promise((_resolve, reject) => {
      timer = setTimeout(() => reject(new Error("Checkpoint timed out")), remaining);
    })]);
  } finally { clearTimeout(timer); }
}

function controlPermit(request, options = {}) {
  let fd;
  try {
    const headerCount = request.rawHeaders.filter((_value, index) => index % 2 === 0 &&
      request.rawHeaders[index].toLowerCase() === "x-alice-rdc-control").length;
    const nonce = request.headers["x-alice-rdc-control"];
    if (headerCount !== 1 || typeof nonce !== "string" || !/^[0-9a-f]{64}$/.test(nonce)) return false;
    const project = options.projectId ?? process.env.ALICE_RDC_PROJECT_ID;
    if (typeof project !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(project)) return false;
    const file = options.file ?? join(process.env.ALICE_RDC_STATE_PATH || "/rdc-state", "control-permit.json");
    fd = openSync(file, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const before = fstatSync(fd);
    if (!before.isFile() || before.nlink !== 1 || before.size < 1 || before.size > 4096) return false;
    const buffer = Buffer.alloc(4097);
    const count = readSync(fd, buffer, 0, buffer.length, 0);
    const after = fstatSync(fd);
    if (count !== before.size || before.size !== after.size || before.mtimeMs !== after.mtimeMs || before.ctimeMs !== after.ctimeMs) return false;
    const raw = buffer.subarray(0, count);
    const value = JSON.parse(raw.toString("utf8"));
    const action = options.action ?? "checkpoint";
    const keys = action === "checkpoint"
      ? "action,container_name,expires_at,issued_at,project_id,schema,sha256"
      : "action,alias,body_sha256,container_name,expires_at,issued_at,project_id,schema,sha256";
    if (!value || Array.isArray(value) || Object.keys(value).sort().join(",") !== keys) return false;
    const canonical = JSON.stringify(Object.fromEntries(Object.keys(value).sort().map((key) => [key, value[key]]))) + "\n";
    const now = options.now ?? Math.floor(Date.now() / 1000);
    if (!raw.equals(Buffer.from(canonical)) || value.schema !== 1 || value.action !== action ||
        value.project_id !== project || value.container_name !== "rdc-" + project.replaceAll("-", "").slice(0, 12) ||
        !Number.isSafeInteger(value.issued_at) || !Number.isSafeInteger(value.expires_at) ||
        value.issued_at < 0 || value.issued_at > now || value.expires_at <= now ||
        value.expires_at <= value.issued_at || value.expires_at - value.issued_at > 300 ||
        typeof value.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(value.sha256)) return false;
    if (action === "secret_import") {
      if (value.alias !== SECRET_ALIAS || value.alias !== options.alias ||
          typeof value.body_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(value.body_sha256) ||
          (options.bodySha256 !== undefined && value.body_sha256 !== options.bodySha256)) return false;
    } else if (action !== "checkpoint") return false;
    return timingSafeEqual(createHash("sha256").update(nonce, "ascii").digest(), Buffer.from(value.sha256, "hex"));
  } catch { return false; }
  finally { if (fd !== undefined) closeSync(fd); }
}

function secretRequestMetadata(request) {
  const aliasCount = request.rawHeaders.filter((_value, index) => index % 2 === 0 &&
    request.rawHeaders[index].toLowerCase() === "x-alice-secret-alias").length;
  const alias = request.headers["x-alice-secret-alias"];
  const length = request.headers["content-length"];
  if (aliasCount !== 1 || alias !== SECRET_ALIAS ||
      request.headers["content-type"] !== "application/octet-stream" ||
      request.headers["transfer-encoding"] || typeof length !== "string" || !/^[0-9]+$/.test(length)) return null;
  const size = Number(length);
  if (!Number.isSafeInteger(size) || size < 1 || size > SECRET_MAX_BYTES) return null;
  return { alias, size };
}

async function readSecretBody(request, size) {
  const chunks = [];
  let total = 0;
  for await (const chunk of request) {
    total += chunk.length;
    if (total > size || total > SECRET_MAX_BYTES) throw new Error("invalid_secret_body");
    chunks.push(chunk);
  }
  if (total !== size) throw new Error("invalid_secret_body");
  return Buffer.concat(chunks, total);
}

function storeRuntimeSecret(alias, body, directory = "/home/node/.alice-secrets") {
  if (alias !== SECRET_ALIAS) throw new Error("invalid_secret_alias");
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  chmodSync(directory, 0o700);
  const target = join(directory, alias);
  writeFileSync(target, body, { flag: "wx", mode: 0o600 });
  chmodSync(target, 0o600);
  return target;
}

function stateSummary(value) {
  if (!value || value.state_ready !== true || typeof value.paired !== "boolean" ||
      !Number.isSafeInteger(value.checkpoint_generation) || value.checkpoint_generation < 0 ||
      !(value.device_id === null || (typeof value.device_id === "string" &&
        /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.device_id))) ||
      (value.paired && value.device_id === null)) throw new Error("State unavailable");
  return {
    state_ready: true, paired: value.paired, device_id: value.device_id,
    checkpoint_generation: value.checkpoint_generation,
  };
}

function pairingHandoff(file, now = Date.now() / 1000) {
  try {
    const info = lstatSync(file);
    if (!info.isFile() || info.size > 4096) return null;
    const value = JSON.parse(readFileSync(file, "utf8"));
    if (!value || Object.keys(value).sort().join(",") !== "expires_at,verification_uri_complete" ||
        !Number.isFinite(value.expires_at) || value.expires_at <= now || value.expires_at > now + 600 ||
        typeof value.verification_uri_complete !== "string" || value.verification_uri_complete.length > 2048 ||
        /[\s\x00-\x1f\x7f]/.test(value.verification_uri_complete)) return null;
    const url = new URL(value.verification_uri_complete);
    if (url.protocol !== "https:" || !["mcp.desktopcommander.app", "auth.desktopcommander.app"].includes(url.hostname) ||
        url.username || url.password || url.hash || (url.port && url.port !== "443")) return null;
    return url.href;
  } catch { return null; }
}

function authSignature(file) {
  try {
    const info = lstatSync(file);
    if (!info.isFile()) throw new Error("Invalid auth file");
    return `${info.ino}:${info.size}:${info.mtimeMs}:${info.ctimeMs}`;
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

function processGroupGone(pid, inspect = {}) {
  if (!Number.isInteger(pid) || pid < 1) throw new Error("Process ownership unavailable");
  const kill = inspect.kill || process.kill;
  try { kill(-pid, 0); }
  catch (error) {
    if (error.code === "ESRCH") return true;
    throw error;
  }
  const entries = inspect.entries || (() => readdirSync("/proc"));
  const read = inspect.read || ((entry) => readFileSync(`/proc/${entry}/stat`, "utf8"));
  const scan = () => {
    const members = [];
    for (const entry of entries()) {
      if (!/^[1-9][0-9]*$/.test(entry)) continue;
      let raw;
      try { raw = read(entry); }
      catch (error) { if (error.code === "ENOENT") continue; throw error; }
      const boundary = raw.lastIndexOf(") ");
      const fields = raw.slice(boundary + 2).trim().split(/\s+/);
      if (boundary < 0 || fields.length < 20 || !/^[0-9]+$/.test(fields[2])) throw new Error("Process state unavailable");
      if (Number(fields[2]) !== pid) continue;
      if (!["Z", "X"].includes(fields[0])) return { closed: false };
      members.push(`${entry}:${fields[19]}:${fields[0]}`);
    }
    return { closed: members.length > 0, signature: members.sort().join(",") };
  };
  // Dead zombies cannot write and can remain until the container's init reaps
  // them. Require two matching scans; a live or changing group stays blocked.
  const first = scan();
  const second = scan();
  return first.closed && second.closed && first.signature === second.signature;
}

function containerWritersGone(uid, supervisorPid, inspect = {}) {
  if (uid !== 1000 || !Number.isInteger(supervisorPid) || supervisorPid < 1) {
    throw new Error("Process ownership unavailable");
  }
  const entries = inspect.entries || (() => readdirSync("/proc"));
  const read = inspect.read || ((entry, file) => readFileSync(`/proc/${entry}/${file}`, "utf8"));
  const scan = () => {
    const members = [];
    const processes = entries().filter((entry) => /^[1-9][0-9]*$/.test(entry));
    if (processes.length > 4096) throw new Error("Process state unavailable");
    for (const entry of processes) {
      let status;
      let raw;
      try { status = read(entry, "status"); raw = read(entry, "stat"); }
      catch (error) { if (error.code === "ENOENT") continue; throw error; }
      if (status.length > 8192 || raw.length > 4096) throw new Error("Process state unavailable");
      const identities = status.split("\n").filter((line) => line.startsWith("Uid:"));
      const match = identities.length === 1 && /^Uid:\s+([0-9]+)\s+([0-9]+)\s+([0-9]+)\s+([0-9]+)$/.exec(identities[0]);
      if (!match || match.slice(1).some((value) => Number(value) > 4294967295)) throw new Error("Process ownership unavailable");
      const owners = match.slice(1).map(Number);
      if (!owners.includes(uid)) continue;
      if (!owners.every((owner) => owner === uid)) throw new Error("Process ownership unavailable");
      const boundary = raw.lastIndexOf(") ");
      const fields = raw.slice(boundary + 2).trim().split(/\s+/);
      if (boundary < 0 || !raw.startsWith(`${entry} (`) || fields.length < 20 ||
          !/^[A-Za-z]$/.test(fields[0]) || !/^[0-9]+$/.test(fields[19])) throw new Error("Process state unavailable");
      if (Number(entry) !== supervisorPid && !["Z", "X"].includes(fields[0])) return { closed: false };
      members.push(`${entry}:${fields[19]}:${fields[0]}`);
    }
    return { closed: members.some((member) => member.startsWith(`${supervisorPid}:`)), signature: members.sort().join(",") };
  };
  // A terminal can daemonize with setsid and leave both original groups. Only
  // the known supervisor may remain alive as UID 1000 before a fresh snapshot.
  const first = scan();
  const second = scan();
  return first.closed && second.closed && first.signature === second.signature;
}

async function superviseCloudRdc(argv, options = {}) {
  const launch = options.spawn || spawn;
  const probe = options.probe || healthy;
  const signals = options.signals || process;
  const sleep = options.sleep || delay;
  const close = options.closeBrowser || closeBrowser;
  const helper = options.stateHelper || stateHelper;
  const signature = options.authSignature || authSignature;
  const groupGone = options.processGroupGone || processGroupGone;
  const writersGone = options.containerWritersGone || (() => containerWritersGone(process.getuid(), process.pid));
  const authorize = options.authorizeControl || controlPermit;
  const authFile = "/home/node/.desktop-commander-device/device.json";
  const handoffFile = options.pairingFile || process.env.ALICE_RDC_PAIRING_FILE;
  const children = [];
  const closedGroups = new Set();
  let summary;
  let browserReady = false;
  let rdcRunning = false;
  let stopping = false;
  let quiescing = false;
  let exitCode = 1;
  let server;
  let poll;
  let checkpointPromise;
  let checkpointComplete = false;
  let helperQueue = Promise.resolve();
  let finish;
  const ended = new Promise((resolve) => { finish = resolve; });
  const stop = (code) => {
    if (stopping) return;
    stopping = true;
    exitCode = code;
    finish();
  };
  const onSignal = () => stop(0);
  const runHelper = (command, deadline) => {
    const pending = helperQueue.then(() => helper(command, { timeoutMs: helperTimeout(command, deadline) }));
    helperQueue = pending.catch(() => {});
    return deadline === undefined ? pending : beforeDeadline(pending, deadline);
  };
  const persistAuth = () => {
    if (stopping || quiescing || !summary) return;
    summary.state_ready = false;
    // Commands share one queue: token rotation cannot race a closed snapshot.
    void runHelper("save-auth").then(() => runHelper("status")).then((value) => {
      summary = stateSummary(value);
    }).catch(() => stop(1));
  };
  const start = (command, args, stdio) => {
    const child = launch(command, args, { stdio, detached: true });
    children.push(child);
    child.once("error", () => stop(1));
    child.once("exit", () => { if (!quiescing) stop(1); });
    return child;
  };
  const waitExit = async (child, deadline) => {
    if (child.exitCode === null && child.signalCode === null) {
      let timer;
      await new Promise((resolve, reject) => {
        const completed = () => { clearTimeout(timer); resolve(); };
        timer = setTimeout(() => {
          child.off("exit", completed);
          reject(new Error("Shutdown incomplete"));
        }, Math.min(options.shutdownTimeoutMs ?? 5000, deadline - Date.now()));
        child.once("exit", completed);
      });
    }
    if (child.exitCode !== 0 || child.signalCode !== null) throw new Error("Shutdown incomplete");
  };
  const checkpoint = () => {
    if (checkpointPromise) return checkpointPromise;
    checkpointPromise = (async () => {
      const phaseDeadline = Date.now() + (options.checkpointTimeoutMs ?? CHECKPOINT_TIMEOUT_MS);
      quiescing = true;
      browserReady = false;
      rdcRunning = false;
      clearInterval(poll);
      const [browser, rdc] = children;
      if (!browser || !rdc) throw new Error("Session unavailable");
      rdc.kill("SIGTERM");
      await beforeDeadline(waitExit(rdc, phaseDeadline), phaseDeadline);
      await beforeDeadline(close(), phaseDeadline);
      await beforeDeadline(waitExit(browser, phaseDeadline), phaseDeadline);
      // RDC suppresses transport-close errors, so its exit code alone does not
      // prove that an executor grandchild has stopped modifying the workspace.
      const deadline = Math.min(phaseDeadline, Date.now() + (options.shutdownTimeoutMs ?? 5000));
      for (const child of children) {
        while (!groupGone(child.pid)) {
          if (Date.now() >= deadline) throw new Error("Shutdown incomplete");
          await beforeDeadline(sleep(25), phaseDeadline);
        }
        closedGroups.add(child.pid);
      }
      // Drain helpers already scheduled by auth notifications before checking
      // all container UID writers, including executors that escaped with setsid.
      let drained;
      do { drained = helperQueue; await beforeDeadline(drained, phaseDeadline); } while (drained !== helperQueue);
      if ((stopping && exitCode !== 0) || !writersGone()) throw new Error("Shutdown incomplete");
      const result = await runHelper("checkpoint", phaseDeadline);
      if (!result || result.status !== "CHECKPOINT_COMPLETE" ||
          !Number.isSafeInteger(result.generation) || result.generation < 1) throw new Error("Checkpoint unavailable");
      summary = stateSummary(await runHelper("status", phaseDeadline));
      if (summary.checkpoint_generation !== result.generation) throw new Error("Checkpoint unavailable");
      checkpointComplete = true;
      return { status: "CHECKPOINT_COMPLETE", generation: result.generation };
    })();
    return checkpointPromise;
  };
  signals.on("SIGTERM", onSignal);
  signals.on("SIGINT", onSignal);
  try {
    session: {
    summary = stateSummary(await runHelper("status"));
    const port = options.port ?? Number(process.env.PORT || 8080);
    if (!Number.isInteger(port) || port < 0 || port > 65535 || (port === 0 && options.port !== 0)) break session;
    server = createServer(async (request, response) => {
      response.setHeader("Cache-Control", "no-store");
      response.setHeader("Referrer-Policy", "no-referrer");
      response.setHeader("Content-Type", "application/json");
      if (request.method === "GET" && request.url === "/healthz") {
        const ok = browserReady && rdcRunning && summary.state_ready && !stopping && !quiescing;
        response.writeHead(ok ? 200 : 503).end(JSON.stringify({
          mode: "cloud-rdc", browser_ready: browserReady && !stopping && !quiescing,
          rdc_running: rdcRunning && !stopping && !quiescing, quiesced: checkpointComplete, ...summary,
        }));
      } else if (request.method === "GET" && request.url === "/rdc/pair") {
        const href = !stopping && !quiescing && !summary.paired && pairingHandoff(handoffFile);
        if (href) response.writeHead(303, { Location: href }).end();
        else response.writeHead(404).end('{"status":"pairing_unavailable"}');
      } else if (request.method === "POST" && request.url === "/rdc/secrets") {
        const metadata = secretRequestMetadata(request);
        const nonce = request.headers["x-alice-rdc-control"];
        if (!metadata || typeof nonce !== "string" ||
            !authorize(request, { action: "secret_import", alias: metadata?.alias })) {
          request.resume();
          response.writeHead(metadata ? 403 : 400).end(metadata ? '{"status":"control_denied"}' : '{"status":"invalid_request"}');
          return;
        }
        let body;
        try { body = await readSecretBody(request, metadata.size); }
        catch {
          response.writeHead(400).end('{"status":"invalid_request"}');
          return;
        }
        const bodySha256 = createHash("sha256").update(body).digest("hex");
        if (!authorize(request, { action: "secret_import", alias: metadata.alias, bodySha256 })) {
          response.writeHead(403).end('{"status":"control_denied"}');
          return;
        }
        try {
          storeRuntimeSecret(metadata.alias, body, options.secretDir);
          response.writeHead(201).end(JSON.stringify({ status: "secret_imported", alias: metadata.alias }));
        } catch (error) {
          response.writeHead(error?.code === "EEXIST" ? 409 : 503)
            .end(error?.code === "EEXIST" ? '{"status":"secret_exists"}' : '{"status":"secret_import_failed"}');
        }
      } else if (request.method === "POST" && request.url === "/checkpoint") {
        if (!authorize(request)) {
          request.resume();
          response.writeHead(403).end('{"status":"control_denied"}');
          return;
        }
        if (request.headers["transfer-encoding"] || Number(request.headers["content-length"] || 0) !== 0) {
          request.resume();
          response.writeHead(400).end('{"status":"invalid_request"}');
          return;
        }
        try {
          const result = await checkpoint();
          response.writeHead(200).end(JSON.stringify(result));
        } catch {
          response.writeHead(503).end('{"status":"checkpoint_failed"}');
          setImmediate(() => stop(1));
        }
      } else response.writeHead(404).end('{"status":"not_found"}');
    });
    await new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen(port, "0.0.0.0", resolve);
    });
    server.on("error", () => stop(1));
    if (options.onListening) options.onListening(server.address().port);
    if (await probe()) break session;
    start("chromium", browserArgs, "ignore");
    const deadline = Date.now() + (options.startupTimeoutMs ?? 30000);
    while (!stopping && Date.now() < deadline) {
      if (await probe()) { browserReady = !stopping; break; }
      await Promise.race([sleep(100), ended]);
    }
    if (!browserReady || stopping || quiescing) break session;
    let lastSignature = signature(authFile);
    const rdc = start(process.execPath, [
      "--require", "/opt/desktop-commander/pairing-handoff.cjs",
      "/opt/desktop-commander/node_modules/@wonderwhy-er/desktop-commander/dist/index.js", ...argv,
    ], ["ignore", "ignore", "ignore", "ipc"]);
    rdcRunning = !stopping;
    rdc.on("message", (message) => {
      if (!message || Object.keys(message).length !== 1) return;
      if (message.type === "rdc-auth-write-failed") stop(1);
      if (message.type === "rdc-auth-committed") {
        try { lastSignature = signature(authFile); persistAuth(); } catch { stop(1); }
      }
    });
    poll = setInterval(() => {
      if (stopping || quiescing) return;
      try {
        const current = signature(authFile);
        if (current !== lastSignature) { lastSignature = current; persistAuth(); }
      } catch { stop(1); }
    }, options.authPollMs ?? 500);
    await ended;
    }
  } catch { exitCode = 1; }
  finally {
    clearInterval(poll);
    // SIGTERM is best effort. A forced/failed child exit never becomes a new
    // durable browser snapshot; the previous complete generation stays intact.
    if (exitCode === 0 && children.length === 2) {
      try { await checkpoint(); } catch { exitCode = 1; }
    }
    quiescing = true;
    for (const child of children) {
      if (Number.isInteger(child.pid) && !closedGroups.has(child.pid)) {
        try { process.kill(-child.pid, "SIGKILL"); } catch { /* Already closed. */ }
      }
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
    }
    await helperQueue;
    if (server) {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    }
    signals.off("SIGTERM", onSignal);
    signals.off("SIGINT", onSignal);
  }
  return exitCode;
}

async function supervise(argv, options = {}) {
  const launch = options.spawn || spawn;
  const probe = options.probe || healthy;
  const signals = options.signals || process;
  const sleep = options.sleep || delay;
  const close = options.closeBrowser || closeBrowser;
  const children = [];
  let ready = false;
  let stopping = false;
  let exitCode = 1;
  let smokePassed = false;
  let server;
  const cloudProbe = argv.length === 1 && argv[0] === "--cloud-probe";
  let finish;
  const ended = new Promise((resolve) => { finish = resolve; });
  const stop = (code) => {
    if (stopping) return;
    stopping = true;
    exitCode = code;
    finish();
  };
  const onSignal = () => stop(0);
  signals.on("SIGTERM", onSignal);
  signals.on("SIGINT", onSignal);
  const start = (command, args, stdio) => {
    const child = launch(command, args, { stdio });
    children.push(child);
    child.once("error", () => stop(1));
    child.once("exit", () => stop(1));
    return child;
  };
  try {
    if (cloudProbe) {
      const port = options.port ?? Number(process.env.PORT || 8080);
      if (!Number.isInteger(port) || port < 0 || port > 65535 || (port === 0 && options.port !== 0)) return 1;
      server = createServer((request, response) => {
        response.setHeader("Cache-Control", "no-store");
        response.setHeader("Content-Type", "application/json");
        if (request.method !== "GET" || request.url !== "/healthz") {
          response.writeHead(404).end('{"status":"not_found"}');
          return;
        }
        const ok = ready && smokePassed && !stopping;
        response.writeHead(ok ? 200 : 503).end(JSON.stringify({
          mode: "cloud-probe", browser_ready: ok, smoke_passed: ok,
        }));
      });
      await new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen(port, "0.0.0.0", resolve);
      });
      server.on("error", () => stop(1));
      if (options.onListening) options.onListening(server.address().port);
    }
    // Never reuse or close a browser started by another process in this container.
    if (await probe()) return 1;
    start("chromium", browserArgs, "ignore");
    // Whole startup is bounded, including failed/slow health requests.
    const deadline = Date.now() + (options.startupTimeoutMs ?? 30000);
    while (!stopping && Date.now() < deadline) {
      if (await probe()) { ready = !stopping; break; }
      await Promise.race([sleep(100), ended]);
    }
    if (!ready || stopping) return exitCode;
    if (argv[0] === "--browser-smoke-test" || cloudProbe) {
      // This script only exercises a synthetic page; it never starts RDC pairing.
      const smoke = launch(process.execPath, ["/opt/desktop-commander/browser-smoke.cjs"], { stdio: "inherit" });
      children.push(smoke);
      smoke.once("error", () => stop(1));
      smoke.once("exit", (code) => {
        if (cloudProbe && code === 0) smokePassed = true;
        else stop(code === 0 ? 0 : 1);
      });
    } else {
      start(process.execPath, [
        "--require", "/opt/desktop-commander/pairing-handoff.cjs",
        "/opt/desktop-commander/node_modules/@wonderwhy-er/desktop-commander/dist/index.js",
        ...argv,
      ], "inherit");
    }
    await ended;
    return exitCode;
  } catch {
    return 1;
  } finally {
    if (server) {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    }
    // Browser.close flushes the profile and removes SingletonLock, unlike a
    // signal-only exit that can strand a lock naming the old container host.
    if (ready) await close();
    for (const child of children) child.kill("SIGTERM");
    // Wait for a clean profile flush, then bound shutdown even if a child hangs.
    await Promise.race([
      Promise.all(children.map((child) => child.exitCode !== null || child.signalCode !== null
        ? Promise.resolve() : new Promise((resolve) => child.once("exit", resolve)))),
      sleep(5000),
    ]);
    for (const child of children) {
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
    }
    signals.off("SIGTERM", onSignal);
    signals.off("SIGINT", onSignal);
  }
}

async function main() {
  if (process.argv[2] === "--wait-ready") {
    process.exitCode = await waitHealthy() ? 0 : 1;
    return;
  }
  if (process.argv[2] === "--healthcheck") {
    process.exitCode = await healthy() ? 0 : 1;
    return;
  }
  process.umask(0o077);
  mkdirSync(PROFILE, { recursive: true, mode: 0o700 });
  chmodSync(PROFILE, 0o700);
  const mode = process.env.ALICE_RDC_MODE;
  if (mode && !["cloud-probe", "cloud-rdc"].includes(mode)) throw new Error("Invalid mode");
  process.exitCode = mode === "cloud-rdc" ? await superviseCloudRdc(process.argv.slice(2)) :
    await supervise(mode === "cloud-probe" ? ["--cloud-probe"] : process.argv.slice(2));
  if (process.exitCode) console.error("RDC/browser session stopped; check container startup and sandbox support.");
}

module.exports = { browserArgs, healthy, waitHealthy, supervise, superviseCloudRdc, pairingHandoff, stateSummary, processGroupGone, containerWritersGone, helperTimeout, stateHelper, CHECKPOINT_TIMEOUT_MS, controlPermit };
if (require.main === module) main().catch(() => {
  console.error("RDC/browser startup failed.");
  process.exitCode = 1;
});
