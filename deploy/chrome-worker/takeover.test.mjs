import assert from "node:assert/strict";
import { once } from "node:events";
import { createServer, createConnection } from "node:net";
import test from "node:test";
import { createTakeover } from "./takeover.mjs";

function fakeChild() {
  return { killed: false, kill() { this.killed = true; } };
}

const fakeSpawn = () => fakeChild();

function listen(server) {
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server.address().port)));
}

// A noVNC stand-in that accepts websocket upstreams and never answers or closes them.
async function silentUpstream(t) {
  const sockets = new Set();
  const server = createServer((socket) => {
    sockets.add(socket);
    socket.on("error", () => {});
    socket.on("close", () => sockets.delete(socket));
  });
  const port = await listen(server);
  t.after(() => {
    for (const socket of sockets) socket.destroy();
    server.close();
  });
  return port;
}

// Routes one client connection through proxyUpgrade and returns both ends of the proxied socket.
async function proxiedWebsocket(t, takeover, token) {
  const frontend = createServer();
  const port = await listen(frontend);
  const accepted = once(frontend, "connection");
  const client = createConnection({ host: "127.0.0.1", port });
  client.on("error", () => {});
  t.after(() => client.destroy());
  const [serverSocket] = await accepted;
  frontend.close();
  takeover.proxyUpgrade(
    { method: "GET", url: `/browser/v1/takeover/websockify?takeover_token=${token}`, headers: {} },
    serverSocket,
    Buffer.alloc(0),
  );
  return { client, serverSocket };
}

function waitForClose(socket, ms = 2000) {
  return Promise.race([
    once(socket, "close"),
    new Promise((_, reject) => setTimeout(() => reject(new Error("websocket_still_open")), ms).unref()),
  ]);
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

test("takeover websocket is closed when its grant expires, even while the socket stays open", async (t) => {
  const upstreamPort = await silentUpstream(t);
  const takeover = createTakeover({ ttlMs: 80, upstreamPort, spawnProcess: fakeSpawn });
  t.after(() => takeover.close());

  const grant = takeover.start();
  const { client, serverSocket } = await proxiedWebsocket(t, takeover, grant.token);
  assert.equal(takeover.openConnectionCount(), 1);

  // Nothing writes to or closes this socket; only the grant ending may tear it down.
  await waitForClose(client);
  assert.equal(serverSocket.destroyed, true);
  assert.equal(takeover.openConnectionCount(), 0);
  assert.equal(takeover.status().active, false);
});

test("stopping takeover closes its open websocket immediately", async (t) => {
  const upstreamPort = await silentUpstream(t);
  const takeover = createTakeover({ ttlMs: 600_000, upstreamPort, spawnProcess: fakeSpawn });
  t.after(() => takeover.close());

  const grant = takeover.start();
  const { client, serverSocket } = await proxiedWebsocket(t, takeover, grant.token);
  assert.equal(takeover.openConnectionCount(), 1);

  takeover.stop();
  assert.equal(serverSocket.destroyed, true);
  assert.equal(takeover.openConnectionCount(), 0);
  await waitForClose(client);
});
