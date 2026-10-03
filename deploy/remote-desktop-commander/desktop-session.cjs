"use strict";

const { spawn } = require("node:child_process");
const { mkdirSync, chmodSync } = require("node:fs");
const { setTimeout: delay } = require("node:timers/promises");

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
    if (argv[0] === "--browser-smoke-test") {
      // This script only exercises a synthetic page; it never starts RDC pairing.
      const smoke = launch(process.execPath, ["/opt/desktop-commander/browser-smoke.cjs"], { stdio: "inherit" });
      children.push(smoke);
      smoke.once("error", () => stop(1));
      smoke.once("exit", (code) => stop(code === 0 ? 0 : 1));
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
  process.exitCode = await supervise(process.argv.slice(2));
  if (process.exitCode) console.error("RDC/browser session stopped; check container startup and sandbox support.");
}

module.exports = { browserArgs, healthy, waitHealthy, supervise };
if (require.main === module) main().catch(() => {
  console.error("RDC/browser startup failed.");
  process.exitCode = 1;
});
