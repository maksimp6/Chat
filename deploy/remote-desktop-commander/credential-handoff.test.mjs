import assert from "node:assert/strict";
import { mkdtemp, readFile, stat } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { once } from "node:events";
import test from "node:test";

import { createCredentialHandoff } from "./credential-handoff.mjs";

async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), "alice-dev-handoff-"));
  const path = join(directory, "credentials.json");
  const handoff = createCredentialHandoff({ token: "bootstrap-token", path });
  const server = http.createServer(async (request, response) => {
    const handled = await handoff.handle(
      request,
      response,
      new URL(request.url, "http://localhost"),
    );
    if (!handled) response.writeHead(404).end();
  }).listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const base = `http://127.0.0.1:${server.address().port}`;
  return { base, path };
}

test("credential handoff is authenticated, one-shot and never echoes secrets", async (t) => {
  const { base, path } = await fixture(t);
  const payload = { login: "test-user", password: "test-password" };

  const unauthorized = await fetch(`${base}/bootstrap/credentials`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  assert.equal(unauthorized.status, 401);

  const first = await fetch(`${base}/bootstrap/credentials`, {
    method: "POST",
    headers: {
      authorization: "Bearer bootstrap-token",
      "content-type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  assert.equal(first.status, 201);
  const firstBody = await first.text();
  assert.equal(firstBody, '{"status":"provisioned"}');
  assert.ok(!firstBody.includes(payload.login));
  assert.ok(!firstBody.includes(payload.password));

  assert.deepEqual(JSON.parse(await readFile(path, "utf8")), payload);
  assert.equal((await stat(path)).mode & 0o777, 0o600);

  const status = await fetch(`${base}/bootstrap/credentials`, {
    headers: { authorization: "Bearer bootstrap-token" },
  });
  assert.deepEqual(await status.json(), { provisioned: true });

  const second = await fetch(`${base}/bootstrap/credentials`, {
    method: "POST",
    headers: {
      authorization: "Bearer bootstrap-token",
      "content-type": "application/json",
    },
    body: JSON.stringify({ login: "replacement", password: "replacement" }),
  });
  assert.equal(second.status, 409);
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")), payload);
});
