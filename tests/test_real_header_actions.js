const fs = require("node:fs");
const { BrowserShim } = require("./browser_dom");

const template = fs.readFileSync("templates/index.html", "utf8");
const shim = new BrowserShim(template);
if (!shim.document.getElementById("header")) throw new Error("real header not found in index.html");
const { window, document } = shim.load(["static/core_api.js", "static/ui_runtime.js"]);

const UI = window.AliceCoreAPI.ui;
const buttons = document.querySelectorAll("#header button[data-action]");
if (!buttons.length) throw new Error("real header action inventory is empty");

const seen = Object.create(null);
const actions = [...new Set(buttons.map((button) => button.dataset.action))];
actions.forEach((action) => {
  if (action === "modal.open" || action === "modal.close") return;
  UI.actions.register(action, function (payload) {
    seen[action] = (seen[action] || 0) + 1;
    if (payload.element.dataset.action !== action) {
      throw new Error("dispatcher payload/action mismatch for " + action);
    }
  });
});

buttons.forEach((button) => {
  const action = button.dataset.action;
  if (action === "modal.open" || action === "modal.close") return;
  const before = seen[action] || 0;
  button.click();
  if ((seen[action] || 0) !== before + 1) {
    throw new Error(button.id + " must dispatch " + action + " exactly once");
  }
});

let recovered = 0;
UI.actions.register("contract.recoverable", function (payload) {
  recovered += 1;
  if (recovered === 1) throw new Error("expected first-click failure");
  if (payload.params.marker !== "still-alive") {
    throw new Error("recovered click must preserve normalized params");
  }
});
const recoverable = UI.button({
  id: "recoverable-real-click",
  text: "Recover",
  action: "contract.recoverable",
  params: { marker: "still-alive" },
});
document.body.appendChild(recoverable);
const originalError = console.error;
let logged = 0;
console.error = function () {
  logged += 1;
};
recoverable.click();
recoverable.click();
console.error = originalError;
if (logged !== 1) throw new Error("first failed click must be logged exactly once");
if (recovered !== 2) throw new Error("failed click must not disable later clicks");

const modelShim = new BrowserShim(template);
const modelRuntime = modelShim.load(
  ["static/core_api.js", "static/ui_runtime.js", "static/models.js"],
  {
    modelsData: { text: {}, voice: {} },
    currentModel: "aliceai-llm",
    conversations: [],
  },
);
const modelButton = modelRuntime.document.getElementById("model-btn");
const modelModal = modelRuntime.document.getElementById("model-modal");
const modelClose = modelRuntime.document.getElementById("close-modal");
if (!modelButton || !modelModal || !modelClose) {
  throw new Error("real model modal controls must exist in index.html");
}
if (modelButton.dataset.action !== "modal.open" || modelButton.dataset.modal !== "model-modal") {
  throw new Error("model button must use generic modal.open dispatcher contract");
}
if (!modelModal.hidden) throw new Error("real model modal must start hidden");
modelButton.click();
if (modelModal.hidden || !modelModal.classList.contains("visible")) {
  throw new Error("real model button click must open model-modal");
}
if (modelModal.getAttribute("aria-hidden") !== "false") {
  throw new Error("opened real model modal must expose aria-hidden=false");
}
modelClose.click();
if (!modelModal.hidden || modelModal.classList.contains("visible")) {
  throw new Error("real model modal close button must close model-modal");
}
if (modelModal.getAttribute("aria-hidden") !== "true") {
  throw new Error("closed real model modal must expose aria-hidden=true");
}
console.log("Real model modal click lifecycle passed");

const memoryShim = new BrowserShim(template);
const memoryRuntime = memoryShim.load(
  ["static/core_api.js", "static/ui_runtime.js", "static/memory_panel.js"],
  {
    AliceDispatcher: {
      request: async function () {
        return {
          ok: true,
          status: 200,
          json: async function () {
            return { config: { enabled: true, max_context_facts: 15 }, facts: [] };
          },
        };
      },
    },
    confirm: function () {
      return true;
    },
  },
);
const memoryButton = memoryRuntime.document.getElementById("memory-btn");
const memoryModal = memoryRuntime.document.getElementById("memoryModal");
const memoryClose = memoryRuntime.document.getElementById("memoryCloseBtn");
if (!memoryButton || !memoryModal || !memoryClose) {
  throw new Error("real memory modal controls must exist in index.html");
}
if (memoryButton.dataset.action !== "modal.open" || memoryButton.dataset.modal !== "memoryModal") {
  throw new Error("memory button must use generic modal.open dispatcher contract");
}
if (!memoryModal.hidden) throw new Error("real memory modal must start hidden");
memoryButton.click();
if (memoryModal.hidden || !memoryModal.classList.contains("visible")) {
  throw new Error("real memory button click must open memoryModal");
}
if (memoryModal.getAttribute("aria-hidden") !== "false") {
  throw new Error("opened real memory modal must expose aria-hidden=false");
}
memoryClose.click();
if (!memoryModal.hidden || memoryModal.classList.contains("visible")) {
  throw new Error("real memory modal close button must close memoryModal");
}
if (memoryModal.getAttribute("aria-hidden") !== "true") {
  throw new Error("closed real memory modal must expose aria-hidden=true");
}
console.log("Real memory modal click lifecycle passed");

console.log("Real header runtime action tests passed");
