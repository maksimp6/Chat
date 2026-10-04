import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import test from "node:test";
import { createKeys } from "./crypto.mjs";
import { createIdp } from "./idp.mjs";
import { configFromEnv } from "./server.mjs";
import { publicKeyFromJwk, verifyJwt } from "./jwt-verify.mjs";

const SECRET = "test-secret-with-at-least-32-characters!!";
const ISSUER = "https://oauth.example.test";
const RESOURCE = "https://chrome.example.test/browser/v1/mcp";
const REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect";
const OWNER = "293531601";
const VERIFIER = "v".repeat(64);
const START = 1_800_000_000_000;
const challenge = (value) => createHash("sha256").update(value).digest("base64url");

async function fixture(t, overrides = {}) {
  const clock = { ms: START };
  const github = { exchanges: [], users: { "code-owner": OWNER, "code-stranger": "999" } };
  const fakeFetch = async (url, init = {}) => {
    if (String(url) === "https://github.com/login/oauth/access_token") {
      const body = JSON.parse(init.body);
      github.exchanges.push(body);
      return github.users[body.code] ? Response.json({ access_token: `token-${body.code}` }) : Response.json({ error: "bad_verification_code" });
    }
    if (String(url) === "https://api.github.com/user") {
      const code = init.headers.authorization.replace("Bearer token-", "");
      return Response.json({ id: Number(github.users[code]) });
    }
    throw new Error(`unexpected fetch ${url}`);
  };
  const options = {
    secret: SECRET,
    publicUrl: ISSUER,
    githubClientId: "gh-client",
    githubClientSecret: "gh-secret",
    allowedGithubIds: [OWNER],
    allowedResources: [RESOURCE],
    scopes: ["browser"],
    fetch: fakeFetch,
    now: () => clock.ms,
    log: () => {},
    ...overrides,
  };
  const idp = createIdp(options);
  const server = createServer((request, response) => idp.handle(request, response));
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.close());
  const base = `http://127.0.0.1:${server.address().port}`;
  const request = (path, init = {}) => fetch(base + path, { redirect: "manual", ...init });
  const form = (path, values, headers = {}) =>
    request(path, { method: "POST", headers, body: new URLSearchParams(values) });
  const register = (values = {}) =>
    request("/register", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ client_name: "ChatGPT", redirect_uris: [REDIRECT], ...values }),
    });
  const registered = async (values) => (await register(values)).json();
  // A value of undefined removes the parameter instead of sending "undefined".
  const authorizeQuery = (client, values = {}) =>
    new URLSearchParams(
      Object.entries({
        client_id: client.client_id,
        redirect_uri: REDIRECT,
        response_type: "code",
        resource: RESOURCE,
        scope: "browser",
        code_challenge: challenge(VERIFIER),
        code_challenge_method: "S256",
        state: "client-state",
        ...values,
      }).filter(([, value]) => value !== undefined),
    );
  const begin = async (client, values) => {
    const response = await request(`/authorize?${authorizeQuery(client, values)}`);
    const html = await response.text();
    const cookie = response.headers.getSetCookie().map((entry) => entry.split(";")[0]).find((entry) => entry.includes("idp_tx="));
    return { response, html, cookie, tx: html.match(/name="tx" value="([^"]+)"/)?.[1] };
  };
  const toGithub = async (flow) => {
    const response = await form("/authorize", { tx: flow.tx }, { cookie: flow.cookie ?? "" });
    return { response, location: response.headers.get("location") };
  };
  const callback = (state, code, cookie, extra = {}) =>
    request(`/github/callback?${new URLSearchParams({ state, ...(code ? { code } : {}), ...extra })}`, {
      headers: { cookie: cookie ?? "" },
    });
  const exchange = (values) => form("/token", values);
  // Drives the complete sign-in and returns everything a test might inspect.
  const signIn = async ({ githubCode = "code-owner", clientValues, authorize } = {}) => {
    const client = await registered(clientValues);
    const flow = await begin(client, authorize);
    const github_ = await toGithub(flow);
    const state = new URL(github_.location).searchParams.get("state");
    const returned = await callback(state, githubCode, flow.cookie);
    const target = returned.headers.get("location") ? new URL(returned.headers.get("location")) : null;
    return { client, flow, github: github_, state, returned, target, code: target?.searchParams.get("code") };
  };
  const tokens = (values) => ({
    grant_type: "authorization_code",
    redirect_uri: REDIRECT,
    code_verifier: VERIFIER,
    ...values,
  });
  return { idp, options, clock, github, request, form, register, registered, begin, toGithub, callback, exchange, signIn, tokens, authorizeQuery };
}

const verifyAccess = (idp, token, overrides = {}) => {
  const { kid, key } = publicKeyFromJwk(idp.jwk);
  return verifyJwt(token, { keys: new Map([[kid, key]]), issuer: ISSUER, audience: RESOURCE, now: Math.floor(START / 1000), ...overrides });
};

test("an unconfigured server fails closed and names only the missing settings", async (t) => {
  const app = await fixture(t, { secret: "short-secret", githubClientSecret: "", allowedGithubIds: [], allowedResources: [] });
  assert.equal(app.idp.ready, false);
  const health = await app.request("/healthz");
  assert.equal(health.status, 503);
  const body = await health.json();
  assert.deepEqual(body.missing, ["IDP_SECRET", "IDP_GITHUB_CLIENT_SECRET", "IDP_ALLOWED_GITHUB_IDS", "IDP_ALLOWED_RESOURCES"]);
  assert.ok(!JSON.stringify(body).includes("short-secret"));
  assert.equal((await app.register()).status, 503);
  assert.equal((await app.request("/jwks.json")).status, 503);
});

test("the public URL must be a bare https origin", () => {
  for (const publicUrl of ["http://oauth.example.test", "https://oauth.example.test/path", "https://u:p@oauth.example.test", "https://oauth.example.test/?x=1", "not a url"]) {
    assert.throws(() => createIdp({ secret: SECRET, publicUrl, githubClientId: "a", githubClientSecret: "b", allowedGithubIds: ["1"], allowedResources: [RESOURCE] }), /invalid_idp_public_url/);
  }
  assert.ok(createIdp({ secret: SECRET, publicUrl: "http://127.0.0.1:9", githubClientId: "a", githubClientSecret: "b", allowedGithubIds: ["1"], allowedResources: [RESOURCE] }).ready);
});

test("configuration is read from the environment with safe defaults", () => {
  const config = configFromEnv({
    IDP_SECRET: SECRET,
    IDP_PUBLIC_URL: ISSUER,
    IDP_GITHUB_CLIENT_ID: "id",
    IDP_GITHUB_CLIENT_SECRET: "secret",
    IDP_ALLOWED_GITHUB_IDS: "1, 2 ,",
    IDP_ALLOWED_RESOURCES: `${RESOURCE},https://other.example.test/mcp`,
    IDP_SCOPES: "browser",
    IDP_NOT_BEFORE: "123",
  });
  assert.deepEqual(config.allowedGithubIds, ["1", "2"]);
  assert.deepEqual(config.allowedResources, [RESOURCE, "https://other.example.test/mcp"]);
  assert.equal(config.allowedRedirectHosts, undefined);
  assert.equal(config.notBefore, 123);
  assert.deepEqual(configFromEnv({}).allowedGithubIds, []);
});

test("metadata and JWKS describe the server and never expose private key material", async (t) => {
  const app = await fixture(t);
  assert.equal((await app.request("/healthz")).status, 200);
  const metadata = await (await app.request("/.well-known/oauth-authorization-server")).json();
  assert.equal(metadata.issuer, ISSUER);
  assert.equal(metadata.authorization_endpoint, `${ISSUER}/authorize`);
  assert.equal(metadata.token_endpoint, `${ISSUER}/token`);
  assert.equal(metadata.registration_endpoint, `${ISSUER}/register`);
  assert.equal(metadata.jwks_uri, `${ISSUER}/jwks.json`);
  assert.deepEqual(metadata.code_challenge_methods_supported, ["S256"]);
  assert.deepEqual(metadata.token_endpoint_auth_methods_supported, ["none"]);
  assert.deepEqual(metadata.scopes_supported, ["browser"]);
  const jwks = await (await app.request("/jwks.json")).json();
  assert.equal(jwks.keys.length, 1);
  assert.equal(jwks.keys[0].kid, createKeys(SECRET).kid);
  assert.equal(jwks.keys[0].d, undefined);
  assert.equal((await app.request("/nope")).status, 404);
  assert.equal((await app.request("/token")).status, 404, "token endpoint is POST only");
});

test("dynamic registration issues opaque, stateless clients", async (t) => {
  const app = await fixture(t);
  const response = await app.register({ client_name: "ChatGPT" });
  assert.equal(response.status, 201);
  const client = await response.json();
  assert.equal(client.token_endpoint_auth_method, "none");
  assert.deepEqual(client.redirect_uris, [REDIRECT]);
  assert.equal(client.client_secret, undefined);
  assert.ok(!client.client_id.includes("chatgpt"), "client_id does not leak the registration in the clear");
  // Nothing is stored: a second instance with the same secret accepts the client, another secret does not.
  const sibling = await fixture(t);
  assert.equal((await sibling.begin(client)).response.status, 200);
  const stranger = await fixture(t, { secret: `${SECRET}-other` });
  assert.equal((await stranger.begin(client)).response.status, 400);
});

test("registration only accepts redirect addresses from the allowlist", async (t) => {
  const app = await fixture(t);
  const invalid = async (values, error = "invalid_redirect_uri") => {
    const response = await app.register(values);
    assert.equal(response.status, 400, JSON.stringify(values));
    assert.equal((await response.json()).error, error);
  };
  await invalid({ redirect_uris: ["https://evil.example/callback"] });
  await invalid({ redirect_uris: ["http://chatgpt.com/callback"] });
  await invalid({ redirect_uris: ["https://chatgpt.com.evil.example/callback"] });
  await invalid({ redirect_uris: ["https://user:pass@chatgpt.com/callback"] });
  await invalid({ redirect_uris: [`${REDIRECT}#fragment`] });
  await invalid({ redirect_uris: ["javascript:alert(1)"] });
  await invalid({ redirect_uris: [] });
  await invalid({ redirect_uris: "https://chatgpt.com/cb" });
  await invalid({ redirect_uris: Array.from({ length: 6 }, (_, index) => `https://chatgpt.com/cb${index}`) });
  await invalid({ redirect_uris: [`https://chatgpt.com/${"a".repeat(600)}`] });
  assert.equal((await app.register({ redirect_uris: ["http://localhost:8123/callback", "http://127.0.0.1:9/cb", "https://claude.ai/api/mcp/auth_callback"] })).status, 201);
  await invalid({ token_endpoint_auth_method: "client_secret_basic" }, "invalid_client_metadata");
  await invalid({ grant_types: ["password"] }, "invalid_client_metadata");
  await invalid({ response_types: ["token"] }, "invalid_client_metadata");
  await invalid({ scope: "admin" }, "invalid_client_metadata");
});

test("registration rejects non-JSON bodies, invalid JSON and oversized bodies", async (t) => {
  const app = await fixture(t);
  const post = (headers, body) => app.request("/register", { method: "POST", headers, body });
  assert.equal((await post({ "content-type": "text/plain" }, "{}")).status, 400);
  assert.equal((await post({ "content-type": "application/json" }, "{not json")).status, 400);
  assert.equal((await post({ "content-type": "application/json" }, "[]")).status, 400);
  assert.equal((await post({ "content-type": "application/json" }, JSON.stringify({ client_name: "x".repeat(70_000) }))).status, 400);
});

test("a flood of registrations stores nothing", async (t) => {
  const app = await fixture(t);
  for (let index = 0; index < 200; index += 1) assert.equal((await app.register({ client_name: `bot-${index}` })).status, 201);
  assert.equal((await app.register({ client_name: "ChatGPT" })).status, 201, "legitimate registration is never locked out");
});

test("authorization requests are validated before anything is shown", async (t) => {
  const app = await fixture(t);
  const client = await app.registered();
  const reject = async (values, error, status = 400) => {
    const { response, html } = await app.begin(client, values);
    assert.equal(response.status, status, JSON.stringify(values));
    assert.equal(JSON.parse(html).error, error, JSON.stringify(values));
  };
  await reject({ client_id: "garbage" }, "invalid_client");
  await reject({ redirect_uri: "https://chatgpt.com/other" }, "invalid_client");
  await reject({ response_type: "token" }, "invalid_request");
  await reject({ code_challenge_method: "plain" }, "invalid_request");
  await reject({ code_challenge: "short" }, "invalid_request");
  await reject({ state: "s".repeat(1025) }, "invalid_request");
  await reject({ resource: undefined }, "invalid_target");
  await reject({ resource: "https://other.example.test/mcp" }, "invalid_target");
  await reject({ scope: "admin" }, "invalid_scope");
  const duplicated = await app.request(`/authorize?${app.authorizeQuery(client)}&state=again`);
  assert.equal(duplicated.status, 400);
  assert.equal((await app.begin(client, { scope: undefined })).response.status, 200, "scope defaults to the supported set");
});

test("the consent page is safe: escaped, framed-off, cookie-bound and referrer-free", async (t) => {
  const app = await fixture(t);
  const client = await app.registered({ client_name: '<img src=x onerror="alert(1)">' });
  const flow = await app.begin(client);
  assert.equal(flow.response.status, 200);
  assert.ok(flow.html.includes("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"));
  assert.ok(!flow.html.includes("<img"));
  const headers = flow.response.headers;
  const csp = headers.get("content-security-policy");
  assert.ok(csp.includes("form-action 'self' https://github.com"), "the form may redirect to GitHub sign-in");
  assert.ok(csp.includes("frame-ancestors 'none'"));
  assert.equal(headers.get("referrer-policy"), "no-referrer");
  assert.equal(headers.get("cache-control"), "no-store");
  const cookie = headers.getSetCookie().find((entry) => entry.startsWith("__Host-idp_tx="));
  for (const flag of ["HttpOnly", "Secure", "SameSite=Lax", "Path=/"]) assert.ok(cookie.includes(flag), flag);
  assert.ok(flow.html.includes("chrome.example.test"));
});

test("the whole sign-in works: GitHub once, then a verifiable token for the service", async (t) => {
  const app = await fixture(t);
  const flow = await app.signIn();
  // Step 1: the server sends the browser to GitHub with its single fixed callback and PKCE.
  const github = new URL(flow.github.location);
  assert.equal(flow.github.response.status, 302);
  assert.equal(github.origin + github.pathname, "https://github.com/login/oauth/authorize");
  assert.equal(github.searchParams.get("client_id"), "gh-client");
  assert.equal(github.searchParams.get("redirect_uri"), `${ISSUER}/github/callback`);
  assert.equal(github.searchParams.get("scope"), "read:user");
  assert.equal(github.searchParams.get("code_challenge_method"), "S256");
  assert.equal(github.searchParams.get("allow_signup"), "false");
  // Step 2: GitHub returns; the server verifies the owner and hands the client a code.
  assert.equal(flow.returned.status, 302);
  assert.equal(flow.target.origin + flow.target.pathname, REDIRECT);
  assert.equal(flow.target.searchParams.get("state"), "client-state");
  assert.equal(flow.target.searchParams.get("iss"), ISSUER);
  assert.ok(flow.code);
  assert.match(flow.returned.headers.get("set-cookie"), /Max-Age=0/);
  const exchange = app.github.exchanges[0];
  assert.equal(exchange.client_secret, "gh-secret");
  assert.equal(exchange.redirect_uri, `${ISSUER}/github/callback`);
  assert.equal(challenge(exchange.code_verifier), github.searchParams.get("code_challenge"), "GitHub PKCE verifier matches the challenge sent");
  // Step 3: the client redeems the code with its own PKCE verifier.
  const redeemed = await app.exchange(app.tokens({ code: flow.code, client_id: flow.client.client_id }));
  assert.equal(redeemed.status, 200);
  assert.equal(redeemed.headers.get("cache-control"), "no-store");
  const issued = await redeemed.json();
  assert.equal(issued.token_type, "Bearer");
  assert.equal(issued.expires_in, 3600);
  assert.equal(issued.scope, "browser");
  const claims = verifyAccess(app.idp, issued.access_token, { requiredScope: "browser" });
  assert.equal(claims.sub, OWNER);
  assert.equal(claims.aud, RESOURCE);
  assert.equal(claims.iss, ISSUER);
  assert.equal(claims.client_id, flow.client.client_id);
  assert.equal(claims.exp - claims.iat, 3600);
  // Step 4: refresh issues a fresh, equally valid token pair.
  const refreshed = await app.exchange({ grant_type: "refresh_token", refresh_token: issued.refresh_token, client_id: flow.client.client_id });
  assert.equal(refreshed.status, 200);
  const next = await refreshed.json();
  assert.equal(verifyAccess(app.idp, next.access_token).sub, OWNER);
  assert.notEqual(next.refresh_token, issued.refresh_token);
});

test("an authorization code works once and a wrong verifier does not burn it", async (t) => {
  const app = await fixture(t);
  const flow = await app.signIn();
  const base = { code: flow.code, client_id: flow.client.client_id };
  const wrong = await app.exchange(app.tokens({ ...base, code_verifier: "w".repeat(64) }));
  assert.equal(wrong.status, 400);
  assert.equal((await wrong.json()).error, "invalid_grant");
  assert.equal((await app.exchange(app.tokens({ ...base, code_verifier: "short" }))).status, 400);
  assert.equal((await app.exchange(app.tokens(base))).status, 200, "the real client can still redeem it");
  const replay = await app.exchange(app.tokens(base));
  assert.equal(replay.status, 400);
  assert.equal((await replay.json()).error, "invalid_grant");
});

test("a code is bound to its client, redirect address and service", async (t) => {
  const app = await fixture(t);
  const flow = await app.signIn();
  const other = await app.registered();
  const reject = async (values, error) => {
    const response = await app.exchange(app.tokens({ code: flow.code, client_id: flow.client.client_id, ...values }));
    assert.equal(response.status, 400);
    assert.equal((await response.json()).error, error);
  };
  await reject({ client_id: other.client_id }, "invalid_grant");
  await reject({ redirect_uri: "https://chatgpt.com/other" }, "invalid_grant");
  await reject({ resource: "https://other.example.test/mcp" }, "invalid_target");
  await reject({ code: "garbage" }, "invalid_grant");
  await reject({ grant_type: "password" }, "unsupported_grant_type");
  assert.equal((await app.exchange(app.tokens({ code: flow.code, client_id: flow.client.client_id, resource: RESOURCE }))).status, 200);
});

test("only the allowed GitHub account can sign in", async (t) => {
  const app = await fixture(t);
  const stranger = await app.signIn({ githubCode: "code-stranger" });
  assert.equal(stranger.returned.status, 403);
  assert.equal(stranger.returned.headers.get("location"), null);
  assert.ok(!(await stranger.returned.text()).includes("code="));
  const refused = await app.signIn({ githubCode: "code-unknown" });
  assert.equal(refused.returned.status, 502, "a failed GitHub exchange never yields a code");
  assert.equal(refused.code, undefined);
  const denied = await app.signIn({ githubCode: null });
  assert.equal(denied.returned.status, 400);
});

test("GitHub declining the sign-in ends it without a code", async (t) => {
  const app = await fixture(t);
  const client = await app.registered();
  const flow = await app.begin(client);
  const state = new URL((await app.toGithub(flow)).location).searchParams.get("state");
  const response = await app.callback(state, undefined, flow.cookie, { error: "access_denied" });
  assert.equal(response.status, 400);
  assert.equal(response.headers.get("location"), null);
});

test("the sign-in only completes in the browser that started it", async (t) => {
  const app = await fixture(t);
  const client = await app.registered();
  const flow = await app.begin(client);
  const other = await app.begin(client);
  assert.equal((await app.form("/authorize", { tx: flow.tx })).status, 400, "no cookie");
  assert.equal((await app.form("/authorize", { tx: flow.tx }, { cookie: other.cookie })).status, 400, "another flow's cookie");
  const state = new URL((await app.toGithub(flow)).location).searchParams.get("state");
  assert.equal((await app.callback(state, "code-owner", undefined)).status, 400);
  assert.equal((await app.callback(state, "code-owner", other.cookie)).status, 400);
  assert.equal((await app.callback(`${state.slice(0, -3)}AAA`, "code-owner", flow.cookie)).status, 400, "tampered state");
  assert.equal((await app.callback("garbage", "code-owner", flow.cookie)).status, 400);
  assert.equal((await app.callback(state, "code-owner", flow.cookie)).status, 302, "the right browser succeeds");
});

test("a transaction cannot be reused as another kind of value", async (t) => {
  const app = await fixture(t);
  const client = await app.registered();
  const flow = await app.begin(client);
  const state = new URL((await app.toGithub(flow)).location).searchParams.get("state");
  assert.equal((await app.form("/authorize", { tx: state }, { cookie: flow.cookie })).status, 400, "gh state is not a tx");
  assert.equal((await app.exchange(app.tokens({ code: flow.tx, client_id: client.client_id }))).status, 400, "tx is not a code");
  assert.equal((await app.exchange({ grant_type: "refresh_token", refresh_token: client.client_id, client_id: client.client_id })).status, 400, "client_id is not a refresh token");
});

test("every stage expires", async (t) => {
  const app = await fixture(t);
  const client = await app.registered();
  const late = await app.begin(client);
  app.clock.ms += 601_000;
  assert.equal((await app.form("/authorize", { tx: late.tx }, { cookie: late.cookie })).status, 400, "consent page expired");

  const flow = await app.begin(client);
  const state = new URL((await app.toGithub(flow)).location).searchParams.get("state");
  app.clock.ms += 601_000;
  assert.equal((await app.callback(state, "code-owner", flow.cookie)).status, 400, "GitHub round trip expired");

  const sign = await app.signIn();
  app.clock.ms += 61_000;
  assert.equal((await app.exchange(app.tokens({ code: sign.code, client_id: sign.client.client_id }))).status, 400, "code expired");

  const fresh = await app.signIn();
  const issued = await (await app.exchange(app.tokens({ code: fresh.code, client_id: fresh.client.client_id }))).json();
  app.clock.ms += 3600_000 + 61_000;
  assert.throws(() => verifyAccess(app.idp, issued.access_token, { now: Math.floor(app.clock.ms / 1000) }), /invalid_token/);
  assert.equal((await app.exchange({ grant_type: "refresh_token", refresh_token: issued.refresh_token, client_id: fresh.client.client_id })).status, 200);
  app.clock.ms += 31 * 24 * 3600_000;
  assert.equal((await app.exchange({ grant_type: "refresh_token", refresh_token: issued.refresh_token, client_id: fresh.client.client_id })).status, 400, "refresh token expired");
  app.clock.ms += 2 * 365 * 24 * 3600_000;
  assert.equal((await app.begin(client)).response.status, 400, "client registration expired after two years");
});

test("refresh tokens are bound to their client and survive a restart", async (t) => {
  const app = await fixture(t);
  const flow = await app.signIn();
  const issued = await (await app.exchange(app.tokens({ code: flow.code, client_id: flow.client.client_id }))).json();
  const refresh = (values) => app.exchange({ grant_type: "refresh_token", refresh_token: issued.refresh_token, client_id: flow.client.client_id, ...values });
  assert.equal((await refresh({ client_id: (await app.registered()).client_id })).status, 400);
  assert.equal((await refresh({ resource: "https://other.example.test/mcp" })).status, 400);
  const restarted = await fixture(t);
  const revived = await restarted.exchange({ grant_type: "refresh_token", refresh_token: issued.refresh_token, client_id: flow.client.client_id });
  assert.equal(revived.status, 200, "no stored state: a fresh process honours the old refresh token");
  assert.equal(verifyAccess(restarted.idp, (await revived.json()).access_token).sub, OWNER);
});

test("access is revoked by configuration alone", async (t) => {
  const app = await fixture(t);
  const flow = await app.signIn();
  const issued = await (await app.exchange(app.tokens({ code: flow.code, client_id: flow.client.client_id }))).json();
  const refresh = (other) => other.exchange({ grant_type: "refresh_token", refresh_token: issued.refresh_token, client_id: flow.client.client_id });
  assert.equal((await refresh(await fixture(t, { allowedGithubIds: ["111"] }))).status, 400, "owner removed");
  assert.equal((await refresh(await fixture(t, { allowedResources: ["https://other.example.test/mcp"] }))).status, 400, "service removed");
  assert.equal((await refresh(await fixture(t, { notBefore: Math.floor(START / 1000) + 1 }))).status, 400, "everything issued before the cut-off");
  assert.equal((await refresh(await fixture(t, { notBefore: Math.floor(START / 1000) }))).status, 200);
  assert.equal((await refresh(await fixture(t, { secret: `${SECRET}-rotated` }))).status, 400, "rotating the secret revokes everything");
});

test("redirect hosts can be restricted further or widened by configuration", async (t) => {
  const narrow = await fixture(t, { allowedRedirectHosts: ["claude.ai"] });
  assert.equal((await narrow.register()).status, 400);
  const wide = await fixture(t, { allowedRedirectHosts: ["example.org"] });
  assert.equal((await wide.register({ redirect_uris: ["https://example.org/cb"] })).status, 201);
});

test("multiple services share one login: each token is only valid for its own service", async (t) => {
  const second = "https://rdc.example.test/mcp";
  const app = await fixture(t, { allowedResources: [RESOURCE, second] });
  const flow = await app.signIn({ authorize: { resource: second } });
  const issued = await (await app.exchange(app.tokens({ code: flow.code, client_id: flow.client.client_id }))).json();
  assert.equal(verifyAccess(app.idp, issued.access_token, { audience: second }).aud, second);
  assert.throws(() => verifyAccess(app.idp, issued.access_token, { audience: RESOURCE }), /invalid_token/);
});

test("auto-approve (test lane only) signs in without GitHub and still enforces the other gates", async (t) => {
  const app = await fixture(t, { autoApprove: true, githubClientId: undefined, githubClientSecret: undefined, fetch: async () => { throw new Error("GitHub must not be contacted"); } });
  assert.equal(app.idp.ready, true);
  assert.deepEqual(await (await app.request("/healthz")).json(), { status: "ok", auto_approve: true });

  const client = await app.registered();
  const flow = await app.begin(client);
  assert.equal(flow.response.status, 200);
  const approved = await app.form("/authorize", { tx: flow.tx }, { cookie: flow.cookie });
  assert.equal(approved.status, 302);
  const target = new URL(approved.headers.get("location"));
  assert.equal(target.origin + target.pathname, REDIRECT);
  assert.equal(target.searchParams.get("state"), "client-state");
  assert.equal(target.searchParams.get("iss"), ISSUER);

  const tokens = await (await app.exchange(app.tokens({ code: target.searchParams.get("code"), client_id: client.client_id }))).json();
  const jwk = app.idp.jwk;
  const claims = verifyJwt(tokens.access_token, { keys: new Map([[jwk.kid, publicKeyFromJwk(jwk).key]]), issuer: ISSUER, audience: RESOURCE, now: START / 1000 });
  assert.equal(claims.sub, OWNER);

  // The browser binding, the resource allowlist and the redirect allowlist still apply.
  assert.equal((await app.form("/authorize", { tx: flow.tx })).status, 400);
  assert.equal((await app.begin(client, { resource: "https://evil.example.test/mcp" })).response.status, 400);
  assert.equal((await app.registered({ redirect_uris: ["https://evil.example.test/cb"] })).error, "invalid_redirect_uri");
});

test("without auto-approve GitHub credentials stay mandatory and nothing is auto-approved", async (t) => {
  const app = await fixture(t, { githubClientId: undefined });
  assert.equal(app.idp.ready, false);
  assert.deepEqual(app.idp.missing, ["IDP_GITHUB_CLIENT_ID"]);
  const healthy = await fixture(t);
  assert.deepEqual(await (await healthy.request("/healthz")).json(), { status: "ok" });
});

test("IDP_AUTO_APPROVE is opt-in and exactly '1'", () => {
  assert.equal(configFromEnv({}).autoApprove, false);
  assert.equal(configFromEnv({ IDP_AUTO_APPROVE: "true" }).autoApprove, false);
  assert.equal(configFromEnv({ IDP_AUTO_APPROVE: "1" }).autoApprove, true);
});
