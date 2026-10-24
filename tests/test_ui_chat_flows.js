"use strict";

// Chat UI flows on the BrowserShim: real static/chat.js, mocked HTTP responses.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

const HTML =
  '<div id="chatbox"></div><textarea id="msg-input"></textarea>' +
  '<button id="send-btn" type="button"></button>' +
  '<div id="model-modal" class="modal" hidden aria-hidden="true"></div>';

function reply(status, body) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

function boot({ conversation = "c1", responses = [] } = {}) {
  const requests = [];
  const queue = [...responses];
  const dispatcher = {
    request: (url, options = {}) => {
      const body = options.body ? JSON.parse(options.body) : null;
      requests.push({ url, body });
      const next = queue.shift();
      if (!next) return reply(500, { error: "no mocked response for " + url });
      return reply(next.status || 200, next.body);
    },
  };
  const shim = new BrowserShim(HTML);
  const { document, context } = shim.load(["static/core_api.js", "static/chat.js"], {
    LogosDispatcher: dispatcher,
    currentConvId: conversation,
    currentModel: "logos-runtime",
  });
  return { document, context, requests };
}

async function settle() {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

function rendered(document) {
  return Array.from(document.querySelectorAll(".msg")).map((node) => node.textContent.trim());
}

test("sending a message posts it to the conversation and renders the reply", async () => {
  const { document, requests } = boot({
    responses: [{ body: { reply: "ответ модели", cost: 0 } }],
  });
  document.getElementById("msg-input").value = "привет";
  document.getElementById("send-btn").click();
  await settle();

  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/chat");
  assert.deepEqual(
    { message: requests[0].body.message, conversation_id: requests[0].body.conversation_id },
    { message: "привет", conversation_id: "c1" },
  );
  assert.equal(document.getElementById("msg-input").value, "");
  assert.ok(rendered(document).some((text) => text.includes("привет")));
  assert.ok(rendered(document).some((text) => text.includes("ответ модели")));
});

test("a message without a conversation opens the model picker and sends nothing", async () => {
  const { document, requests } = boot({ conversation: null });
  document.getElementById("msg-input").value = "привет";
  document.getElementById("send-btn").click();
  await settle();

  assert.equal(requests.length, 0);
  assert.equal(document.getElementById("model-modal").classList.contains("visible"), true);
});

test("a server error is shown to the user instead of a silent failure", async () => {
  const { document } = boot({
    responses: [{ status: 502, body: { error: "provider unavailable" } }],
  });
  document.getElementById("msg-input").value = "привет";
  document.getElementById("send-btn").click();
  await settle();

  assert.ok(rendered(document).some((text) => text.includes("provider unavailable")));
});

test("approving a tool call sends the server-issued approval token", async () => {
  const toolCall = {
    name: "run_command",
    arguments: { command: "echo hi" },
    approval_token: "token-from-server",
  };
  const { document, requests } = boot({
    responses: [
      { body: { requires_approval: true, tool_call: toolCall, original_message: "запусти" } },
      { body: { reply: "готово", cost: 0 } },
    ],
  });
  document.getElementById("msg-input").value = "запусти";
  document.getElementById("send-btn").click();
  await settle();

  const approve = document.querySelector(".approval-btn-approve");
  assert.ok(approve, "approval card must offer an approve button");
  approve.click();
  await settle();

  const execute = requests.find((request) => request.url === "/api/mcp/execute-approved");
  assert.ok(execute, "approve must call the approval endpoint");
  assert.deepEqual(
    {
      name: execute.body.name,
      arguments: execute.body.arguments,
      approval_token: execute.body.approval_token,
      conversation_id: execute.body.conversation_id,
    },
    {
      name: "run_command",
      arguments: { command: "echo hi" },
      approval_token: "token-from-server",
      conversation_id: "c1",
    },
  );
  assert.ok(rendered(document).some((text) => text.includes("готово")));
});

test("rejecting a tool call never reaches the approval endpoint", async () => {
  const { document, requests } = boot({
    responses: [
      {
        body: {
          requires_approval: true,
          tool_call: { name: "run_command", arguments: {}, approval_token: "t" },
          original_message: "запусти",
        },
      },
    ],
  });
  document.getElementById("msg-input").value = "запусти";
  document.getElementById("send-btn").click();
  await settle();

  document.querySelector(".approval-btn-reject").click();
  await settle();

  assert.equal(requests.filter((r) => r.url === "/api/mcp/execute-approved").length, 0);
  assert.ok(rendered(document).some((text) => text.includes("отклонено")));
});
