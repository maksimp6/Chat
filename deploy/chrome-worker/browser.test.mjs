import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { createWorker } from "./server.mjs";
import { chromium } from "playwright-core";

async function fixture(t) {
  const profileDir = await mkdtemp(join(tmpdir(), "playwright-mcp-test-"));
  const worker = createWorker({ token: "synthetic-test-token", profileDir });
  const http = createServer(worker.handler);
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const endpoint = new URL(`http://127.0.0.1:${http.address().port}/browser/v1/mcp`);
  const client = new Client({ name: "worker-smoke", version: "1.0.0" });
  const transport = new StreamableHTTPClientTransport(endpoint, {
    requestInit: { headers: { Authorization: "Bearer synthetic-test-token" } },
  });
  t.after(async () => {
    await client.close();
    await worker.close();
    await new Promise((resolve) => http.close(resolve));
    await rm(profileDir, { recursive: true, force: true });
  });
  await client.connect(transport);
  return { worker, client, transport, endpoint };
}

test("official Playwright MCP initializes, lists tools, and executes a browser flow", async (t) => {
  const app = await fixture(t);
  assert.ok(app.transport.sessionId);
  const { tools } = await app.client.listTools();
  for (const name of ["browser_navigate", "browser_snapshot", "browser_click", "browser_type", "browser_take_screenshot"]) {
    assert.ok(tools.some((tool) => tool.name === name), name);
  }
  const site = createServer((request, response) => {
    response.setHeader("content-type", "text/html");
    response.end('<title>MCP fixture</title><h1>Playwright MCP works</h1><input aria-label="Name"><button onclick="document.querySelector(\'h1\').textContent=\'Saved\'">Save</button>');
  });
  await new Promise((resolve) => site.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => site.close(resolve)));
  const result = await app.client.callTool({ name: "browser_navigate", arguments: { url: `http://127.0.0.1:${site.address().port}/` } });
  assert.notEqual(result.isError, true, JSON.stringify(result));
  assert.match(JSON.stringify(result), /MCP fixture/);
  const snapshot = await app.client.callTool({ name: "browser_snapshot", arguments: {} });
  const text = snapshot.content.filter((part) => part.type === "text").map((part) => part.text).join("\n");
  const input = text.match(/textbox "Name" \[ref=(\w+)\]/)?.[1];
  const button = text.match(/button "Save" \[ref=(\w+)\]/)?.[1];
  assert.ok(input, text);
  assert.ok(button, text);
  assert.notEqual((await app.client.callTool({ name: "browser_type", arguments: { target: input, text: "Synthetic" } })).isError, true);
  assert.notEqual((await app.client.callTool({ name: "browser_click", arguments: { target: button } })).isError, true);
  assert.match(JSON.stringify(await app.client.callTool({ name: "browser_snapshot", arguments: {} })), /Saved/);
  const screenshot = await app.client.callTool({ name: "browser_take_screenshot", arguments: {} });
  assert.notEqual(screenshot.isError, true, JSON.stringify(screenshot));
  assert.ok(screenshot.content.some((part) => part.type === "image"));
  assert.equal((await fetch(app.endpoint)).status, 401);
  await app.transport.terminateSession();
  const expired = await fetch(app.endpoint, { headers: { Authorization: "Bearer synthetic-test-token", "mcp-session-id": "nonexistent", Accept: "text/event-stream" } });
  assert.equal(expired.status, 404);
});


test("Chrome cookies and localStorage survive a cold worker/profile restore", async (t) => {
  const base = await mkdtemp(join(tmpdir(), "chrome-cold-restore-"));
  const stateDir = join(base, "volume");
  const profileDir = join(base, "profile");
  const authDir = join(base, "auth");
  await mkdir(stateDir);
  let context;
  const options = {
    token: "synthetic-test-token", profileDir, stateDir, authDir,
    chromium: { async launchPersistentContext(directory, launchOptions) {
      context = await chromium.launchPersistentContext(directory, launchOptions);
      return context;
    } },
  };
  let worker = createWorker(options);
  t.after(async () => { await worker.close(); await rm(base, { recursive: true, force: true }); });
  await worker.callTool("wake");
  await context.route("https://fixture.example/**", (route) => route.fulfill({ contentType: "text/html", body: "<title>Persistent fixture</title>" }));
  await worker.callTool("navigate", { url: "https://fixture.example/" });
  await context.addCookies([{ name: "synthetic-cookie", value: "retained", domain: "fixture.example", path: "/", expires: Math.floor(Date.now() / 1000) + 3600 }]);
  await context.pages()[0].evaluate(() => localStorage.setItem("synthetic-marker", "retained"));
  await worker.close();
  await rm(profileDir, { recursive: true, force: true });
  await rm(authDir, { recursive: true, force: true });
  worker = createWorker(options);
  const restored = await worker.callTool("browser_status");
  assert.equal(restored.profile.restored, true);
  await worker.callTool("wake");
  assert.equal((await context.cookies()).find((cookie) => cookie.name === "synthetic-cookie")?.value, "retained");
  await context.route("https://fixture.example/**", (route) => route.fulfill({ contentType: "text/html", body: "<title>Restored fixture</title>" }));
  await worker.callTool("navigate", { url: "https://fixture.example/" });
  assert.equal(await context.pages()[0].evaluate(() => localStorage.getItem("synthetic-marker")), "retained");
});
