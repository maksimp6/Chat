import http from "node:http";
import process from "node:process";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";

const DEFAULT_UPSTREAM = "/opt/desktop-commander/stdio-server.mjs";
const MAX_BODY = 1024 * 1024;
const BLOCKED_TOOLS = new Set(["write_pdf"]);

function authorized(request, token) {
  if (!token) return false;
  const value = request.headers.authorization;
  return typeof value === "string" && value === `Bearer ${token}`;
}

async function readJson(request) {
  const declared = Number(request.headers["content-length"] || 0);
  if (!Number.isSafeInteger(declared) || declared < 0 || declared > MAX_BODY) {
    throw new Error("invalid_body");
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY) throw new Error("invalid_body");
    chunks.push(chunk);
  }
  if (!chunks.length) return undefined;
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

export async function startGateway(options = {}) {
  const token = options.token ?? process.env.ALICE_SHORT_TOKEN ?? "";
  if (!token) throw new Error("MCP token is required");
  const upstream = options.upstream ?? new Client(
    { name: "alice-dev-gateway", version: "0.1.0" },
    { capabilities: {} },
  );
  let transport = options.upstreamTransport;
  if (!transport) {
    transport = new StdioClientTransport({
      command: process.execPath,
      args: [process.env.ALICE_DEV_UPSTREAM || DEFAULT_UPSTREAM, "--no-onboarding"],
      cwd: process.env.ALICE_DEV_CWD || "/workspace",
      env: { ...process.env },
      stderr: "pipe",
    });
    transport.stderr?.on("data", () => {});
  }
  await upstream.connect(transport);

  const makeServer = () => {
    const server = new Server(
      { name: "alice-dev-gateway", version: "0.1.0" },
      { capabilities: { tools: {} } },
    );
    server.setRequestHandler(ListToolsRequestSchema, async (request) => {
      const listed = await upstream.listTools(request.params);
      return { ...listed, tools: listed.tools.filter((tool) => !BLOCKED_TOOLS.has(tool.name)) };
    });
    server.setRequestHandler(CallToolRequestSchema, async (request) => {
      if (BLOCKED_TOOLS.has(request.params.name)) {
        throw new Error("tool_not_available_in_alice_dev");
      }
      return upstream.callTool(request.params);
    });
    return server;
  };

  const server = http.createServer(async (request, response) => {
    response.setHeader("Cache-Control", "no-store");
    response.setHeader("Referrer-Policy", "no-referrer");
    if (request.method === "GET" && request.url === "/healthz") {
      response.writeHead(200, { "Content-Type": "application/json" })
        .end('{"status":"ok","mode":"mcp-gateway"}');
      return;
    }
    if (request.url !== "/mcp" || request.method !== "POST") {
      request.resume();
      response.writeHead(404, { "Content-Type": "application/json" })
        .end('{"status":"not_found"}');
      return;
    }
    if (!authorized(request, token)) {
      request.resume();
      response.writeHead(401, {
        "Content-Type": "application/json",
        "WWW-Authenticate": 'Bearer realm="alice-dev"',
      }).end('{"status":"unauthorized"}');
      return;
    }
    try {
      const body = await readJson(request);
      const downstream = makeServer();
      const httpTransport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
      await downstream.connect(httpTransport);
      response.on("close", () => {
        void httpTransport.close().catch(() => {});
        void downstream.close().catch(() => {});
      });
      await httpTransport.handleRequest(request, response, body);
    } catch {
      if (!response.headersSent) response.writeHead(400, { "Content-Type": "application/json" });
      if (!response.writableEnded) {
        response.end(JSON.stringify({
          jsonrpc: "2.0",
          error: { code: -32600, message: "Invalid request" },
          id: null,
        }));
      }
    }
  });

  const host = options.host ?? "0.0.0.0";
  const port = options.port ?? Number(process.env.PORT || 8080);
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, resolve);
  });
  return {
    server,
    upstream,
    close: async () => {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
      await upstream.close();
    },
  };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const gateway = await startGateway();
  const stop = async () => {
    await gateway.close().catch(() => {});
    process.exit(0);
  };
  process.on("SIGTERM", stop);
  process.on("SIGINT", stop);
}
