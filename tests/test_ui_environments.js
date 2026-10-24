"use strict";

// Environments panel on the BrowserShim: real static/environments.js with mocked dispatcher
// responses. The panel is opened through its header button, as a user does.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

function environment(overrides = {}) {
  return {
    environment_id: "env-1",
    branch_name: "feature/x",
    commit_sha: "0123456789abcdef",
    status: "STOPPED",
    data_namespace: "ns-1",
    url: "https://env-1.example.com",
    ...overrides,
  };
}

function boot({ environments = [environment()], operationStatus = 200 } = {}) {
  const requests = [];
  const dispatcher = {
    request: (url, options = {}) => {
      const method = options.method || "GET";
      requests.push({ url, method });
      if (url === "/api/environments" && method === "GET") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ environments: environments.map((item) => ({ ...item })) }),
        });
      }
      if (url.startsWith("/api/environments/")) {
        return new Promise((resolve) => {
          setTimeout(
            () => resolve({ ok: operationStatus < 300, status: operationStatus, json: async () => ({}) }),
            0,
          );
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
    },
  };
  const shim = new BrowserShim('<body><div id="header-actions-2"></div></body>');
  const { window, document } = shim.load(["static/core_api.js", "static/environments.js"], {
    LogosDispatcher: dispatcher,
    confirm: () => true,
  });
  window.LogosCoreAPI.ui.events.mountClicks(document.body);
  return { window, document, requests };
}

async function settle() {
  for (let i = 0; i < 12; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

async function openPanel(document) {
  document.getElementById("environments-btn").click();
  await settle();
}

test("only web addresses become links; a javascript: address from the server does not", async () => {
  const { document } = boot({
    environments: [environment({ url: "javascript:alert(1)" })],
  });
  await openPanel(document);

  const link = document.querySelector(".environment-card a");
  assert.equal(link.getAttribute("href"), "#");
});

test("a web address is kept as the link target", async () => {
  const { document } = boot();
  await openPanel(document);

  assert.equal(
    document.querySelector(".environment-card a").getAttribute("href"),
    "https://env-1.example.com",
  );
});

test("a second click on a running operation does not send it twice", async () => {
  const { document, requests } = boot();
  await openPanel(document);

  const start = document.querySelector('button[data-op="start"]');
  start.click();
  start.click();
  await settle();

  const operations = requests.filter((request) => request.url === "/api/environments/env-1/start");
  assert.equal(operations.length, 1);
});

test("a failed operation re-enables its button so it can be retried", async () => {
  const { document } = boot({ operationStatus: 500 });
  await openPanel(document);

  const start = document.querySelector('button[data-op="start"]');
  start.click();
  await settle();

  assert.equal(start.disabled, false);
});
