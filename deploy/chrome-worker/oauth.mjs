import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

const SCOPE = "browser";
const COOKIE = "browser_oauth_transaction";
const OAUTH_PATH = "/browser/oauth";
const MAX_BODY = 16 * 1024;
const ACCESS_TTL = 15 * 60;
const REFRESH_TTL = 30 * 24 * 60 * 60;
const random = () => randomBytes(32).toString("base64url");
const digest = (value) => createHash("sha256").update(value).digest("base64url");
const equal = (left, right) => timingSafeEqual(createHash("sha256").update(String(left)).digest(), createHash("sha256").update(String(right)).digest());
const escape = (value) => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

function json(response, status, payload, extra = {}) {
  response.writeHead(status, { "content-type": "application/json", "cache-control": "no-store", ...extra });
  response.end(JSON.stringify(payload));
}

function redirect(response, location, cookie) {
  response.writeHead(302, { location, "cache-control": "no-store", "referrer-policy": "no-referrer", ...(cookie ? { "set-cookie": cookie } : {}) });
  response.end();
}

function validRedirect(value) {
  try {
    const url = new URL(value);
    const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
    return !url.username && !url.password && !url.hash && (url.protocol === "https:" || (url.protocol === "http:" && loopback));
  } catch { return false; }
}

async function body(request, type) {
  if ((request.headers["content-type"] ?? "").split(";")[0].trim().toLowerCase() !== type) throw new Error("invalid_request");
  let size = 0;
  const chunks = [];
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
  const params = new URLSearchParams(text);
  for (const key of params.keys()) if (params.getAll(key).length !== 1) throw new Error("invalid_request");
  return Object.fromEntries(params);
}

/** Single-owner OAuth bridge; GitHub access tokens are used once and never saved. */
export function createOAuth(options = {}) {
  const env = options.env ?? process.env;
  const publicUrl = options.publicUrl ?? env.BROWSER_PUBLIC_URL ?? "";
  const githubClientId = options.githubClientId ?? env.BROWSER_GITHUB_CLIENT_ID ?? env.ALICE_GITHUB_CLIENT_ID ?? "";
  const githubClientSecret = options.githubClientSecret ?? env.BROWSER_GITHUB_CLIENT_SECRET ?? env.ALICE_GITHUB_CLIENT_SECRET ?? "";
  const ownerId = String(options.ownerId ?? env.BROWSER_GITHUB_ALLOWED_ID ?? env.ALICE_GITHUB_ALLOWED_IDS ?? "").trim();
  const enabled = Boolean(publicUrl && githubClientId && githubClientSecret && /^\d+$/.test(ownerId));
  if (!enabled) return { enabled: false, handle: async () => false, authorize: () => false, challenge: () => "Bearer", metadata: null };
  if (!validRedirect(publicUrl)) throw new Error("invalid_browser_public_url");
  const origin = new URL(publicUrl).origin;
  if (new URL(publicUrl).pathname !== "/" || new URL(publicUrl).search) throw new Error("browser_public_url_must_be_origin");
  const issuer = `${origin}${OAUTH_PATH}`;
  const resource = `${origin}/browser/v1/mcp`;
  const callback = `${issuer}/github/callback`;
  const metadataUrl = `${origin}/.well-known/oauth-protected-resource/browser/v1/mcp`;
  const stateFile = options.stateFile ?? env.BROWSER_OAUTH_STATE_FILE ?? "/tmp/chrome-auth/oauth.json";
  const now = () => Math.floor((options.now?.() ?? Date.now()) / 1000);
  const fetchRemote = options.fetch ?? fetch;
  const cookieFlags = `HttpOnly; SameSite=Lax; Path=${OAUTH_PATH}; ${origin.startsWith("https:") ? "Secure; " : ""}`;
  mkdirSync(dirname(stateFile), { recursive: true, mode: 0o700 });
  let state;
  if (existsSync(stateFile)) {
    state = JSON.parse(readFileSync(stateFile, "utf8"));
    if (state.version !== 1 || typeof state.signingKey !== "string" || !["clients", "pending", "codes", "access", "refresh"].every((key) => state[key] && typeof state[key] === "object" && !Array.isArray(state[key]))) throw new Error("invalid_oauth_state");
    chmodSync(stateFile, 0o600);
  } else {
    state = { version: 1, signingKey: random(), clients: {}, pending: {}, codes: {}, access: {}, refresh: {} };
  }
  let durability = Promise.resolve();
  let registrations = [];
  function prune() {
    for (const collection of ["pending", "codes", "access", "refresh"]) {
      for (const [key, record] of Object.entries(state[collection])) if (record.expires <= now()) delete state[collection][key];
    }
    // Anonymous DCR must not fill persistent storage forever. Keep clients indefinitely
    // once their owner has actually authorized them, as ChatGPT reuses that client ID.
    for (const [key, client] of Object.entries(state.clients)) if (client.provisional_expires && client.provisional_expires <= now()) delete state.clients[key];
  }
  function save() {
    // Serialize BOTH local replacement and remote checkpoint. A later request may
    // update the in-memory snapshot, but cannot replace the file while it is archived.
    // Never return a newly issued credential before its snapshot is durable.
    durability = durability.catch(() => {}).then(async () => {
      prune();
      const temporary = `${stateFile}.tmp`;
      writeFileSync(temporary, JSON.stringify(state), { mode: 0o600 });
      chmodSync(temporary, 0o600);
      renameSync(temporary, stateFile);
      await options.onPersist?.();
    });
    return durability;
  }
  const sign = (value) => `${value}.${createHmac("sha256", state.signingKey).update(value).digest("base64url")}`;
  const cookie = (transaction, clear = false) => `${COOKIE}=${clear ? "" : sign(transaction)}; ${cookieFlags}Max-Age=${clear ? 0 : 600}`;
  function browserBound(request, transaction) {
    const supplied = (request.headers.cookie ?? "").split(";").map((part) => part.trim()).find((part) => part.startsWith(`${COOKIE}=`))?.slice(COOKIE.length + 1);
    return typeof supplied === "string" && equal(supplied, sign(transaction));
  }
  const challenge = () => `Bearer resource_metadata="${metadataUrl}", scope="${SCOPE}"`;
  const metadata = {
    issuer,
    authorization_endpoint: `${issuer}/authorize`,
    token_endpoint: `${issuer}/token`,
    registration_endpoint: `${issuer}/register`,
    revocation_endpoint: `${issuer}/revoke`,
    response_types_supported: ["code"],
    grant_types_supported: ["authorization_code", "refresh_token"],
    token_endpoint_auth_methods_supported: ["none"],
    revocation_endpoint_auth_methods_supported: ["none"],
    code_challenge_methods_supported: ["S256"],
    authorization_response_iss_parameter_supported: true,
    scopes_supported: [SCOPE],
  };

  function authorize(request) {
    const header = request.headers.authorization;
    if (typeof header !== "string" || !header.startsWith("Bearer ") || header.length > 4096) return false;
    const token = state.access[digest(header.slice(7))];
    return Boolean(token && token.expires > now() && token.owner === ownerId && token.resource === resource && token.scope === SCOPE);
  }

  function revokeFamily(family) {
    for (const collection of ["access", "refresh"]) {
      for (const [key, token] of Object.entries(state[collection])) if (token.family === family) delete state[collection][key];
    }
  }

  function issue(client, family = random()) {
    const access = random();
    const refresh = random();
    const shared = { client, family, owner: ownerId, resource, scope: SCOPE };
    state.access[digest(access)] = { ...shared, expires: now() + ACCESS_TTL };
    state.refresh[digest(refresh)] = { ...shared, expires: now() + REFRESH_TTL, used: false };
    return { access_token: access, token_type: "Bearer", expires_in: ACCESS_TTL, refresh_token: refresh, scope: SCOPE };
  }

  async function register(request, response) {
    registrations = registrations.filter((time) => time > now() - 60);
    if (registrations.length >= 30) return json(response, 429, { error: "registration_limit" }, { "retry-after": "60" });
    registrations.push(now());
    const input = await body(request, "application/json");
    if (!Array.isArray(input.redirect_uris) || !input.redirect_uris.length || input.redirect_uris.length > 10 || !input.redirect_uris.every((uri) => typeof uri === "string" && uri.length <= 2048 && validRedirect(uri))) return json(response, 400, { error: "invalid_redirect_uri" });
    if ((input.token_endpoint_auth_method && input.token_endpoint_auth_method !== "none") || (input.grant_types && (!Array.isArray(input.grant_types) || input.grant_types.some((grant) => !metadata.grant_types_supported.includes(grant)))) || (input.response_types && (!Array.isArray(input.response_types) || input.response_types.some((type) => type !== "code"))) || (input.scope && input.scope !== SCOPE)) return json(response, 400, { error: "invalid_client_metadata" });
    if (Object.keys(state.clients).length >= 1000) return json(response, 429, { error: "registration_limit" });
    const clientId = random();
    const client = { client_id: clientId, client_id_issued_at: now(), client_name: String(input.client_name ?? "MCP client").slice(0, 100), redirect_uris: [...new Set(input.redirect_uris)], token_endpoint_auth_method: "none", grant_types: metadata.grant_types_supported, response_types: ["code"], scope: SCOPE };
    state.clients[clientId] = { ...client, provisional_expires: now() + 900 };
    await save();
    json(response, 201, client);
  }

  async function begin(request, response, url) {
    for (const key of url.searchParams.keys()) if (url.searchParams.getAll(key).length !== 1) return json(response, 400, { error: "invalid_request" });
    const params = Object.fromEntries(url.searchParams);
    const client = Object.hasOwn(state.clients, params.client_id) ? state.clients[params.client_id] : null;
    if (!client || !client.redirect_uris.includes(params.redirect_uri)) return json(response, 400, { error: "invalid_client" });
    if (params.response_type !== "code" || params.resource !== resource || (params.scope && params.scope !== SCOPE) || params.code_challenge_method !== "S256" || !/^[A-Za-z0-9_-]{43}$/.test(params.code_challenge ?? "") || (params.state?.length ?? 0) > 1024) return json(response, 400, { error: "invalid_request" });
    if (Object.keys(state.pending).length >= 1000) return json(response, 429, { error: "authorization_limit" });
    const transaction = random();
    state.pending[digest(transaction)] = { ...params, expires: now() + 600, started: false };
    await save();
    response.writeHead(200, {
      "content-type": "text/html; charset=utf-8", "cache-control": "no-store", "set-cookie": cookie(transaction),
      "content-security-policy": "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
      "referrer-policy": "no-referrer", "x-content-type-options": "nosniff",
    });
    response.end(`<!doctype html><html lang="ru"><meta charset="utf-8"><title>Подключить Chrome к ChatGPT</title><h1>Доступ к вашему браузеру</h1><p>Приложение: ${escape(client.client_name)}.</p><p>Адрес возврата: ${escape(new URL(params.redirect_uri).origin)}.</p><p>Подключение разрешит управление Chrome и доступ к сайтам вашего сохранённого профиля. Вход доступен только владельцу через GitHub.</p><form method="post" action="${OAUTH_PATH}/authorize"><input type="hidden" name="transaction" value="${transaction}"><button type="submit">Разрешить и войти через GitHub</button></form></html>`);
  }

  async function githubLogin(request, response) {
    const input = await body(request, "application/x-www-form-urlencoded");
    const transaction = input.transaction ?? "";
    const pending = state.pending[digest(transaction)];
    if (!pending || pending.expires <= now() || pending.started || !browserBound(request, transaction)) return json(response, 400, { error: "invalid_request" });
    pending.started = true;
    // Bind the upstream GitHub authorization code to this same browser transaction.
    pending.github_verifier = random();
    await save();
    const target = new URL("https://github.com/login/oauth/authorize");
    target.search = new URLSearchParams({ client_id: githubClientId, redirect_uri: callback, scope: "read:user", state: transaction, code_challenge: digest(pending.github_verifier), code_challenge_method: "S256", allow_signup: "false" }).toString();
    redirect(response, target.href);
  }

  async function githubCallback(request, response, url) {
    const transaction = url.searchParams.get("state") ?? "";
    const pending = state.pending[digest(transaction)];
    if (!pending || !pending.started || pending.expires <= now() || !browserBound(request, transaction)) return json(response, 400, { error: "invalid_oauth_state" });
    delete state.pending[digest(transaction)];
    await save();
    const target = new URL(pending.redirect_uri);
    target.searchParams.set("iss", issuer);
    if (pending.state) target.searchParams.set("state", pending.state);
    const deny = () => { target.searchParams.set("error", "access_denied"); redirect(response, target.href, cookie("", true)); };
    const code = url.searchParams.get("code");
    if (!code || url.searchParams.has("error")) return deny();
    let account;
    try {
      const tokenResponse = await fetchRemote("https://github.com/login/oauth/access_token", {
        method: "POST", redirect: "error", signal: AbortSignal.timeout(10_000),
        headers: { accept: "application/json", "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ client_id: githubClientId, client_secret: githubClientSecret, code, redirect_uri: callback, code_verifier: pending.github_verifier }),
      });
      const token = await tokenResponse.json();
      if (!tokenResponse.ok || typeof token.access_token !== "string") return deny();
      const userResponse = await fetchRemote("https://api.github.com/user", {
        redirect: "error", signal: AbortSignal.timeout(10_000),
        headers: { accept: "application/vnd.github+json", authorization: `Bearer ${token.access_token}`, "x-github-api-version": "2022-11-28", "user-agent": "Alice-Chrome-MCP" },
      });
      account = await userResponse.json();
      if (!userResponse.ok || !equal(String(account.id), ownerId)) return deny();
    } catch { return deny(); }
    if (!Object.hasOwn(state.clients, pending.client_id)) return deny();
    delete state.clients[pending.client_id].provisional_expires;
    const authorizationCode = random();
    state.codes[digest(authorizationCode)] = { client: pending.client_id, redirect: pending.redirect_uri, challenge: pending.code_challenge, resource, owner: ownerId, expires: now() + 300, used: false };
    await save();
    target.searchParams.set("code", authorizationCode);
    redirect(response, target.href, cookie("", true));
  }

  async function token(request, response) {
    const input = await body(request, "application/x-www-form-urlencoded");
    if (!Object.hasOwn(state.clients, input.client_id)) return json(response, 401, { error: "invalid_client" });
    if (input.resource !== resource || (input.scope && input.scope !== SCOPE)) return json(response, 400, { error: "invalid_target" });
    if (input.grant_type === "authorization_code") {
      const code = state.codes[digest(input.code ?? "")];
      if (!code || code.client !== input.client_id || code.redirect !== input.redirect_uri || code.owner !== ownerId || code.resource !== resource || code.expires <= now() || !/^[A-Za-z0-9._~-]{43,128}$/.test(input.code_verifier ?? "") || !equal(digest(input.code_verifier), code.challenge)) return json(response, 400, { error: "invalid_grant" });
      if (code.used) { revokeFamily(code.family); await save(); return json(response, 400, { error: "invalid_grant" }); }
      code.used = true;
      code.family = random();
      const result = issue(input.client_id, code.family);
      await save();
      return json(response, 200, result);
    }
    if (input.grant_type === "refresh_token") {
      const refresh = state.refresh[digest(input.refresh_token ?? "")];
      if (!refresh || refresh.client !== input.client_id || refresh.owner !== ownerId || refresh.resource !== resource || refresh.expires <= now()) return json(response, 400, { error: "invalid_grant" });
      if (refresh.used) { revokeFamily(refresh.family); await save(); return json(response, 400, { error: "invalid_grant" }); }
      refresh.used = true;
      const result = issue(input.client_id, refresh.family);
      await save();
      return json(response, 200, result);
    }
    json(response, 400, { error: "unsupported_grant_type" });
  }

  async function revoke(request, response) {
    const input = await body(request, "application/x-www-form-urlencoded");
    const key = digest(input.token ?? "");
    const token = state.refresh[key] ?? state.access[key];
    if (token && token.client === input.client_id) { revokeFamily(token.family); await save(); }
    json(response, 200, {});
  }

  async function handle(request, response, suppliedUrl) {
    const url = suppliedUrl instanceof URL ? suppliedUrl : new URL(request.url, origin);
    const path = url.pathname;
    const resourcePaths = ["/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/browser/v1/mcp"];
    const issuerPaths = ["/.well-known/oauth-authorization-server", "/.well-known/oauth-authorization-server/browser/oauth"];
    if (!path.startsWith(`${OAUTH_PATH}/`) && !resourcePaths.includes(path) && !issuerPaths.includes(path)) return false;
    try {
      prune();
      if (request.method === "GET" && resourcePaths.includes(path)) json(response, 200, { resource, authorization_servers: [issuer], scopes_supported: [SCOPE], bearer_methods_supported: ["header"] });
      else if (request.method === "GET" && issuerPaths.includes(path)) json(response, 200, metadata);
      else if (request.method === "POST" && path === `${OAUTH_PATH}/register`) await register(request, response);
      else if (request.method === "GET" && path === `${OAUTH_PATH}/authorize`) await begin(request, response, url);
      else if (request.method === "POST" && path === `${OAUTH_PATH}/authorize`) await githubLogin(request, response);
      else if (request.method === "GET" && path === `${OAUTH_PATH}/github/callback`) await githubCallback(request, response, url);
      else if (request.method === "POST" && path === `${OAUTH_PATH}/token`) await token(request, response);
      else if (request.method === "POST" && path === `${OAUTH_PATH}/revoke`) await revoke(request, response);
      else json(response, 404, { error: "not_found" });
    } catch (error) {
      json(response, error.message === "invalid_request" || error instanceof SyntaxError ? 400 : 500, { error: error.message === "invalid_request" || error instanceof SyntaxError ? "invalid_request" : "oauth_unavailable" });
    }
    return true;
  }

  return { enabled, handle, authorize, challenge, metadata };
}
