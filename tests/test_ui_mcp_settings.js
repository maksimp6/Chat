"use strict";

// MCP server manager on the BrowserShim: real static/settings/settings_mcp.js, mocked dispatcher
// responses routed by method and URL.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

const SERVER = {
  id: "s1",
  name: "Git",
  server_url: "https://mcp.example.com/sse",
  server_label: "git",
  require_approval: "always",
  has_authorization: true,
  has_headers: true,
};

function boot({ deleteStatus = 200 } = {}) {
  const requests = [];
  const alerts = [];
  const dispatcher = {
    request: (url, options = {}) => {
      const method = options.method || "GET";
      requests.push({ url, method, body: options.body ? JSON.parse(options.body) : null });
      if (method === "GET") {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ data: [SERVER] }) });
      }
      const status = method === "DELETE" ? deleteStatus : 200;
      return Promise.resolve({ ok: status < 300, status, json: async () => ({}) });
    },
  };
  const shim = new BrowserShim("<body></body>");
  const { window, document } = shim.load(["static/core_api.js", "static/settings/ui_helpers.js", "static/settings/settings_mcp.js"], {
    LogosDispatcher: dispatcher,
    confirm: () => true,
    alert: (message) => alerts.push(message),
  });
  return { window, document, requests, alerts };
}

async function settle() {
  for (let i = 0; i < 10; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

test("saving an unchanged server does not send its stored secrets back", async () => {
  const { window, document, requests } = boot();
  window.openMcpManagerModal();
  await settle();

  document.querySelector(".mcp-edit-btn").click();
  document.getElementById("mcp-ed-save").click();
  await settle();

  const put = requests.find((request) => request.method === "PUT");
  assert.equal(put.url, "/api/mcp-servers/s1");
  assert.equal("authorization" in put.body, false);
  assert.equal("headers" in put.body, false);
  assert.equal(put.body.name, "Git");
});

test("the edit form shows that a secret is saved, without its value", async () => {
  const { window, document } = boot();
  window.openMcpManagerModal();
  await settle();

  document.querySelector(".mcp-edit-btn").click();

  assert.equal(document.getElementById("mcp-ed-auth").value, "");
  assert.match(document.getElementById("mcp-ed-auth").getAttribute("placeholder"), /сохранён/);
  assert.match(document.getElementById("mcp-ed-headers").getAttribute("placeholder"), /сохранён/);
});

test("a failed delete reports the error instead of refreshing as if it worked", async () => {
  const { window, document, alerts } = boot({ deleteStatus: 500 });
  window.openMcpManagerModal();
  await settle();

  document.querySelector(".mcp-del-btn").click();
  await settle();

  assert.equal(alerts.length, 1);
  assert.match(alerts[0], /HTTP 500/);
});
