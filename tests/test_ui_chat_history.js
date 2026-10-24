"use strict";

// Chat history, usage display, and the model picker on the BrowserShim:
// real static/core_api.js and static/chat.js, mocked dispatcher responses.

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

// Each request stays pending until the test resolves it, so responses can arrive out of order.
function deferredBoot({ conversation = "c1" } = {}) {
  const pending = new Map();
  const dispatcher = {
    request: (url) =>
      new Promise((resolve) => {
        pending.set(url, (status, body) =>
          resolve({ ok: status < 300, status, json: async () => body }),
        );
      }),
  };
  const shim = new BrowserShim(HTML);
  const { document, context } = shim.load(["static/core_api.js", "static/chat.js"], {
    LogosDispatcher: dispatcher,
    currentConvId: conversation,
    currentModel: "logos-runtime",
  });
  return { document, context, pending };
}

function instantBoot({ conversation = "c1", responses = [], extra = {} } = {}) {
  const queue = [...responses];
  const dispatcher = {
    request: () => {
      const next = queue.shift();
      if (!next) return reply(500, { error: "no mocked response" });
      return reply(next.status || 200, next.body);
    },
  };
  const shim = new BrowserShim(HTML);
  const { document, context } = shim.load(["static/core_api.js", "static/chat.js"], {
    LogosDispatcher: dispatcher,
    currentConvId: conversation,
    currentModel: "logos-runtime",
    ...extra,
  });
  return { document, context };
}

async function settle() {
  for (let i = 0; i < 10; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

function renderedText(document) {
  return Array.from(document.querySelectorAll(".msg")).map((node) => node.textContent);
}

test("a failed history request shows an error, not the empty-conversation state", async () => {
  const { document, context } = instantBoot({
    responses: [{ status: 500, body: { error: "boom" } }],
  });
  context.loadHistory("c1");
  await settle();

  const states = Array.from(document.querySelectorAll(".empty-state"));
  assert.equal(
    states.some((node) => node.textContent === "Начните диалог"),
    false,
  );
  assert.equal(document.querySelector(".error-state").textContent, "Ошибка сети: HTTP 500");
});

test("a slow response for a conversation the user has left does not overwrite the current one", async () => {
  const { document, context, pending } = deferredBoot({ conversation: "c1" });
  context.loadHistory("c1");
  context.currentConvId = "c2";
  context.loadHistory("c2");

  pending.get("/api/conversations/c2/messages")(200, {
    messages: [{ id: "m2", role: "assistant", text: "Ответ второго диалога" }],
  });
  await settle();
  pending.get("/api/conversations/c1/messages")(200, {
    messages: [{ id: "m1", role: "assistant", text: "Ответ первого диалога" }],
  });
  await settle();

  const texts = renderedText(document).join("\n");
  assert.match(texts, /Ответ второго диалога/);
  assert.doesNotMatch(texts, /Ответ первого диалога/);
});

test("usage without an output count shows zero instead of 'undefined'", async () => {
  const { document, context } = instantBoot();
  context.addMessage("ответ", "bot", false, 0, null, null, null, {
    total_tokens: 5,
    input_tokens: 3,
  });

  const summary = document.querySelector(".msg-meta-summary").textContent;
  assert.equal(summary, "📊 Токены: 5 (вход: 3, выход: 0)");
});

test("opening the model picker for a new chat sets the modal's open state", async () => {
  const { document, context } = instantBoot({ conversation: null });
  document.getElementById("msg-input").value = "привет";
  document.getElementById("send-btn").click();
  await settle();

  const modal = document.getElementById("model-modal");
  assert.equal(modal.classList.contains("visible"), true);
  assert.equal(modal.hidden, false);
  assert.equal(modal.getAttribute("aria-hidden"), "false");
});

test("a denied clipboard shows a failure on the copy button instead of an unhandled rejection", async () => {
  const { document, context } = instantBoot({
    extra: {
      navigator: { clipboard: { writeText: () => Promise.reject(new Error("denied")) } },
      AbortSignal,
    },
  });
  context.addMessage("ответ", "bot", false, 0);
  document.querySelector(".copy-btn").click();
  await settle();

  assert.equal(document.querySelector(".copy-btn").textContent, "⚠️");
});
