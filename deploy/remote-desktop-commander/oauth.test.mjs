import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { once } from "node:events";
import test from "node:test";

import { createOAuth } from "./oauth.mjs";

const PUBLIC = "https://alice-dev.example.test";
const RESOURCE = `${PUBLIC}/mcp`;
const SHORT = "owner-short-token";
const REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect";
const VERIFIER = "a".repeat(43);
const hash = (value) => createHash("sha256").update(value).digest("base64url");

async function fixture(t) {
  const directory = mkdtempSync(join(tmpdir(), "alice-dev-oauth-"));
  const oauth = createOAuth({
    publicUrl: PUBLIC,
    shortToken: SHORT,
    ownerId: "owner",
    stateFile: join(directory, "oauth.json"),
  });
  const server = createServer(async (request, response) => {
    if (await oauth.handle(request, response, new URL(request.url, PUBLIC))) return;
    response.writeHead(oauth.authorize(request) ? 200 : 401, {
      "www-authenticate": oauth.challenge(),
    });
    response.end();
  }).listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(async () => {
    await new Promise((resolve) => server.close(resolve));
    rmSync(directory, { recursive: true, force: true });
  });
  const base = `http://127.0.0.1:${server.address().port}`;
  const request = (path, init = {}) => fetch(base + path, { redirect: "manual", ...init });
  const form = (path, values, cookie) => request(path, {
    method: "POST",
    headers: {
      "content-type": "application/x-www-form-urlencoded",
      ...(cookie ? { cookie } : {}),
    },
    body: new URLSearchParams(values),
  });
  return { oauth, request, form };
}

test("Alice Dev advertises ChatGPT-compatible OAuth discovery", async (t) => {
  const { oauth, request } = await fixture(t);
  assert.match(
    oauth.challenge(),
    /resource_metadata="https:\/\/alice-dev\.example\.test\/\.well-known\/oauth-protected-resource\/mcp"/,
  );
  const resource = await (await request("/.well-known/oauth-protected-resource/mcp")).json();
  assert.equal(resource.resource, RESOURCE);
  assert.deepEqual(resource.authorization_servers, [`${PUBLIC}/oauth`]);
  assert.deepEqual(resource.scopes_supported, ["alice-dev", "offline_access"]);

  const metadata = await (await request("/.well-known/oauth-authorization-server/oauth")).json();
  assert.equal(metadata.issuer, `${PUBLIC}/oauth`);
  assert.equal(metadata.authorization_endpoint, `${PUBLIC}/oauth/authorize`);
  assert.equal(metadata.token_endpoint, `${PUBLIC}/oauth/token`);
  assert.equal(metadata.registration_endpoint, `${PUBLIC}/oauth/register`);
  assert.deepEqual(metadata.code_challenge_methods_supported, ["S256"]);
});

test("DCR, PKCE consent, code exchange and refresh work end to end", async (t) => {
  const { oauth, request, form } = await fixture(t);
  const registration = await request("/oauth/register", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      client_name: "ChatGPT",
      redirect_uris: [REDIRECT],
      token_endpoint_auth_method: "none",
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"],
      scope: "alice-dev offline_access",
    }),
  });
  assert.equal(registration.status, 201);
  const client = await registration.json();

  const query = new URLSearchParams({
    client_id: client.client_id,
    redirect_uri: REDIRECT,
    response_type: "code",
    scope: "alice-dev offline_access",
    resource: RESOURCE,
    code_challenge_method: "S256",
    code_challenge: hash(VERIFIER),
    state: "state-1",
  });
  const consent = await request(`/oauth/authorize?${query}`);
  assert.equal(consent.status, 200);
  const cookie = consent.headers.get("set-cookie")?.split(";")[0];
  const transaction = (await consent.text()).match(/name="transaction" value="([^"]+)"/)?.[1];
  assert.ok(cookie);
  assert.ok(transaction);

  const approved = await form(
    "/oauth/authorize",
    { transaction, password: SHORT },
    cookie,
  );
  assert.equal(approved.status, 302);
  const callback = new URL(approved.headers.get("location"));
  assert.equal(callback.origin + callback.pathname, REDIRECT);
  assert.equal(callback.searchParams.get("iss"), `${PUBLIC}/oauth`);

  const token = await form("/oauth/token", {
    grant_type: "authorization_code",
    client_id: client.client_id,
    redirect_uri: REDIRECT,
    resource: RESOURCE,
    code: callback.searchParams.get("code"),
    code_verifier: VERIFIER,
  });
  assert.equal(token.status, 200);
  const issued = await token.json();
  assert.equal(issued.scope, "alice-dev offline_access");
  assert.ok(issued.access_token);
  assert.ok(issued.refresh_token);
  assert.equal(oauth.authorize({
    headers: { authorization: `Bearer ${issued.access_token}` },
  }), true);

  const refresh = await form("/oauth/token", {
    grant_type: "refresh_token",
    client_id: client.client_id,
    resource: RESOURCE,
    refresh_token: issued.refresh_token,
  });
  assert.equal(refresh.status, 200);
  assert.ok((await refresh.json()).access_token);
});
