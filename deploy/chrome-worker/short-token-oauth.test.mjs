import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createOAuth } from "./oauth.mjs";

const PUBLIC = "https://browser.example.test";
const RESOURCE = `${PUBLIC}/browser/v1/mcp`;
const REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect";
const VERIFIER = "v".repeat(43);
const SHORT_TOKEN = "short-token-owner-secret";
const hash = (value) => createHash("sha256").update(value).digest("base64url");

test("short token authorizes ChatGPT OAuth without GitHub", async (t) => {
  const directory = mkdtempSync(join(tmpdir(), "short-token-oauth-"));
  const oauth = createOAuth({ env: {}, publicUrl: PUBLIC, shortToken: SHORT_TOKEN, ownerId: "owner", stateFile: join(directory, "oauth.json") });
  const server = createServer(async (request, response) => {
    if (await oauth.handle(request, response, new URL(request.url, PUBLIC))) return;
    response.writeHead(404).end();
  }).listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(async () => { await new Promise((resolve) => server.close(resolve)); rmSync(directory, { recursive: true, force: true }); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const request = (path, init = {}) => fetch(base + path, { redirect: "manual", ...init });
  const client = await (await request("/browser/oauth/register", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ client_name: "ChatGPT", redirect_uris: [REDIRECT] }) })).json();
  const query = new URLSearchParams({ client_id: client.client_id, redirect_uri: REDIRECT, response_type: "code", resource: RESOURCE, code_challenge: hash(VERIFIER), code_challenge_method: "S256", scope: "browser", state: "client-state" });
  const start = await request(`/browser/oauth/authorize?${query}`);
  assert.equal(start.status, 200);
  const cookie = start.headers.get("set-cookie").split(";")[0];
  const html = await start.text();
  assert.match(html, /name="password"/);
  assert.doesNotMatch(html, /github\.com/i);
  const transaction = html.match(/name="transaction" value="([^"]+)"/)[1];
  const submit = (password) => request("/browser/oauth/authorize", { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded", cookie }, body: new URLSearchParams({ transaction, password }) });
  assert.equal((await submit("wrong-token")).status, 403);
  const granted = await submit(SHORT_TOKEN);
  assert.equal(granted.status, 302);
  const location = new URL(granted.headers.get("location"));
  assert.equal(location.origin, "https://chatgpt.com");
  assert.ok(location.searchParams.get("code"));
  assert.equal(location.searchParams.get("state"), "client-state");
});


test("short-token OAuth redirect does not wait for a stalled remote checkpoint", async (t) => {
  const directory = mkdtempSync(join(tmpdir(), "short-token-oauth-stalled-checkpoint-"));
  let persistCalls = 0;
  const oauth = createOAuth({
    env: {},
    publicUrl: PUBLIC,
    shortToken: SHORT_TOKEN,
    ownerId: "owner",
    stateFile: join(directory, "oauth.json"),
    onPersist: () => {
      persistCalls += 1;
      if (persistCalls >= 3) return new Promise(() => {});
    },
  });
  const server = createServer(async (request, response) => {
    if (await oauth.handle(request, response, new URL(request.url, PUBLIC))) return;
    response.writeHead(404).end();
  }).listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(async () => {
    await new Promise((resolve) => server.close(resolve));
    rmSync(directory, { recursive: true, force: true });
  });
  const base = `http://127.0.0.1:${server.address().port}`;
  const request = (path, init = {}) => fetch(base + path, { redirect: "manual", ...init });
  const client = await (await request("/browser/oauth/register", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ client_name: "ChatGPT", redirect_uris: [REDIRECT] }),
  })).json();
  const query = new URLSearchParams({
    client_id: client.client_id,
    redirect_uri: REDIRECT,
    response_type: "code",
    resource: RESOURCE,
    code_challenge: hash(VERIFIER),
    code_challenge_method: "S256",
    scope: "browser",
    state: "client-state",
  });
  const start = await request(`/browser/oauth/authorize?${query}`);
  const cookie = start.headers.get("set-cookie").split(";")[0];
  const transaction = (await start.text()).match(/name="transaction" value="([^"]+)"/)[1];

  const granted = await Promise.race([
    request("/browser/oauth/authorize", {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded", cookie },
      body: new URLSearchParams({ transaction, password: SHORT_TOKEN }),
    }),
    new Promise((_, reject) => setTimeout(() => reject(new Error("redirect_waited_for_checkpoint")), 250)),
  ]);

  assert.equal(granted.status, 302);
  assert.equal(new URL(granted.headers.get("location")).origin, "https://chatgpt.com");
  assert.equal(persistCalls, 3);
});

async function mobileConsent(t, clientName = "ChatGPT Mobile") {
  const directory = mkdtempSync(join(tmpdir(), "mobile-consent-"));
  const oauth = createOAuth({ env: {}, publicUrl: PUBLIC, shortToken: SHORT_TOKEN, ownerId: "owner", stateFile: join(directory, "oauth.json") });
  const server = createServer(async (request, response) => {
    if (await oauth.handle(request, response, new URL(request.url, PUBLIC))) return;
    response.writeHead(404).end();
  }).listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(async () => { await new Promise((resolve) => server.close(resolve)); rmSync(directory, { recursive: true, force: true }); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const registration = await fetch(base + "/browser/oauth/register", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ client_name: clientName, redirect_uris: [REDIRECT] }),
  });
  assert.equal(registration.status, 201);
  const client = await registration.json();
  const query = new URLSearchParams({ client_id: client.client_id, redirect_uri: REDIRECT, response_type: "code", resource: RESOURCE, code_challenge: hash(VERIFIER), code_challenge_method: "S256", scope: "browser", state: "mobile-state" });
  const response = await fetch(`${base}/browser/oauth/authorize?${query}`, { redirect: "manual" });
  assert.equal(response.status, 200);
  return { response, html: await response.text() };
}

test("mobile consent has a responsive labelled form without disabling zoom", async (t) => {
  const { html, response } = await mobileConsent(t);
  assert.match(html, /<meta name="viewport" content="width=device-width, initial-scale=1">/);
  assert.doesNotMatch(html, /user-scalable\s*=\s*no|maximum-scale\s*=\s*1/i);
  assert.match(html, /<label for="owner-token">/);
  assert.match(html, /id="owner-token"[^>]*name="password"[^>]*autocomplete="current-password"/);
  assert.match(html, /aria-describedby="access-notice"/);
  assert.match(html, /min-height:\s*48px/);
  assert.match(html, /overflow-wrap:\s*anywhere/);
  assert.match(html, /<form method="post" action="\/browser\/oauth\/authorize">/);
  assert.doesNotMatch(html, new RegExp(SHORT_TOKEN));
  assert.match(response.headers.get("set-cookie"), /HttpOnly; SameSite=Lax;/);
});

test("mobile consent styles use a fresh CSP nonce and escape client metadata", async (t) => {
  const { html, response } = await mobileConsent(t, '<script>alert("injected")</script>');
  const nonce = html.match(/<style nonce="([A-Za-z0-9_-]+)">/)?.[1];
  assert.ok(nonce);
  const csp = response.headers.get("content-security-policy");
  assert.ok(csp.includes(`style-src 'nonce-${nonce}'`));
  assert.match(csp, /default-src 'none'/);
  assert.match(csp, /form-action 'self' https:\/\/chatgpt\.com/);
  assert.match(csp, /frame-ancestors 'none'/);
  assert.doesNotMatch(csp, /unsafe-inline|unsafe-eval/);
  assert.doesNotMatch(html, /<script|onerror=|onload=/i);
  assert.match(html, /&lt;script&gt;alert\(&quot;injected&quot;\)&lt;\/script&gt;/);
  const next = await mobileConsent(t);
  assert.notEqual(next.html.match(/<style nonce="([A-Za-z0-9_-]+)">/)?.[1], nonce);
});
