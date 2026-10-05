import { randomUUID } from "node:crypto";
import { createConnection } from "@playwright/mcp";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { isInitializeRequest } from "@modelcontextprotocol/sdk/types.js";

// Authentication is checked by the worker before every request reaches this handler.
export function createPlaywrightMcp(contextGetter, outputDir) {
  const sessions = new Map();
  let boundSessionId;

  async function handle(request, response, message) {
    const sessionId = request.headers["mcp-session-id"];
    let session = typeof sessionId === "string" ? sessions.get(sessionId) : undefined;
    if (!session) {
      if (sessionId || request.method !== "POST" || !isInitializeRequest(message)) {
        response.writeHead(sessionId ? 404 : 400, { "content-type": "application/json" });
        response.end(JSON.stringify({ jsonrpc: "2.0", id: message?.id ?? null, error: { code: -32000, message: "Initialize an MCP session first" } }));
        return;
      }
      if (boundSessionId) {
        response.writeHead(409, { "content-type": "application/json", "cache-control": "no-store" });
        response.end(JSON.stringify({ jsonrpc: "2.0", id: message?.id ?? null, error: { code: -32001, message: "MCP client already bound" } }));
        return;
      }
      const server = await createConnection({ saveSession: false, outputDir }, contextGetter);
      const transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: randomUUID,
        enableJsonResponse: true,
        onsessioninitialized: (id) => {
          boundSessionId = id;
          sessions.set(id, { server, transport });
        },
      });
      await server.connect(transport);
      const onclose = transport.onclose;
      transport.onclose = () => {
        sessions.delete(transport.sessionId);
        if (boundSessionId === transport.sessionId) boundSessionId = undefined;
        onclose?.();
      };
      session = { server, transport };
    }
    await session.transport.handleRequest(request, response, message);
  }

  async function close() {
    await Promise.all([...sessions.values()].map(({ server }) => server.close()));
    sessions.clear();
    boundSessionId = undefined;
  }

  return { handle, close };
}
