import assert from "node:assert/strict";
import { once } from "node:events";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { evaluationResult, runLiveSmoke } from "./live-smoke.mjs";

test("smoke evidence parses only the executed JSON result, never echoed source", () => {
  assert.deepEqual(evaluationResult({ content: [{ type: "text", text: '### Result\n{"storage":null}\n### Ran Playwright code\nconst marker="expected";' }] }), { storage: null });
  assert.throws(() => evaluationResult({ content: [{ type: "text", text: '### Ran Playwright code\nconst marker="expected";' }] }), /evaluation_result_missing/);
});

test("smoke rejects unmodified non-hex markers before sending any requests", async () => {
  let calls = 0;
  for (const marker of ['0123456789abcdef0123456789abcde"', "01234567-89ab-cdef-0123-456789abcdef", "ABCDEF0123456789ABCDEF0123456789", 123]) {
    await assert.rejects(runLiveSmoke({ phase: "seed", endpoint: "https://gateway.example.test/browser/v1/mcp", token: "synthetic-token", marker, fetch: async () => { calls += 1; throw new Error("unexpected_request"); } }), /invalid_smoke_marker/);
  }
  assert.equal(calls, 0);
});

test("live SDK seed and verify survive a cold profile restore and reject wrong/replayed markers", { skip: process.env.BROWSER_LIVE_SMOKE_TEST !== "1" }, async (t) => {
  const { chromium } = await import("playwright-core");
  const { createWorker } = await import("./server.mjs");
  const root = await mkdtemp(join(tmpdir(), "live-smoke-"));
  const durable = join(root, "durable");
  await mkdir(durable);
  let current;
  async function start(generation) {
    const local = join(root, `runtime-${generation}`);
    const auth = join(local, "auth");
    const worker = createWorker({
      token: "synthetic-smoke-token", profileDir: join(local, "profile"), stateDir: durable, authDir: auth,
      oauth: { env: {}, publicUrl: "https://gateway.example.test", shortToken: "synthetic-smoke-token", ownerId: "12345", stateFile: join(auth, "oauth.json") },
      chromium: {
        async launchPersistentContext(path, options) {
          const context = await chromium.launchPersistentContext(path, options);
          await context.route("https://example.com/**", (route) => route.fulfill({ contentType: "text/html", body: "<title>Example Domain fixture</title><h1>Example Domain</h1>" }));
          return context;
        },
      },
    });
    await worker.ready;
    const server = createServer(worker.handler).listen(0, "127.0.0.1");
    await once(server, "listening");
    const actual = `http://127.0.0.1:${server.address().port}`;
    // Mimic a TLS Gateway without leaving the machine: preserve HTTP headers and
    // rewrite only this known synthetic origin to the local worker.
    const fetchGateway = (input, init) => {
      const url = new URL(input instanceof Request ? input.url : input);
      assert.equal(url.origin, "https://gateway.example.test");
      return fetch(`${actual}${url.pathname}${url.search}`, init);
    };
    return { worker, server, fetch: fetchGateway, async close() { await worker.close(); await new Promise((resolve) => server.close(resolve)); } };
  }
  t.after(async () => { await current?.close(); await rm(root, { recursive: true, force: true }); });
  const marker = "0123456789abcdef0123456789abcdef";
  const run = (phase, value = marker) => runLiveSmoke({ phase, endpoint: "https://gateway.example.test/browser/v1/mcp", token: "synthetic-smoke-token", marker: value, fetch: current.fetch });
  current = await start(1);
  assert.equal((await run("seed")).status, "passed");
  await current.close();
  current = null;
  // The next worker restores into a different, empty local profile directory.
  current = await start(2);
  await assert.rejects(run("verify", "ffffffffffffffffffffffffffffffff"), /smoke_persistent_profile_marker_mismatch/);
  assert.equal((await run("verify")).status, "passed");
  await assert.rejects(run("verify"), /smoke_persistent_profile_marker_mismatch/);
});
