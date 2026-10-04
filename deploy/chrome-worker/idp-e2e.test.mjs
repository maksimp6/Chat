import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { createServer } from "node:http";
import test from "node:test";

import { createIdp } from "../oauth-idp/idp.mjs";
import { createWorker } from "./server.mjs";

// Real HTTP servers for the IdP and the worker; only GitHub and DNS are faked.
const ISSUER = "https://oauth.example.test";
const RESOURCE = "https://chrome.example.test/browser/v1/mcp";
const REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect";
const OWNER = "293531601";
const VERIFIER = "v".repeat(64);
const SCOPE = "browser.read browser.control";

async function listen(handler) {
  const server = createServer(handler).listen(0, "127.0.0.1");
  await once(server, "listening");
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

async function setup(t, githubUsers) {
  const githubFetch = async (url, init = {}) => {
    if (String(url) === "https://github.com/login/oauth/access_token") return Response.json({ access_token: `t-${JSON.parse(init.body).code}` });
    if (String(url) === "https://api.github.com/user") return Response.json({ id: Number(githubUsers[init.headers.authorization.replace("Bearer t-", "")]) });
    throw new Error(`unexpected fetch ${url}`);
  };
  const idp = createIdp({ secret: "e2e-secret-with-at-least-32-characters", publicUrl: ISSUER, githubClientId: "gh", githubClientSecret: "ghs", allowedGithubIds: [OWNER], allowedResources: [RESOURCE], scopes: ["browser.read", "browser.control"], fetch: githubFetch, log: () => {} });
  const idpServer = await listen((request, response) => idp.handle(request, response));
  const chromium = { async launchPersistentContext() { return { pages: () => [], on() {}, async close() {} }; } };
  const worker = createWorker({
    token: "static-token",
    profileDir: "/state/profile",
    chromium,
    idp: { issuer: ISSUER, resource: RESOURCE, ownerIds: [OWNER], fetch: (url) => fetch(String(url).replace(ISSUER, idpServer.base)) },
  });
  const workerServer = await listen(worker.handler);
  t.after(async () => { idpServer.server.close(); workerServer.server.close(); await worker.close(); });
  return { idp: idpServer.base, worker: workerServer.base };
}

// Registers like ChatGPT, signs in through (fake) GitHub, and returns the token response.
async function connect(servers, githubCode) {
  const manual = { redirect: "manual" };
  const client = await (await fetch(`${servers.idp}/register`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ client_name: "ChatGPT", redirect_uris: [REDIRECT] }) })).json();
  const query = new URLSearchParams({ client_id: client.client_id, redirect_uri: REDIRECT, response_type: "code", resource: RESOURCE, scope: SCOPE, state: "s", code_challenge: createHash("sha256").update(VERIFIER).digest("base64url"), code_challenge_method: "S256" });
  const consent = await fetch(`${servers.idp}/authorize?${query}`, manual);
  const html = await consent.text();
  const cookie = consent.headers.getSetCookie().map((entry) => entry.split(";")[0]).join("; ");
  const github = await fetch(`${servers.idp}/authorize`, { ...manual, method: "POST", headers: { cookie }, body: new URLSearchParams({ tx: html.match(/name="tx" value="([^"]+)"/)[1] }) });
  const ghState = new URL(github.headers.get("location")).searchParams.get("state");
  const back = await fetch(`${servers.idp}/github/callback?${new URLSearchParams({ state: ghState, code: githubCode })}`, { ...manual, headers: { cookie } });
  const location = back.headers.get("location");
  if (!location) return { client, back, token: null };
  const code = new URL(location).searchParams.get("code");
  const token = await fetch(`${servers.idp}/token`, { method: "POST", body: new URLSearchParams({ grant_type: "authorization_code", code, client_id: client.client_id, redirect_uri: REDIRECT, code_verifier: VERIFIER, resource: RESOURCE }) });
  return { client, back, token };
}

test("owner signs in through the IdP and the worker accepts the issued token", async (t) => {
  const servers = await setup(t, { owner: OWNER });
  const { token } = await connect(servers, "owner");
  assert.equal(token.status, 200);
  const issued = await token.json();
  assert.equal(issued.scope, SCOPE);

  const mcp = (bearer) => fetch(`${servers.worker}/browser/v1/mcp`, { headers: bearer ? { authorization: `Bearer ${bearer}` } : {} });
  const anonymous = await mcp();
  assert.equal(anonymous.status, 401);
  assert.match(anonymous.headers.get("www-authenticate"), /resource_metadata=/);
  assert.notEqual((await mcp(issued.access_token)).status, 401);
  assert.equal((await mcp(`${issued.access_token}x`)).status, 401);

  // The token must not open the REST surface, and the discovery chain must be coherent.
  assert.equal((await fetch(`${servers.worker}/browser/v1/status`, { headers: { authorization: `Bearer ${issued.access_token}` } })).status, 401);
  const resource = await (await fetch(`${servers.worker}/.well-known/oauth-protected-resource/browser/v1/mcp`)).json();
  assert.deepEqual(resource.authorization_servers, [ISSUER]);
  const metadata = await (await fetch(`${servers.idp}/.well-known/oauth-authorization-server`)).json();
  assert.equal(metadata.issuer, ISSUER);
  assert.ok(metadata.code_challenge_methods_supported.includes("S256"));
});

test("a GitHub account that is not the owner never receives a token", async (t) => {
  const servers = await setup(t, { stranger: "999" });
  const { back, token } = await connect(servers, "stranger");
  assert.equal(back.status, 403);
  assert.equal(token, null);
});
