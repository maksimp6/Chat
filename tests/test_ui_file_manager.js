"use strict";

// File manager on the BrowserShim: real static/file_manager.js with mocked dispatcher responses
// routed by method and URL.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

const STORES = { data: [{ id: "vs_1", name: "Docs", file_counts: { completed: 0 } }] };
const FILES = { data: [{ id: "f1", filename: "notes.txt", bytes: 10 }] };

function boot({ routes = {}, extra = {} } = {}) {
  const requests = [];
  const alerts = [];
  const dispatcher = {
    request: (url, options = {}) => {
      const method = options.method || "GET";
      const body = options.body ? JSON.parse(options.body) : null;
      requests.push({ url, method, body });
      const route = routes[method + " " + url] || routes[url];
      if (!route) return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
      if (route.jsonThrows) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => {
            throw new Error(route.jsonThrows);
          },
        });
      }
      const status = route.status || 200;
      return Promise.resolve({ ok: status < 300, status, json: async () => route.body });
    },
  };
  const shim = new BrowserShim("<body></body>");
  const { window, document } = shim.load(
    ["static/core_api.js", "static/settings/ui_helpers.js", "static/file_manager.js"],
    {
      LogosDispatcher: dispatcher,
      confirm: () => true,
      alert: (message) => alerts.push(message),
      AbortSignal,
      ...extra,
    },
  );
  window.LogosCoreAPI.ui.events.mountClicks(document.body);
  return { window, document, requests, alerts };
}

async function settle() {
  for (let i = 0; i < 12; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

const DEFAULT_ROUTES = {
  "/api/vector-stores": { body: STORES },
  "/api/files": { body: FILES },
};

test("adding files to a vector store opens the picker, and confirming closes it", async () => {
  const { window, document, requests } = boot({ routes: DEFAULT_ROUTES });
  window.openFileManagerModal();
  await settle();

  document.querySelector(".vs-add-files-btn").click();
  await settle();

  assert.equal(document.getElementById("vs-add-files-modal").hidden, false);
  const checkbox = document.querySelector(".vs-add-file-chk");
  checkbox.checked = true;
  document.getElementById("vs-add-confirm").click();
  await settle();

  const post = requests.find((request) => request.method === "POST");
  assert.equal(post.url, "/api/vector-stores/vs_1/files");
  assert.deepEqual(post.body, { file_ids: ["f1"] });
  assert.equal(document.getElementById("vs-add-files-modal").hidden, true);
});

test("a rejected vector store creation is reported and keeps the typed name", async () => {
  const { window, document, alerts } = boot({
    routes: {
      ...DEFAULT_ROUTES,
      "POST /api/vector-stores": { status: 500, body: { error: "boom" } },
    },
  });
  window.openFileManagerModal();
  await settle();

  document.getElementById("vs-name-input").value = "Новое";
  document.getElementById("btn-create-vs").click();
  await settle();

  assert.equal(alerts.length, 1);
  assert.match(alerts[0], /HTTP 500/);
  assert.equal(document.getElementById("vs-name-input").value, "Новое");
});

test("a denied clipboard says the copy failed instead of 'copied'", async () => {
  const { window, document } = boot({
    routes: DEFAULT_ROUTES,
    extra: { navigator: { clipboard: { writeText: () => Promise.reject(new Error("denied")) } } },
  });
  window.openFileManagerModal();
  await settle();

  document.querySelector(".vs-id-copy").click();
  await settle();

  assert.equal(document.querySelector(".vs-id-copy").textContent, "не скопировано");
});

test("an error message from the server is shown as text, not as markup", async () => {
  const { window, document } = boot({
    routes: {
      ...DEFAULT_ROUTES,
      "/api/files": { jsonThrows: "<img src=x onerror=alert(1)>" },
    },
  });
  window.openFileManagerModal();
  await settle();

  const list = document.getElementById("file-list");
  assert.equal(list.querySelector("img"), null);
  assert.match(list.textContent, /<img src=x/);
});
