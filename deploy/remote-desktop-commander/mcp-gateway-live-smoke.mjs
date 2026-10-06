import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const endpoint = new URL(process.env.RDC_MCP_SMOKE_URL || "http://127.0.0.1:8766/mcp");
const token = process.env.RDC_MCP_SMOKE_TOKEN || "local-smoke-token";

const denied = await fetch(endpoint, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: {
      protocolVersion: "2025-03-26",
      capabilities: {},
      clientInfo: { name: "denied", version: "1" },
    },
  }),
});
if (denied.status !== 401) throw new Error(`expected 401, got ${denied.status}`);

const client = new Client({ name: "gateway-live-smoke", version: "1" }, { capabilities: {} });
const transport = new StreamableHTTPClientTransport(endpoint, {
  requestInit: { headers: { Authorization: `Bearer ${token}` } },
});
await client.connect(transport);

const tools = await client.listTools();
if (!tools.tools.some((tool) => tool.name === "get_config")) {
  throw new Error("get_config missing");
}
if (tools.tools.some((tool) => tool.name === "write_pdf")) {
  throw new Error("write_pdf must be hidden");
}
const result = await client.callTool({ name: "get_config", arguments: {} });
if (!Array.isArray(result.content)) throw new Error("invalid tool result");

console.log(`mcp-gateway-live-ok tools=${tools.tools.length}`);
await client.close();
