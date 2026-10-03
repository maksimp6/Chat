import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { chromium } from "playwright-core";
import { createWorker } from "./server.mjs";

async function fixture(t) {
  const profileDir = await mkdtemp(join(tmpdir(), "chrome-worker-test-"));
  let context;
  const workers = [];
  function worker() {
    const instance = createWorker({
      token: "synthetic-test-token",
      profileDir,
      allowedHosts: "example.test",
      resolve: async () => [{ address: "93.184.216.34" }],
      chromium: {
        async launchPersistentContext(directory, options) {
          context = await chromium.launchPersistentContext(directory, options);
          return context;
        },
      },
    });
    workers.push(instance);
    return instance;
  }
  t.after(async () => {
    for (const instance of workers) await instance.close();
    await rm(profileDir, { recursive: true, force: true });
  });
  return { worker, context: () => context };
}

test("sandboxed browser restores persistent cookie after worker recreation", async (t) => {
  const app = await fixture(t);
  let worker = app.worker();
  await worker.callTool("wake");
  await app.context().addCookies([{
    name: "synthetic_restart", value: "retained", domain: "example.test", path: "/",
    expires: Math.floor(Date.now() / 1000) + 3600,
  }]);
  await worker.close();
  worker = app.worker();
  await worker.callTool("wake");
  assert.equal((await app.context().cookies()).find((cookie) => cookie.name === "synthetic_restart")?.value, "retained");
});

test("browser rejects redirects and page requests to a private server", async (t) => {
  const app = await fixture(t);
  let hits = 0;
  const server = createServer((request, response) => { hits += 1; response.end("private"); });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const target = `http://127.0.0.1:${server.address().port}/private`;
  const worker = app.worker();
  await worker.callTool("wake");
  await app.context().route("http://example.test/redirect", (route) => route.fulfill({ status: 302, headers: { location: target } }));
  await assert.rejects(worker.callTool("navigate", { url: "http://example.test/redirect" }));
  assert.equal(hits, 0);
  await worker.callTool("sleep");
  await worker.callTool("wake");
  await app.context().route("http://example.test/page", (route) => route.fulfill({
    contentType: "text/html",
    body: `<img src="${target}" onerror="document.title='blocked'">`,
  }));
  await worker.callTool("navigate", { url: "http://example.test/page" });
  await app.context().pages()[0].waitForFunction(() => document.title === "blocked");
  assert.equal(hits, 0);
});
