"use strict";

// Conversation sidebar flows on the BrowserShim: real static/sidebar.js, mocked HTTP.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

const HTML =
  '<button id="menu-btn" type="button"></button><div id="sidebar">' +
  '<button id="close-sidebar-btn" type="button"></button>' +
  '<button id="new-chat-btn" type="button"></button>' +
  '<div id="conv-list"></div></div><div id="overlay"></div>' +
  '<div id="chatbox"></div><div id="model-modal" class="modal" hidden aria-hidden="true"></div>';

function reply(status, body) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: async () => body });
}

function boot({ conversations, currentConvId, responses = [], prompts = [] }) {
  const requests = [];
  const loaded = [];
  const queue = [...responses];
  const shim = new BrowserShim(HTML);
  const extra = {
    conversations,
    currentConvId,
    LogosDispatcher: {
      request: (url, options = {}) => {
        requests.push({ url, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : null });
        const next = queue.shift();
        return reply(next ? next.status || 200 : 500, next ? next.body : { error: "unmocked" });
      },
    },
    loadHistory: (id) => loaded.push(id),
    confirm: () => true,
    prompt: () => prompts.shift() || null,
    alert: () => {},
  };
  const { document, window, context } = shim.load(["static/core_api.js", "static/sidebar.js"], extra);
  return { document, window, context, requests, loaded };
}

async function settle() {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

function items(document) {
  return Array.from(document.querySelectorAll(".conv-item")).map((node) => ({
    title: node.querySelector(".conv-title").textContent,
    active: node.className.includes("active"),
  }));
}

const CONVERSATIONS = [
  { id: "c1", title: "Первый", model: "logos-runtime" },
  { id: "c2", title: "Второй", model: "logos-runtime" },
];

test("sidebar lists conversations and marks the current one active", () => {
  const { document } = boot({ conversations: CONVERSATIONS, currentConvId: "c2" });
  assert.deepEqual(items(document), [
    { title: "Первый", active: false },
    { title: "Второй", active: true },
  ]);
});

test("an empty conversation list shows the empty state", () => {
  const { document } = boot({ conversations: [], currentConvId: null });
  assert.equal(document.querySelector(".conv-empty").textContent, "Нет диалогов");
});

test("selecting a conversation loads its history", () => {
  const { document, context, loaded } = boot({ conversations: CONVERSATIONS, currentConvId: "c2" });
  document.querySelectorAll(".conv-link")[0].click();
  assert.deepEqual(loaded, ["c1"]);
  assert.equal(context.currentConvId, "c1");
});

test("the second click of a double-click does not select the conversation again", () => {
  const { document, loaded } = boot({ conversations: CONVERSATIONS, currentConvId: "c2" });
  const link = document.querySelectorAll(".conv-link")[0];
  link.dispatchEvent({ type: "click", bubbles: true, detail: 1, preventDefault() {} });
  link.dispatchEvent({ type: "click", bubbles: true, detail: 2, preventDefault() {} });
  assert.deepEqual(loaded, ["c1"]);
});

test("the delete button names the conversation it deletes", () => {
  const { document } = boot({ conversations: CONVERSATIONS, currentConvId: "c2" });
  assert.equal(document.querySelector(".delete-btn").getAttribute("aria-label"), "Удалить диалог");
});

test("deleting the current conversation removes it and selects the next one", async () => {
  const { document, context, requests, loaded } = boot({
    conversations: [...CONVERSATIONS],
    currentConvId: "c1",
    responses: [{ body: { status: "deleted" } }],
  });
  document.querySelector(".delete-btn").click();
  await settle();

  assert.deepEqual(
    requests.map((r) => [r.method, r.url]),
    [["DELETE", "/api/conversations/c1"]],
  );
  assert.deepEqual(
    context.conversations.map((c) => c.id),
    ["c2"],
  );
  assert.equal(context.currentConvId, "c2");
  assert.deepEqual(loaded, ["c2"]);
  assert.deepEqual(items(document), [{ title: "Второй", active: true }]);
});

test("renaming a conversation patches its title and updates the list", async () => {
  const { document, context, requests } = boot({
    conversations: [...CONVERSATIONS],
    currentConvId: "c1",
    responses: [{ body: { status: "ok", title: "Новое имя" } }],
    prompts: ["Новое имя"],
  });
  document.querySelector(".conv-title").dispatchEvent({ type: "dblclick", preventDefault() {}, stopPropagation() {} });
  await settle();

  assert.deepEqual(requests[0], {
    url: "/api/conversations/c1",
    method: "PATCH",
    body: { title: "Новое имя" },
  });
  assert.equal(context.conversations[0].title, "Новое имя");
  assert.deepEqual(items(document)[0], { title: "Новое имя", active: true });
});

test("new chat opens the model picker", () => {
  const { document, window } = boot({ conversations: CONVERSATIONS, currentConvId: "c1" });
  document.getElementById("new-chat-btn").click();
  assert.equal(window.isCreatingNewChat, true);
  assert.equal(document.getElementById("model-modal").classList.contains("visible"), true);
});
