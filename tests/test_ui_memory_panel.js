"use strict";

// Memory management panel on the BrowserShim: real static/memory_panel.js,
// mocked dispatcher responses.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

const PANEL_HTML =
  '<div id="memoryModal" class="modal memory-modal" hidden>' +
  '<input type="checkbox" id="memEnabled" />' +
  '<input type="number" id="memLimit" />' +
  '<button id="memoryClearBtn" type="button">Очистить всю память</button>' +
  '<span id="memCount">0</span>' +
  '<p id="memoryStatus" role="status"></p>' +
  '<div id="memoryFactsList"></div>' +
  "</div>";

function reply(status, body) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

function boot({ responses = [], confirmAnswer = true } = {}) {
  const requests = [];
  const confirms = [];
  const queue = [...responses];
  const dispatcher = {
    request: (url, options = {}) => {
      const body = options.body ? JSON.parse(options.body) : null;
      requests.push({ url, method: options.method || "GET", body });
      const next = queue.shift();
      if (!next) return reply(500, { error: "no mocked response for " + url });
      return reply(next.status || 200, next.body);
    },
  };
  const shim = new BrowserShim(PANEL_HTML);
  const { document, window } = shim.load(["static/memory_panel.js"], {
    LogosDispatcher: dispatcher,
    confirm: (message) => {
      confirms.push(message);
      return confirmAnswer;
    },
  });
  return { document, window, requests, confirms };
}

async function settle() {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

function openMemoryModal(document) {
  document.dispatchEvent({
    type: "logos:modal:before-open",
    detail: { modalId: "memoryModal", modal: document.getElementById("memoryModal") },
  });
}

function statusText(document) {
  return document.getElementById("memoryStatus").textContent;
}

function change(document, id) {
  document.getElementById(id).dispatchEvent({ type: "change" });
}

const MANAGE = {
  config: { enabled: true, max_context_facts: 15 },
  facts: [{ category: "profile", fact: "Живёт в Москве" }],
};

test("opening the memory modal shows the saved config and facts", async () => {
  const { document, requests } = boot({ responses: [{ body: MANAGE }] });
  openMemoryModal(document);
  await settle();

  assert.equal(requests[0].url, "/api/memory/manage");
  assert.equal(document.getElementById("memEnabled").checked, true);
  assert.equal(document.getElementById("memLimit").value, "15");
  assert.equal(document.getElementById("memCount").textContent, "1");
  assert.equal(
    document.querySelector(".memory-fact").textContent,
    "[profile] Живёт в Москве",
  );
});

test("a stored limit of zero is shown as zero, not replaced by the default", async () => {
  const { document } = boot({
    responses: [{ body: { config: { enabled: true, max_context_facts: 0 }, facts: [] } }],
  });
  openMemoryModal(document);
  await settle();

  assert.equal(document.getElementById("memLimit").value, "0");
});

test("an empty memory says so instead of showing an empty list", async () => {
  const { document } = boot({
    responses: [{ body: { config: { enabled: true, max_context_facts: 15 }, facts: [] } }],
  });
  openMemoryModal(document);
  await settle();

  assert.equal(document.getElementById("memCount").textContent, "0");
  assert.equal(document.querySelector(".memory-empty").textContent, "Память пуста.");
});

test("changing the limit saves exactly what was typed, including zero", async () => {
  const { document, requests } = boot({
    responses: [{ body: MANAGE }, { body: { status: "ok" } }, { body: MANAGE }],
  });
  openMemoryModal(document);
  await settle();

  document.getElementById("memLimit").value = "0";
  change(document, "memLimit");
  await settle();

  const put = requests.find((request) => request.method === "PUT");
  assert.equal(put.url, "/api/memory/config");
  assert.deepEqual(put.body, { enabled: true, max_context_facts: 0 });
});

test("a failed save reports the error and resyncs the controls to the server state", async () => {
  const { document, requests } = boot({
    responses: [
      { body: MANAGE },
      { status: 400, body: { error: "max_context_facts must be an integer" } },
      { body: { config: { enabled: false, max_context_facts: 15 }, facts: [] } },
    ],
  });
  openMemoryModal(document);
  await settle();

  document.getElementById("memEnabled").checked = false;
  change(document, "memEnabled");
  await settle();

  assert.equal(requests[requests.length - 1].url, "/api/memory/manage");
  assert.equal(document.getElementById("memEnabled").checked, false);
  assert.equal(statusText(document), "Не удалось сохранить настройки памяти.");
});

test("a failed load says so instead of leaving the panel silently stale", async () => {
  const { document } = boot({ responses: [{ status: 500, body: { error: "boom" } }] });
  openMemoryModal(document);
  await settle();

  assert.equal(statusText(document), "Не удалось загрузить память.");
});

test("clear-all asks first and sends nothing when the user declines", async () => {
  const { document, requests, confirms } = boot({ confirmAnswer: false });
  document.getElementById("memoryClearBtn").click();
  await settle();

  assert.equal(confirms.length, 1);
  assert.deepEqual(requests, []);
});

test("clear-all posts a category-less clear and reloads the panel", async () => {
  const { document, requests } = boot({
    responses: [{ body: { status: "cleared", category: "all" } }, { body: MANAGE }],
  });
  document.getElementById("memoryClearBtn").click();
  await settle();

  assert.equal(requests[0].url, "/api/memory/clear");
  assert.equal(requests[0].method, "POST");
  assert.deepEqual(requests[0].body, { category: null });
  assert.equal(requests[1].url, "/api/memory/manage");
});

test("a failed clear-all reports the error", async () => {
  const { document } = boot({
    responses: [{ status: 500, body: { error: "boom" } }, { body: MANAGE }],
  });
  document.getElementById("memoryClearBtn").click();
  await settle();

  assert.equal(statusText(document), "Не удалось очистить память.");
});
