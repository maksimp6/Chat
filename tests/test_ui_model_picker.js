"use strict";

// Model picker on the BrowserShim: real static/core.js and static/models.js.

const assert = require("node:assert/strict");
const test = require("node:test");
const vm = require("node:vm");
const { BrowserShim } = require("../browser/emulator/browser_dom");

const HTML =
  '<button id="model-btn" type="button"></button>' +
  '<div id="model-list"></div><div id="model-modal"></div>';

const MODELS = {
  text: {
    "yandexgpt-5-lite": { name: "Logos", input: 0.5, output: 1.2, currency: "RUB" },
    "free-model": { name: "Free", input: 0, output: 0, currency: "RUB" },
    "yandexgpt-5-lite": { name: "YandexGPT 5 Lite", input: 0.2, output: 0.2, currency: "RUB" },
  },
  voice: {},
};

function boot() {
  const requests = [];
  const shim = new BrowserShim(HTML);
  const { document, window, context } = shim.load(["static/core.js", "static/models.js"], {
    LogosCoreAPI: {
      ui: {
        modal: { open: () => {}, close: () => {} },
        actions: { register: () => {} },
        events: { mountClicks: () => {} },
      },
    },
    LogosDispatcher: {
      request: (url, options = {}) => {
        requests.push({
          url,
          method: options.method || "GET",
          body: options.body ? JSON.parse(options.body) : null,
        });
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ id: "new-chat", title: "Новый чат" }),
        });
      },
    },
    selectConv: () => {},
    conversations: [],
    alert: () => {},
  });
  return { document, window, context, requests };
}

function setState(context, { currentConvId, currentModel = "yandexgpt-5-lite" }) {
  vm.runInContext(
    `modelsData = ${JSON.stringify(MODELS)};
     currentModel = ${JSON.stringify(currentModel)};
     currentConvId = ${JSON.stringify(currentConvId)};
     renderModelModal();`,
    context,
  );
}

function option(document, key) {
  return Array.from(document.querySelectorAll(".model-option")).find((node) =>
    node.getAttribute("aria-label").startsWith(MODELS.text[key].name),
  );
}

async function settle() {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

test("a free model shows an explicit zero price, not a missing one", () => {
  const { document, context } = boot();
  setState(context, { currentConvId: null });
  assert.equal(option(document, "free-model").querySelector(".model-option-price").textContent, "0 / 0 ₽/1K");
});

test("starting a new chat does not rewrite the model of the current conversation", async () => {
  const { document, window, context, requests } = boot();
  setState(context, { currentConvId: "old-chat" });
  window.isCreatingNewChat = true;

  option(document, "yandexgpt-5-lite").click();
  await settle();

  assert.equal(
    requests.filter((r) => r.method === "PATCH").length,
    0,
    "no PATCH may reach the conversation that is still current",
  );
  const created = requests.find((r) => r.method === "POST" && r.url === "/api/conversations");
  assert.deepEqual(created.body, { model: "yandexgpt-5-lite" });
  assert.equal(window.isCreatingNewChat, false);
});

test("choosing a model in an existing chat patches that conversation", async () => {
  const { document, window, context, requests } = boot();
  setState(context, { currentConvId: "old-chat" });
  window.isCreatingNewChat = false;

  option(document, "yandexgpt-5-lite").click();
  await settle();

  const writes = requests.filter((r) => r.method !== "GET");
  assert.deepEqual(writes, [
    {
      url: "/api/conversations/old-chat",
      method: "PATCH",
      body: { model: "yandexgpt-5-lite" },
    },
  ]);
});
