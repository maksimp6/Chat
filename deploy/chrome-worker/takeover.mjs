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

  function authorized(url, request) {
    expire();
    if (!session) return false;
    if (url.searchParams.get("takeover_token") === session.token) return true;
    const cookie = String(request?.headers?.cookie ?? "");
    return cookie.split(";").some((part) => part.trim() === `browser_takeover=${session.token}`);
  }

  function noVncPath(pathname) {
    const prefix = "/browser/v1/takeover/";
    if (!pathname.startsWith(prefix)) return undefined;
    const relative = pathname.slice(prefix.length);
    if (!relative || relative === "vnc.html") return "/vnc.html";
    if (!/^[A-Za-z0-9._/-]+$/.test(relative) || relative.includes("..") || relative.includes("\\")) return undefined;
    return "/" + relative;
  }

  async function proxyHttp(request, response, url) {
    if (!authorized(url, request)) return false;
    const supplied = url.searchParams.get("takeover_token");
    const upstreamPath = noVncPath(url.pathname);
    if (!upstreamPath) return false;
    const upstream = await fetch(new URL(upstreamPath, "http://127.0.0.1:6080"));
    const headers = {
      "content-type": upstream.headers.get("content-type") ?? "application/octet-stream",
      "cache-control": "no-store",
    };
    if (supplied && session && supplied === session.token) {
      headers["set-cookie"] = `browser_takeover=${session.token}; Path=/browser/v1/takeover/; HttpOnly; Secure; SameSite=Strict; Max-Age=${Math.max(1, Math.floor((session.expiresAt - now()) / 1000))}`;
    }
    response.writeHead(upstream.status, headers);
    response.end(Buffer.from(await upstream.arrayBuffer()));
    return true;
  }

  function proxyUpgrade(request, socket, head) {
    const url = new URL(request.url, "http://worker.invalid");
    if (!url.pathname.startsWith("/browser/v1/takeover/") || !authorized(url, request)) {
      socket.write("HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n");
      socket.destroy();
      return;
    }
    const upstream = createConnection({ host: "127.0.0.1", port: 6080 }, () => {
      const path = url.pathname.endsWith("/websockify") ? "/websockify" : undefined;
      if (!path) {
        socket.destroy();
        upstream.destroy();
        return;
      }
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
