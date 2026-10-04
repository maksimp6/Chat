"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const { BrowserShim } = require("./browser_dom");

const ROOT = path.resolve(__dirname, "..");

async function settle() {
  for (let i = 0; i < 10; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

function boot(chatResponse) {
  const browser = new BrowserShim(
    '<div id="chatbox"></div><textarea id="msg-input"></textarea><button id="send-btn"></button>',
  );
  const requests = [];
  const dispatcher = {
    request: async (url, init) => {
      requests.push({ url, body: init && init.body ? JSON.parse(init.body) : null });
      const body = url === "/api/chat" ? chatResponse : { reply: "Готово" };
      return { ok: true, status: 200, json: async () => body };
    },
  };
  const { document } = browser.load([path.join(ROOT, "static", "chat.js")], {
    AliceDispatcher: dispatcher,
    performance: { now: () => 0 },
    currentConvId: "conv-1",
    currentModel: "test-model",
    conversations: [],
  });
  return { document, requests };
}

async function send(document, text) {
  document.getElementById("msg-input").value = text;
  document.getElementById("send-btn").click();
  await settle();
}

async function testSendShowsApprovalCard() {
  const { document, requests } = boot({
    requires_approval: true,
    tool_call: {
      name: "delete_file",
      description: "Удалить файл",
      arguments: { path: "notes.txt" },
      call_id: "call-1",
    },
    original_message: "Удали notes.txt",
  });
  await send(document, "Удали notes.txt");

  const card = document.querySelector(".approval-card");
  assert.ok(card, "approval card must be rendered for requires_approval replies");
  assert.match(card.textContent, /Требуется подтверждение действия/);
  assert.match(card.textContent, /Удалить файл/);
  assert.match(card.textContent, /notes\.txt/);
  assert.doesNotMatch(document.getElementById("chatbox").textContent, /Ошибка HTTP/);

  card.querySelector(".approval-btn-approve").click();
  await settle();
  const approved = requests.find((r) => r.url === "/api/mcp/execute-approved");
  assert.deepEqual(approved.body, {
    conversation_id: "conv-1",
    model: "test-model",
    name: "delete_file",
    arguments: { path: "notes.txt" },
    original_message: "Удали notes.txt",
  });
  assert.match(document.getElementById("chatbox").textContent, /Готово/);
}

async function testOriginalMessageFallsBackToTypedText() {
  const { document, requests } = boot({
    requires_approval: true,
    tool_call: { name: "restart", arguments: {} },
  });
  await send(document, "Перезапусти");
  document.querySelector(".approval-btn-approve").click();
  await settle();
  const approved = requests.find((r) => r.url === "/api/mcp/execute-approved");
  assert.equal(approved.body.original_message, "Перезапусти");
}

async function main() {
  await testSendShowsApprovalCard();
  await testOriginalMessageFallsBackToTypedText();
  console.log("chat send approval tests passed");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
