// Central OAuth 2.1 authorization server for Alice services. The owner signs in
// with GitHub here once; services (the Chrome MCP worker and others) only verify
// the EdDSA access tokens this server issues. GitHub therefore needs exactly one
// callback URL, ever.
//
// The server is stateless: dynamic client registrations, authorization
// transactions, authorization codes and refresh tokens are sealed values (see
// crypto.mjs), so it scales to zero, survives restarts, and an anonymous
// registration flood has nothing to fill up.
import { cookieMac, createKeys, MIN_SECRET_LENGTH, open, randomToken, safeEqual, seal, sha256b64u } from "./crypto.mjs";
import { signJwt } from "./jwt-sign.mjs";

const ACCESS_TTL = 3600;
const REFRESH_TTL = 30 * 24 * 3600;
const CODE_TTL = 60;
const TRANSACTION_TTL = 600;
const CLIENT_TTL = 2 * 365 * 24 * 3600;
const MAX_BODY = 64 * 1024;
const MAX_REDIRECT_URIS = 5;
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);
const DEFAULT_REDIRECT_HOSTS = ["chatgpt.com", "chat.openai.com", "claude.ai", "claude.com"];
const GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize";
const GITHUB_TOKEN = "https://github.com/login/oauth/access_token";
const GITHUB_USER = "https://api.github.com/user";
const MIN_PASSPHRASE_LENGTH = 16;
const MAX_PASSPHRASE_FAILURES = 5;
const LOCKOUT_SECONDS = 600;
const GRANT_TYPES = ["authorization_code", "refresh_token"];

const escapeHtml = (value) =>
  String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

function parseOrigin(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error("invalid_idp_public_url");
  }
  const secure = url.protocol === "https:";
  const loopback = url.protocol === "http:" && LOOPBACK_HOSTS.has(url.hostname);
  if (!(secure || loopback) || url.username || url.password || url.search || url.hash || url.pathname !== "/") {
    throw new Error("invalid_idp_public_url");
  }
  return url.origin;
}

async function readBody(request, type) {
  if ((request.headers["content-type"] ?? "").split(";")[0].trim().toLowerCase() !== type) throw new Error("invalid_request");
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY) throw new Error("invalid_request");
    chunks.push(chunk);
  }
  const text = Buffer.concat(chunks).toString("utf8");
  if (type === "application/json") {
    const value = JSON.parse(text);
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid_request");
    return value;
  }
  return singleParams(new URLSearchParams(text));
}

// Each OAuth parameter may appear at most once.
function singleParams(search) {
  for (const key of search.keys()) if (search.getAll(key).length !== 1) throw new Error("invalid_request");
  return Object.fromEntries(search);
}

function cookieValue(request, name) {
  for (const part of String(request.headers.cookie ?? "").split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return rest.join("=");
  }
  return "";
}

export function createIdp(options = {}) {
  const now = () => Math.floor((options.now?.() ?? Date.now()) / 1000);
  const fetchRemote = options.fetch ?? fetch;
  const log = options.log ?? ((entry) => process.stderr.write(`${JSON.stringify(entry)}\n`));
  const scopes = [...(options.scopes?.length ? options.scopes : ["mcp"])];
  const allowedIds = new Set((options.allowedGithubIds ?? []).map(String));
  const allowedResources = new Set(options.allowedResources ?? []);
  const redirectHosts = new Set(options.allowedRedirectHosts ?? DEFAULT_REDIRECT_HOSTS);
  const notBefore = Number(options.notBefore ?? 0);
  // Test lane: the owner proves identity with a passphrase instead of GitHub. Every
  // other gate (browser binding, redirect and resource allowlists, PKCE) still applies.
  const passphraseConfigured = options.ownerPassphrase !== undefined;
  const passphraseMode = passphraseConfigured && String(options.ownerPassphrase).length >= MIN_PASSPHRASE_LENGTH;
  const passphraseDigest = passphraseMode ? sha256b64u(options.ownerPassphrase) : "";
  let failures = [];
  const usedCodes = new Map();

  // Fail closed: report which settings are missing (names only, never values).
  const missing = [];
  if (typeof options.secret !== "string" || options.secret.length < MIN_SECRET_LENGTH) missing.push("IDP_SECRET");
  if (!options.publicUrl) missing.push("IDP_PUBLIC_URL");
  if (passphraseConfigured && !passphraseMode) missing.push("IDP_OWNER_PASSPHRASE");
  if (!passphraseConfigured && !options.githubClientId) missing.push("IDP_GITHUB_CLIENT_ID");
  if (!passphraseConfigured && !options.githubClientSecret) missing.push("IDP_GITHUB_CLIENT_SECRET");
  if (!allowedIds.size) missing.push("IDP_ALLOWED_GITHUB_IDS");
  if (!allowedResources.size) missing.push("IDP_ALLOWED_RESOURCES");
  // Production holds GitHub credentials; passphrase sign-in must never be reachable there.
  if (passphraseConfigured && (options.githubClientId || options.githubClientSecret)) {
    missing.push("IDP_OWNER_PASSPHRASE_conflicts_with_IDP_GITHUB_credentials");
  }
  const ready = missing.length === 0;
  const issuer = ready ? parseOrigin(options.publicUrl) : "";
  const keys = ready ? createKeys(options.secret) : null;
  const callback = `${issuer}/github/callback`;
  const cookieName = issuer.startsWith("https:") ? "__Host-idp_tx" : "idp_tx";

  const metadata = {
    issuer,
    authorization_endpoint: `${issuer}/authorize`,
    token_endpoint: `${issuer}/token`,
    registration_endpoint: `${issuer}/register`,
    jwks_uri: `${issuer}/jwks.json`,
    response_types_supported: ["code"],
    grant_types_supported: GRANT_TYPES,
    code_challenge_methods_supported: ["S256"],
    token_endpoint_auth_methods_supported: ["none"],
    scopes_supported: scopes,
    authorization_response_iss_parameter_supported: true,
  };

  const securityHeaders = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
  };

  function json(response, status, value, headers = {}) {
    const body = JSON.stringify(value);
    response.writeHead(status, {
      "content-type": "application/json; charset=utf-8",
      "content-length": Buffer.byteLength(body),
      ...securityHeaders,
      ...headers,
    });
    response.end(body);
  }

  const oauthError = (response, status, error) => json(response, status, { error });

  function page(response, status, title, body, headers = {}) {
    response.writeHead(status, {
      "content-type": "text/html; charset=utf-8",
      "content-security-policy":
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https://github.com; frame-ancestors 'none'; base-uri 'none'",
      ...securityHeaders,
      ...headers,
    });
    response.end(
      `<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(title)}</title>` +
        `<style>body{font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem}button{font:inherit;padding:.6rem 1rem}</style>${body}</html>`,
    );
  }

  const clearCookie = () => `${cookieName}=; HttpOnly; ${issuer.startsWith("https:") ? "Secure; " : ""}SameSite=Lax; Path=/; Max-Age=0`;

  // The browser that started the sign-in must be the one that finishes it.
  function boundToBrowser(request, nonce) {
    const supplied = cookieValue(request, cookieName);
    return Boolean(supplied) && safeEqual(supplied, `${nonce}.${cookieMac(keys, nonce)}`);
  }

  function validRedirect(value) {
    if (typeof value !== "string" || value.length > 512) return false;
    let url;
    try {
      url = new URL(value);
    } catch {
      return false;
    }
    if (url.username || url.password || url.hash) return false;
    if (url.protocol === "https:") return redirectHosts.has(url.hostname);
    return url.protocol === "http:" && LOOPBACK_HOSTS.has(url.hostname);
  }

  function grantedScope(requested) {
    if (requested === undefined || requested === "") return scopes.join(" ");
    const list = String(requested).split(" ").filter(Boolean);
    return list.length && list.every((scope) => scopes.includes(scope)) ? [...new Set(list)].join(" ") : null;
  }

  function register(input, response) {
    const uris = input.redirect_uris;
    if (!Array.isArray(uris) || !uris.length || uris.length > MAX_REDIRECT_URIS || !uris.every(validRedirect)) {
      return oauthError(response, 400, "invalid_redirect_uri");
    }
    const method = input.token_endpoint_auth_method;
    const grants = input.grant_types;
    const responses = input.response_types;
    if (
      (method && method !== "none") ||
      (grants && (!Array.isArray(grants) || grants.some((grant) => !GRANT_TYPES.includes(grant)))) ||
      (responses && (!Array.isArray(responses) || responses.some((type) => type !== "code"))) ||
      (input.scope !== undefined && grantedScope(input.scope) === null)
    ) {
      return oauthError(response, 400, "invalid_client_metadata");
    }
    const issued = now();
    const name = String(input.client_name ?? "MCP client").slice(0, 100);
    const redirectUris = [...new Set(uris)];
    const clientId = seal(keys, "client", { name, redirect_uris: redirectUris, iat: issued, exp: issued + CLIENT_TTL });
    json(response, 201, {
      client_id: clientId,
      client_id_issued_at: issued,
      client_name: name,
      redirect_uris: redirectUris,
      token_endpoint_auth_method: "none",
      grant_types: GRANT_TYPES,
      response_types: ["code"],
      scope: scopes.join(" "),
    });
  }

  function begin(request, response, url) {
    let params;
    try {
      params = singleParams(url.searchParams);
    } catch {
      return oauthError(response, 400, "invalid_request");
    }
    const client = open(keys, "client", params.client_id, now());
    if (!client || !client.redirect_uris.includes(params.redirect_uri)) return oauthError(response, 400, "invalid_client");
    if (
      params.response_type !== "code" ||
      params.code_challenge_method !== "S256" ||
      !/^[A-Za-z0-9_-]{43}$/.test(params.code_challenge ?? "") ||
      (params.state?.length ?? 0) > 1024
    ) {
      return oauthError(response, 400, "invalid_request");
    }
    // RFC 8707: tokens are issued only for services the owner pre-approved.
    if (!allowedResources.has(params.resource)) return oauthError(response, 400, "invalid_target");
    const scope = grantedScope(params.scope);
    if (scope === null) return oauthError(response, 400, "invalid_scope");
    const nonce = randomToken(16);
    const transaction = seal(keys, "tx", {
      client_id: params.client_id,
      redirect_uri: params.redirect_uri,
      code_challenge: params.code_challenge,
      resource: params.resource,
      scope,
      state: params.state ?? null,
      nonce,
      exp: now() + TRANSACTION_TTL,
    });
    const cookie = `${cookieName}=${nonce}.${cookieMac(keys, nonce)}; HttpOnly; ${issuer.startsWith("https:") ? "Secure; " : ""}SameSite=Lax; Path=/; Max-Age=${TRANSACTION_TTL}`;
    page(
      response,
      200,
      "Вход в сервисы Alice",
      `<h1>Доступ к вашим сервисам</h1><p>Приложение: ${escapeHtml(client.name)}.</p>` +
        `<p>Адрес возврата: ${escapeHtml(new URL(params.redirect_uri).origin)}.</p>` +
        `<p>Сервис: ${escapeHtml(new URL(params.resource).host)}. Права: ${escapeHtml(scope)}.</p>` +
        (passphraseMode
          ? `<p>Тестовая среда: вход по парольной фразе владельца.</p>` +
            `<form method="post" action="/authorize"><input type="hidden" name="tx" value="${escapeHtml(transaction)}">` +
            `<p><input type="password" name="passphrase" autocomplete="current-password" required></p><button type="submit">Разрешить</button></form>`
          : `<p>Вход доступен только владельцу через GitHub.</p>` +
            `<form method="post" action="/authorize"><input type="hidden" name="tx" value="${escapeHtml(transaction)}"><button type="submit">Разрешить и войти через GitHub</button></form>`),
      { "set-cookie": cookie },
    );
  }

  async function startGithub(request, response) {
    const input = await readBody(request, "application/x-www-form-urlencoded");
    const transaction = open(keys, "tx", input.tx, now());
    if (!transaction || !boundToBrowser(request, transaction.nonce)) return oauthError(response, 400, "invalid_request");
    if (passphraseMode) {
      failures = failures.filter((time) => time > now() - LOCKOUT_SECONDS);
      if (failures.length >= MAX_PASSPHRASE_FAILURES) {
        return page(response, 429, "Слишком много попыток", "<h1>Слишком много попыток</h1><p>Подождите десять минут.</p>", { "retry-after": String(LOCKOUT_SECONDS) });
      }
      if (typeof input.passphrase !== "string" || !safeEqual(sha256b64u(input.passphrase), passphraseDigest)) {
        failures.push(now());
        return page(response, 403, "Неверная фраза", "<h1>Неверная фраза</h1><p>Вернитесь назад и повторите.</p>");
      }
      return finish(response, transaction, [...allowedIds][0], { "set-cookie": clearCookie() });
    }
    const verifier = randomToken(32);
    const state = seal(keys, "gh", { ...transaction, ghv: verifier, exp: now() + TRANSACTION_TTL });
    const target = new URL(GITHUB_AUTHORIZE);
    target.search = new URLSearchParams({
      client_id: options.githubClientId,
      redirect_uri: callback,
      scope: "read:user",
      state,
      code_challenge: sha256b64u(verifier),
      code_challenge_method: "S256",
      allow_signup: "false",
    }).toString();
    response.writeHead(302, { location: target.href, ...securityHeaders });
    response.end();
  }

  async function githubUser(code, verifier) {
    const exchange = await fetchRemote(GITHUB_TOKEN, {
      method: "POST",
      headers: { accept: "application/json", "content-type": "application/json", "user-agent": "alice-oauth-idp" },
      body: JSON.stringify({
        client_id: options.githubClientId,
        client_secret: options.githubClientSecret,
        code,
        redirect_uri: callback,
        code_verifier: verifier,
      }),
      signal: AbortSignal.timeout(10_000),
    });
    const granted = exchange.ok ? await exchange.json() : null;
    if (typeof granted?.access_token !== "string") throw new Error("github_exchange_failed");
    const profile = await fetchRemote(GITHUB_USER, {
      headers: { accept: "application/vnd.github+json", authorization: `Bearer ${granted.access_token}`, "user-agent": "alice-oauth-idp" },
      signal: AbortSignal.timeout(10_000),
    });
    const user = profile.ok ? await profile.json() : null;
    if (!Number.isSafeInteger(user?.id)) throw new Error("github_profile_failed");
    return String(user.id);
  }

  async function githubCallback(request, response, url) {
    const params = singleParams(url.searchParams);
    const flow = open(keys, "gh", params.state, now());
    const headers = { "set-cookie": clearCookie() };
    if (!flow || !boundToBrowser(request, flow.nonce)) {
      return page(response, 400, "Вход не удался", "<h1>Вход не удался</h1><p>Сессия входа устарела. Начните подключение заново.</p>", headers);
    }
    if (params.error || typeof params.code !== "string") {
      return page(response, 400, "Вход отменён", "<h1>Вход отменён</h1><p>GitHub не подтвердил вход.</p>", headers);
    }
    let subject;
    try {
      subject = await githubUser(params.code, flow.ghv);
    } catch (error) {
      log({ event: "idp_github_failed", error: /^[a-z_]+$/.test(error?.message ?? "") ? error.message : "unexpected" });
      return page(response, 502, "Вход не удался", "<h1>Вход не удался</h1><p>Не удалось связаться с GitHub. Попробуйте ещё раз.</p>", headers);
    }
    if (!allowedIds.has(subject)) {
      log({ event: "idp_login_denied" });
      return page(response, 403, "Нет доступа", "<h1>Нет доступа</h1><p>Этот аккаунт GitHub не допущен.</p>", headers);
    }
    return finish(response, flow, subject, headers);
  }

  function finish(response, flow, subject, headers) {
    const code = seal(keys, "code", {
      jti: randomToken(16),
      sub: subject,
      client_id: flow.client_id,
      redirect_uri: flow.redirect_uri,
      code_challenge: flow.code_challenge,
      resource: flow.resource,
      scope: flow.scope,
      exp: now() + CODE_TTL,
    });
    const target = new URL(flow.redirect_uri);
    target.searchParams.set("code", code);
    if (flow.state !== null) target.searchParams.set("state", flow.state);
    target.searchParams.set("iss", issuer);
    response.writeHead(302, { location: target.href, ...securityHeaders, ...headers });
    response.end();
  }

  // Re-checks the owner and the service on every issue and refresh, so removing
  // either from the configuration revokes access without any stored state.
  function issueTokens(grant) {
    if (!allowedIds.has(grant.sub) || !allowedResources.has(grant.resource)) return null;
    const issued = now();
    return {
      access_token: signJwt(keys, {
        iss: issuer,
        sub: grant.sub,
        aud: grant.resource,
        scope: grant.scope,
        client_id: grant.client_id,
        iat: issued,
        exp: issued + ACCESS_TTL,
        jti: randomToken(12),
      }),
      token_type: "Bearer",
      expires_in: ACCESS_TTL,
      refresh_token: seal(keys, "refresh", {
        sub: grant.sub,
        client_id: grant.client_id,
        resource: grant.resource,
        scope: grant.scope,
        iat: issued,
        exp: issued + REFRESH_TTL,
      }),
      scope: grant.scope,
    };
  }

  function pruneUsedCodes() {
    for (const [id, expires] of usedCodes) if (expires <= now()) usedCodes.delete(id);
  }

  async function token(request, response) {
    const input = await readBody(request, "application/x-www-form-urlencoded");
    let grant = null;
    if (input.grant_type === "authorization_code") {
      const code = open(keys, "code", input.code, now());
      if (!code || code.client_id !== input.client_id || code.redirect_uri !== input.redirect_uri) {
        return oauthError(response, 400, "invalid_grant");
      }
      if (input.resource && input.resource !== code.resource) return oauthError(response, 400, "invalid_target");
      const verifier = input.code_verifier ?? "";
      if (!/^[A-Za-z0-9._~-]{43,128}$/.test(verifier) || !safeEqual(sha256b64u(verifier), code.code_challenge)) {
        return oauthError(response, 400, "invalid_grant");
      }
      pruneUsedCodes();
      if (usedCodes.has(code.jti)) return oauthError(response, 400, "invalid_grant");
      usedCodes.set(code.jti, code.exp);
      grant = code;
    } else if (input.grant_type === "refresh_token") {
      const refresh = open(keys, "refresh", input.refresh_token, now());
      if (!refresh || refresh.client_id !== input.client_id || refresh.iat < notBefore) {
        return oauthError(response, 400, "invalid_grant");
      }
      if (input.resource && input.resource !== refresh.resource) return oauthError(response, 400, "invalid_target");
      grant = refresh;
    } else {
      return oauthError(response, 400, "unsupported_grant_type");
    }
    const tokens = issueTokens(grant);
    if (!tokens) return oauthError(response, 400, "invalid_grant");
    json(response, 200, tokens);
  }

  async function handle(request, response) {
    const url = new URL(request.url, "http://idp.invalid");
    const path = url.pathname;
    try {
      if (!ready) {
        if (path === "/healthz") return json(response, 503, { status: "misconfigured", missing });
        return oauthError(response, 503, "idp_not_configured");
      }
      const get = request.method === "GET";
      const post = request.method === "POST";
      if (get && path === "/healthz") return json(response, 200, passphraseMode ? { status: "ok", sign_in: "passphrase" } : { status: "ok" });
      if (get && path === "/.well-known/oauth-authorization-server") {
        return json(response, 200, metadata, { "access-control-allow-origin": "*", "cache-control": "public, max-age=300" });
      }
      if (get && path === "/jwks.json") {
        return json(response, 200, { keys: [keys.jwk] }, { "access-control-allow-origin": "*", "cache-control": "public, max-age=300" });
      }
      if (post && path === "/register") return register(await readBody(request, "application/json"), response);
      if (get && path === "/authorize") return begin(request, response, url);
      if (post && path === "/authorize") return await startGithub(request, response);
      if (get && path === "/github/callback") return await githubCallback(request, response, url);
      if (post && path === "/token") return await token(request, response);
      return oauthError(response, 404, "not_found");
    } catch (error) {
      const client = error?.message === "invalid_request" || error instanceof SyntaxError;
      if (!client) log({ event: "idp_failed", path, error: /^[a-z_]+$/.test(error?.message ?? "") ? error.message : "unexpected" });
      return oauthError(response, client ? 400 : 500, client ? "invalid_request" : "server_error");
    }
  }

  return { handle, ready, missing, issuer, metadata, jwk: keys?.jwk ?? null };
}
