import { randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import { createConnection } from "node:net";

const DEFAULT_TTL_MS = 10 * 60 * 1000;

export function createTakeover(options = {}) {
  const now = options.now ?? Date.now;
  const ttlMs = options.ttlMs ?? DEFAULT_TTL_MS;
  const spawnProcess = options.spawnProcess ?? spawn;
  let session;
  let processes = [];
  let displayStarted = false;

  function stopProcesses() {
    for (const child of processes.splice(0)) {
      try { child.kill("SIGTERM"); } catch {}
    }
  }

  function expire() {
    if (session && session.expiresAt <= now()) {
      session = undefined;
      stopProcesses();
    }
  }

  function status() {
    expire();
    return session ? { active: true, expiresAt: session.expiresAt } : { active: false };
  }

  function startDisplay() {
    if (displayStarted) return;
    displayStarted = true;
    processes = [
      spawnProcess("Xvfb", [":99", "-screen", "0", "1440x900x24", "-nolisten", "tcp"], { stdio: "ignore" }),
      spawnProcess("x11vnc", ["-display", ":99", "-localhost", "-forever", "-shared", "-nopw", "-rfbport", "5900"], { stdio: "ignore" }),
      spawnProcess("websockify", ["--web", "/usr/share/novnc", "127.0.0.1:6080", "127.0.0.1:5900"], { stdio: "ignore" }),
    ];
  }

  function start() {
    expire();
    if (session) throw Object.assign(new Error("takeover_already_active"), { status: 409 });
    startDisplay();
    const token = randomBytes(32).toString("base64url");
    session = { token, expiresAt: now() + ttlMs };
    return { token, expiresAt: session.expiresAt };
  }

  function stop() {
    session = undefined;
  }

  function close() {
    session = undefined;
    displayStarted = false;
    stopProcesses();
  }

  function authorized(url) {
    expire();
    return Boolean(session && url.searchParams.get("takeover_token") === session.token);
  }

  async function proxyHttp(request, response, url) {
    if (!url.pathname.startsWith("/browser/v1/takeover/") || !authorized(url)) return false;
    const upstreamPath = url.pathname.replace("/browser/v1/takeover", "") || "/vnc.html";
    const upstream = await fetch(`http://127.0.0.1:6080${upstreamPath}`);
    response.writeHead(upstream.status, {
      "content-type": upstream.headers.get("content-type") ?? "application/octet-stream",
      "cache-control": "no-store",
    });
    response.end(Buffer.from(await upstream.arrayBuffer()));
    return true;
  }

  function proxyUpgrade(request, socket, head) {
    const url = new URL(request.url, "http://worker.invalid");
    if (!url.pathname.startsWith("/browser/v1/takeover/") || !authorized(url)) {
      socket.write("HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n");
      socket.destroy();
      return;
    }
    const upstream = createConnection({ host: "127.0.0.1", port: 6080 }, () => {
      const path = url.pathname.endsWith("/websockify") ? "/websockify" : url.pathname.replace("/browser/v1/takeover", "");
      const headers = Object.entries(request.headers)
        .filter(([name]) => name.toLowerCase() !== "host")
        .map(([name, value]) => `${name}: ${value}`)
        .join("\r\n");
      upstream.write(`${request.method} ${path} HTTP/1.1\r\nHost: 127.0.0.1:6080\r\n${headers}\r\n\r\n`);
      if (head?.length) upstream.write(head);
      socket.pipe(upstream).pipe(socket);
    });
    upstream.on("error", () => socket.destroy());
  }

  return { startDisplay, start, stop, close, status, authorized, proxyHttp, proxyUpgrade };
}
