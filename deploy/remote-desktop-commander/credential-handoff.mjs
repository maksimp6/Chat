import { chmod, mkdir, open, rename, stat } from "node:fs/promises";
import { dirname } from "node:path";

const MAX_BODY = 16 * 1024;

async function exists(path) {
  try {
    await stat(path);
    return true;
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

async function body(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY) throw new Error("invalid_body");
    chunks.push(chunk);
  }
  if (!chunks.length) throw new Error("invalid_body");
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function valid(value) {
  return typeof value === "string" && value.length > 0 && value.length <= 4096;
}

export function createCredentialHandoff(options = {}) {
  const path = options.path ?? process.env.ALICE_DEV_CREDENTIAL_FILE ?? "/workspace/.alice/credentials.json";
  const token = options.token ?? "";

  function authorized(request) {
    return Boolean(token) && request.headers.authorization === `Bearer ${token}`;
  }

  async function handle(request, response, url) {
    if (url.pathname !== "/bootstrap/credentials") return false;
    response.setHeader("Cache-Control", "no-store");

    if (!authorized(request)) {
      request.resume();
      response.writeHead(401, { "Content-Type": "application/json" })
        .end('{"status":"unauthorized"}');
      return true;
    }

    if (request.method === "GET") {
      response.writeHead(200, { "Content-Type": "application/json" })
        .end(JSON.stringify({ provisioned: await exists(path) }));
      return true;
    }

    if (request.method !== "POST") {
      request.resume();
      response.writeHead(405, { "Content-Type": "application/json", Allow: "GET, POST" })
        .end('{"status":"method_not_allowed"}');
      return true;
    }

    if (await exists(path)) {
      request.resume();
      response.writeHead(409, { "Content-Type": "application/json" })
        .end('{"status":"already_provisioned"}');
      return true;
    }

    try {
      const payload = await body(request);
      if (
        !payload ||
        Object.keys(payload).sort().join(",") !== "login,password" ||
        !valid(payload.login) ||
        !valid(payload.password)
      ) throw new Error("invalid_body");

      await mkdir(dirname(path), { recursive: true, mode: 0o700 });
      const temporary = `${path}.tmp-${process.pid}`;
      const file = await open(temporary, "wx", 0o600);
      try {
        await file.writeFile(JSON.stringify({ login: payload.login, password: payload.password }));
        await file.sync();
      } finally {
        await file.close();
      }
      await chmod(temporary, 0o600);
      await rename(temporary, path);
      await chmod(path, 0o600);

      response.writeHead(201, { "Content-Type": "application/json" })
        .end('{"status":"provisioned"}');
    } catch {
      if (!response.headersSent) {
        response.writeHead(400, { "Content-Type": "application/json" });
      }
      if (!response.writableEnded) response.end('{"status":"invalid_request"}');
    }
    return true;
  }

  return { handle, path };
}
