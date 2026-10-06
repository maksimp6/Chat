import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { createWorker } from "./server.mjs";

let resolverCalls = 0;
let resolverFailures = 0;

function resolveThroughPython(alias, purpose) {
  resolverCalls += 1;
  return new Promise((resolve, reject) => {
    const repoRoot = fileURLToPath(new URL("../../", import.meta.url));
    const child = spawn("python3", ["tests/secret_manager_browser_bridge.py"], {
      cwd: repoRoot,
      env: { ...process.env, PYTHONPATH: repoRoot },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let output = "";
    let error = "";
    child.stdout.on("data", (chunk) => { output += chunk; });
    child.stderr.on("data", (chunk) => { error += chunk; });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        resolverFailures += 1;
        return reject(new Error(`resolver_process_failed_exit_${code}`));
      }
      resolve(output);
    });
    child.stdin.end(JSON.stringify({ alias, purpose }) + "\n");
  });
}

function fakeChromium() {
  const profile = { value: "" };
  const page = {
    url: () => "https://example.test/login",
    title: async () => "Login",
    locator() {
      return { fill: async (value) => { profile.value = value; } };
    },
  };
  return {
    profile,
    chromium: {
      async launchPersistentContext() {
        return { pages: () => [page], async close() {} };
      },
    },
  };
}

test("password manager resolves rotated canary into browser without response disclosure", async (t) => {
  const resolved = await resolveThroughPython("github", "browser.password");
  assert.equal(resolved === "canary-cross-runtime-v2", true);

  const fake = fakeChromium();
  const worker = createWorker({
    token: "test-token",
    profileDir: "/tmp/alice-secret-e2e",
    chromium: fake.chromium,
    secretResolver: resolveThroughPython,
  });
  const server = createServer(worker.handler).listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(async () => {
    await worker.close();
    await new Promise((resolve) => server.close(resolve));
  });
  const base = `http://127.0.0.1:${server.address().port}`;
  await fetch(base + "/browser/v1/wake", {
    method: "POST",
    headers: { authorization: "Bearer test-token", "content-type": "application/json" },
    body: "{}",
  });
  const response = await fetch(base + "/browser/v1/secret/fill", {
    method: "POST",
    headers: { authorization: "Bearer test-token", "content-type": "application/json" },
    body: JSON.stringify({
      alias: "github",
      locator: "#password",
      purpose: "browser.password",
    }),
  });
  const body = await response.text();

  assert.equal(response.status, 200, `resolverCalls=${resolverCalls} resolverFailures=${resolverFailures}`);
  assert.deepEqual(JSON.parse(body), { ok: true });
  assert.equal(fake.profile.value, "canary-cross-runtime-v2");
  assert.equal(body.includes("canary-cross-runtime-v1"), false);
  assert.equal(body.includes("canary-cross-runtime-v2"), false);
});
