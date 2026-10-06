"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const { BrowserShim } = require("./browser_dom");

const ROOT = path.resolve(__dirname, "..");
const HTML =
  '<div id="chatbox"></div><textarea id="msg-input"></textarea><button id="send-btn"></button>' +
  '<div id="voice-status"></div><button id="mic-btn" type="button"></button>';

async function settle() {
  for (let i = 0; i < 10; i += 1)
    await new Promise((resolve) => setImmediate(resolve));
}

function messages(document) {
  return document
    .getElementById("chatbox")
    .querySelectorAll(".msg")
    .map((node) => ({
      role: node.className.replace("msg ", ""),
      text: node.querySelector(".bubble").textContent.trim(),
    }));
}

function boot(responses) {
  const browser = new BrowserShim(HTML);
  const requests = [];
  const storageWrites = [];
  const dispatcher = {
    request: async (url, init) => {
      requests.push({ url, init });
      const next = responses[url];
      if (next instanceof Error) throw next;
      const body = typeof next === "function" ? next(init) : next || {};
      return {
        ok: body.status ? body.status < 400 : true,
        status: body.status || 200,
        json: async () => body,
      };
    },
  };
  const loaded = browser.load([path.join(ROOT, "static", "chat.js")], {
    AliceDispatcher: dispatcher,
    performance: { now: () => 0 },
    currentConvId: "conv-1",
    currentModel: "test-model",
    conversations: [{ id: "conv-1", title: "Old" }],
    renderSidebar() {},
  });
  const originalSet = loaded.window.localStorage.setItem.bind(
    loaded.window.localStorage,
  );
  loaded.window.localStorage.setItem = (key, value) => {
    storageWrites.push(key);
    originalSet(key, value);
  };
  loaded.context.localStorage = loaded.window.localStorage;
  return { ...loaded, requests, storageWrites };
}

async function testRoleMapping() {
  const { context } = boot({});
  assert.equal(context.normalizeChatRole("assistant"), "bot");
  assert.equal(context.normalizeChatRole("ASSISTANT"), "bot");
  assert.equal(context.normalizeChatRole("user"), "user");
  assert.equal(context.normalizeChatRole("error"), "error");
  assert.equal(context.normalizeChatRole("system"), null);
  assert.equal(context.normalizeChatRole("tool"), null);
  assert.equal(context.normalizeChatRole(undefined), null);
}

async function testHistoryRenderingIsSideEffectFreeAndIdempotent() {
  const history = {
    messages: [
      { id: "m1", role: "user", text: "Привет" },
      { id: "m2", role: "assistant", text: "Здравствуйте" },
      { id: "m3", role: "system", text: "hidden system prompt" },
      { id: "m4", role: "tool", text: "raw tool output" },
    ],
  };
  const { context, document, requests, storageWrites } = boot({
    "/api/conversations/conv-1/messages": history,
  });
  context.loadHistory("conv-1");
  await settle();
  assert.deepEqual(messages(document), [
    { role: "user", text: "Привет" },
    { role: "bot", text: "Здравствуйте" },
  ]);
  assert.ok(
    requests.every((r) => !r.init || r.init.method !== "POST"),
    "history must not write",
  );
  assert.deepEqual(storageWrites, []);

  context.addMessage(
    "Здравствуйте",
    "assistant",
    false,
    0,
    null,
    0,
    null,
    null,
    null,
    { id: "m2" },
  );
  assert.equal(
    messages(document).length,
    2,
    "re-synchronized message must not duplicate",
  );

  context.loadHistory("conv-1");
  await settle();
  assert.equal(messages(document).length, 2, "reload renders the same messages once");
}

async function testHistoryEmptyAndErrors() {
  const empty = boot({
    "/api/conversations/conv-1/messages": {
      messages: [{ role: "system", text: "x" }],
    },
  });
  empty.context.loadHistory("conv-1");
  await settle();
  assert.equal(
    empty.document.querySelector(".empty-state").textContent,
    "Начните диалог",
  );

  const offline = boot({
    "/api/conversations/conv-1/messages": new Error("offline"),
  });
  offline.context.loadHistory("conv-1");
  await settle();
  assert.match(
    offline.document.querySelector(".error-state").textContent,
    /offline/,
  );
}

async function testLiveSendSuccess() {
  const { document, requests } = boot({
    "/api/chat": { reply: "Ответ", message_id: "a1", title: "Новый" },
  });
  document.getElementById("msg-input").value = "Вопрос";
  document.getElementById("send-btn").click();
  assert.deepEqual(messages(document), [{ role: "user", text: "Вопрос" }]);
  await settle();
  assert.deepEqual(messages(document), [
    { role: "user", text: "Вопрос" },
    { role: "bot", text: "Ответ" },
  ]);
  assert.equal(requests.filter((r) => r.url === "/api/chat").length, 1);
}

async function testLiveSendErrorsStaySeparate() {
  const apiError = boot({ "/api/chat": { error: "quota", status: 429 } });
  apiError.document.getElementById("msg-input").value = "Вопрос";
  apiError.document.getElementById("send-btn").click();
  await settle();
  assert.deepEqual(messages(apiError.document), [
    { role: "user", text: "Вопрос" },
    { role: "error", text: "⚠️ Ошибка: quota" },
  ]);

  const httpError = boot({ "/api/chat": { status: 502 } });
  httpError.document.getElementById("msg-input").value = "Вопрос";
  httpError.document.getElementById("send-btn").click();
  await settle();
  assert.equal(messages(httpError.document)[1].role, "error");

  const network = boot({ "/api/chat": new Error("down") });
  network.document.getElementById("msg-input").value = "Вопрос";
  network.document.getElementById("send-btn").click();
  await settle();
  assert.deepEqual(messages(network.document)[1], {
    role: "error",
    text: "⚠️ Ошибка: down",
  });
}

async function startVoice() {
  const browser = new BrowserShim(HTML);
  const sources = [];
  const dispatcher = {
    request: async (url) => {
      const body =
        url === "/api/voice/session" ? { session_id: "voice-1" } : { ok: true };
      return { ok: true, status: 200, json: async () => body };
    },
  };
  const { document, context } = browser.load(
    [
      path.join(ROOT, "static", "chat.js"),
      path.join(ROOT, "static", "voice.js"),
    ],
    {
      AliceDispatcher: dispatcher,
      performance: { now: () => 0 },
      currentConvId: "conv-1",
      Blob,
      MediaRecorder: class {
        static isTypeSupported() {
          return true;
        }
        start() {}
        addEventListener() {}
        stop() {}
      },
      AudioContext: class {
        createMediaStreamSource() {
          return { connect() {}, disconnect() {} };
        }
        close() {}
      },
      EventSource: class {
        constructor() {
          sources.push(this);
        }
        close() {}
      },
      Audio: class {
        play() {
          return Promise.resolve();
        }
      },
      navigator: {
        mediaDevices: {
          getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }),
        },
      },
    },
  );
  document.getElementById("mic-btn").click();
  await settle();
  const emit = (event) =>
    sources.at(-1).onmessage({ data: JSON.stringify(event) });
  return { document, context, emit };
}

async function testVoiceTranscriptCreatesExactlyOneUserMessage() {
  const { document, context, emit } = await startVoice();
  emit({
    type: "conversation.item.input_audio_transcription.completed",
    transcript: "   ",
  });
  assert.equal(
    messages(document).length,
    0,
    "empty transcript creates no message",
  );

  const done = {
    type: "conversation.item.input_audio_transcription.completed",
    transcript: " Привет, Алиса ",
    item_id: "item-1",
  };
  emit(done);
  emit(done);
  assert.deepEqual(messages(document), [
    { role: "user", text: "Привет, Алиса" },
  ]);

  emit({
    type: "response.output_text.done",
    text: "Привет!",
    response_id: "resp-1",
  });
  assert.deepEqual(messages(document)[1], { role: "bot", text: "Привет!" });
  emit({ type: "response.done" });

  context.addMessage(
    "Привет, Алиса",
    "user",
    false,
    0,
    null,
    0,
    null,
    null,
    null,
    { id: "voice-item-1" },
  );
  assert.equal(
    messages(document).length,
    2,
    "server/local sync must not duplicate",
  );
}

async function main() {
  await testRoleMapping();
  await testHistoryRenderingIsSideEffectFreeAndIdempotent();
  await testHistoryEmptyAndErrors();
  await testLiveSendSuccess();
  await testLiveSendErrorsStaySeparate();
  await testVoiceTranscriptCreatesExactlyOneUserMessage();
  console.log("chat history role tests passed");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});