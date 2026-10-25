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
  const alerts = [];
  let context;
  const requests = [];
  const queue = [...responses];
  const dispatcher = {
    request: (url, options = {}, dispatchOptions = {}) => {
      const body = options.body ? JSON.parse(options.body) : null;
      requests.push({ url, body, timeoutMs: dispatchOptions.timeoutMs });
      const next = queue.shift();
      if (!next) return reply(500, { error: "no mocked response for " + url });
      if (next && typeof next.then === "function") return next;
      return reply(next.status || 200, next.body);
    },
  };
  const shim = new BrowserShim(HTML);
  const loaded = shim.load(["static/core_api.js", "static/models.js", "static/chat.js"], {
    LogosDispatcher: dispatcher,
    currentConvId: conversation,
    currentModel: "yandexgpt-5-lite",
    conversations: [],
    alert: (message) => alerts.push(message),
    selectConv: (id) => {
      context.currentConvId = id;
      return context.loadHistory(id);
    },
  });
  context = loaded.context;
  return { document: loaded.document, context, requests, alerts };

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

test("Send creates a conversation, finishes initial history, then sends and renders", async () => {
  const { document, requests, context } = boot({ conversation: null, responses: [
    { status: 201, body: { id: "created", title: "Новый чат" } },
    { body: { messages: [] } },
    { body: { reply: "готово" } },
  ] });
  document.getElementById("msg-input").value = "привет";
  document.getElementById("send-btn").click();
  await settle();
  assert.deepEqual(requests.map((r) => r.url), ["/api/conversations", "/api/conversations/created/messages", "/api/chat"]);
  assert.equal(requests[2].body.conversation_id, "created");
  assert.equal(context.currentConvId, "created");
  assert.ok(rendered(document).some((text) => text.includes("привет")));
  assert.ok(rendered(document).some((text) => text.includes("готово")));
});

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function result(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

test("rapid Send during creation submits the original text/model/settings and keeps the next draft", async () => {
  const creating = deferred();
  const { document, context, requests } = boot({ conversation: null, responses: [
    creating.promise, { body: { messages: [] } }, { body: { reply: "готово" } },
  ] });
  const settings = { reasoning: { effort: "low" } };
  context.window.getResponsesParams = () => settings;
  const input = document.getElementById("msg-input");
  input.value = "original";
  document.getElementById("send-btn").click();
  document.getElementById("send-btn").click();
  assert.equal(requests.length, 1);
  input.value = "next draft";
  context.currentModel = "different-model";
  settings.reasoning.effort = "high";
  creating.resolve(result(201, { id: "created" }));
  await settle();
  assert.equal(requests.filter((r) => r.url === "/api/conversations").length, 1);
  assert.equal(requests[2].body.message, "original");
  assert.equal(requests[2].body.model, "yandexgpt-5-lite");
  assert.equal(requests[2].body.params.reasoning.effort, "low");
  assert.equal(input.value, "next draft");
});

for (const [status, body] of [[400, { title: "invalid request" }], [502, { detail: "provider unavailable" }], [200, {}]]) {
  test("failed creation does not send or insert an invalid conversation: " + status, async () => {
    const { document, context, requests, alerts } = boot({ conversation: null, responses: [{ status, body }] });
    document.getElementById("msg-input").value = "keep me";
    document.getElementById("send-btn").click();
    await settle();
    assert.equal(requests.length, 1);
    assert.equal(context.currentConvId, null);
    assert.equal(context.conversations.length, 0);
    assert.equal(document.getElementById("msg-input").value, "keep me");
    assert.equal(alerts.length, 1);
  });
}

test("switching chats during creation preserves selection and draft while submitting to the created chat", async () => {
  const creating = deferred();
  const { document, context, requests } = boot({ conversation: null, responses: [creating.promise, { body: { reply: "background reply" } }] });
  document.getElementById("msg-input").value = "original";
  document.getElementById("send-btn").click();
  context.currentConvId = "other";
  document.getElementById("msg-input").value = "other draft";
  creating.resolve(result(201, { id: "created" }));
  await settle();
  assert.equal(context.currentConvId, "other");
  assert.equal(document.getElementById("msg-input").value, "other draft");
  assert.equal(requests[1].body.conversation_id, "created");
  assert.equal(requests[1].body.message, "original");
  assert.equal(rendered(document).length, 0);
});

test("late reply updates only its conversation and is not painted into the selected other chat", async () => {
  const pending = deferred();
  const { document, context } = boot({ responses: [pending.promise] });
  context.conversations = [{ id: "c1", title: "first" }, { id: "c2", title: "second" }];
  document.getElementById("msg-input").value = "first question";
  document.getElementById("send-btn").click();
  context.currentConvId = "c2";
  document.getElementById("chatbox").replaceChildren();
  document.getElementById("msg-input").value = "second draft";
  pending.resolve(result(200, { reply: "first reply", title: "first renamed" }));
  await settle();
  assert.equal(context.conversations[0].title, "first renamed");
  assert.equal(context.conversations[1].title, "second");
  assert.equal(rendered(document).length, 0);
  assert.equal(document.getElementById("msg-input").value, "second draft");
});

test("a second send in the same conversation waits for the first request and keeps its draft", async () => {
  const pending = deferred();
  const { document, requests } = boot({ responses: [pending.promise, { body: { reply: "second reply" } }] });
  const input = document.getElementById("msg-input");
  input.value = "first";
  document.getElementById("send-btn").click();
  input.value = "second";
  document.getElementById("send-btn").click();
  assert.equal(requests.length, 1);
  assert.equal(input.value, "second");
  pending.resolve(result(200, { reply: "first reply" }));
  await settle();
  document.getElementById("send-btn").click();
  await settle();
  assert.equal(requests.length, 2);
  assert.equal(requests[1].body.message, "second");
  assert.equal(requests[1].timeoutMs, 300000);
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
