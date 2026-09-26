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

const settingsShim = new BrowserShim(template);
let settingsOpenCalls = 0;
settingsShim.window.SettingsUI = {
  injectModalStyles: function () {},
};
settingsShim.window.openSettingsModal = function () {
  settingsOpenCalls += 1;
};
const settingsRuntime = settingsShim.load(
  ["static/core_api.js", "static/ui_runtime.js", "static/settings.js", "static/header_actions.js"],
  {
    SettingsUI: settingsShim.window.SettingsUI,
    openSettingsModal: settingsShim.window.openSettingsModal,
  },
);
const settingsButton = settingsRuntime.document.getElementById("settings-btn");
if (!settingsButton) throw new Error("real settings button must exist in index.html");
settingsButton.click();
if (settingsOpenCalls !== 1) {
  throw new Error("real settings button click must open settings exactly once");
}
console.log("Real settings dispatcher click lifecycle passed");

const toolsShim = new BrowserShim(template);
toolsShim.window.AliceDispatcher = {
  request: async function () {
    return {
      ok: true,
      status: 200,
      json: async function () {
        return { categories: {} };
      },
    };
  },
};
const toolsRuntime = toolsShim.load(
  [
    "static/core_api.js",
    "static/ui_runtime.js",
    "static/settings/ui_helpers.js",
    "static/settings/settings_storage.js",
    "static/settings/settings_modal.js",
    "static/header_actions.js",
  ],
  {
    AliceDispatcher: toolsShim.window.AliceDispatcher,
  },
);
const toolsButton = toolsRuntime.document.getElementById("tools-btn");
if (!toolsButton) throw new Error("real tools button must exist in index.html");
toolsButton.click();
const toolsModal = toolsRuntime.document.getElementById("tools-modal-custom");
const toolsClose = toolsModal && toolsModal.querySelector(".modal-close");
if (!toolsModal || !toolsClose) {
  throw new Error("real tools modal controls must be created after click");
}
if (
  toolsClose.dataset.action !== "modal.close" ||
  toolsClose.dataset.modal !== "tools-modal-custom"
) {
  throw new Error("tools modal close must use generic modal.close dispatcher contract");
}
if (!toolsModal.parentNode || toolsModal.parentNode.id !== "app-root") {
  throw new Error("real tools modal must mount inside .alice-pro-app");
}
if (toolsModal.hidden || !toolsModal.classList.contains("visible")) {
  throw new Error("real tools button click must open tools modal");
}
if (toolsModal.getAttribute("aria-hidden") !== "false") {
  throw new Error("opened real tools modal must expose aria-hidden=false");
}
toolsClose.click();
if (!toolsModal.hidden || toolsModal.classList.contains("visible")) {
  throw new Error("real tools close button must close tools modal");
}
if (toolsModal.getAttribute("aria-hidden") !== "true") {
  throw new Error("closed real tools modal must expose aria-hidden=true");
}
console.log("Real tools modal click lifecycle passed");

const mcpShim = new BrowserShim(template);
mcpShim.window.SettingsUI = {
  injectModalStyles: function () {},
  escapeHtml: function (value) {
    return String(value || "");
  },
  strToArr: function (value) {
    return value ? String(value).split(",") : [];
  },
};
mcpShim.window.AliceDispatcher = {
  request: async function () {
    return {
      ok: true,
      status: 200,
      json: async function () {
        return { data: [] };
      },
    };
  },
};
const mcpRuntime = mcpShim.load(
  [
    "static/core_api.js",
    "static/ui_runtime.js",
    "static/settings/settings_mcp.js",
    "static/header_actions.js",
  ],
  {
    SettingsUI: mcpShim.window.SettingsUI,
    AliceDispatcher: mcpShim.window.AliceDispatcher,
  },
);
const mcpButton = mcpRuntime.document.getElementById("mcp-btn");
if (!mcpButton) throw new Error("real MCP button must exist in index.html");
mcpButton.click();
const mcpModal = mcpRuntime.document.getElementById("mcp-manager-modal");
const mcpClose = mcpModal && mcpModal.querySelector(".modal-close");
if (!mcpModal || !mcpClose) {
  throw new Error("real MCP modal controls must be created after click");
}
if (!mcpModal.parentNode || mcpModal.parentNode.id !== "app-root") {
  throw new Error("real MCP modal must mount inside .alice-pro-app");
}
if (
  mcpClose.dataset.action !== "mcp-manager.close" ||
  mcpClose.dataset.modal !== "mcp-manager-modal"
) {
  throw new Error("MCP modal close must use dispatcher contract");
}
if (mcpModal.hidden || !mcpModal.classList.contains("visible")) {
  throw new Error("real MCP button click must open MCP modal");
}
if (mcpModal.getAttribute("aria-hidden") !== "false") {
  throw new Error("opened real MCP modal must expose aria-hidden=false");
}
mcpClose.click();
if (!mcpModal.hidden || mcpModal.classList.contains("visible")) {
  throw new Error("real MCP close button must close MCP modal");
}
if (mcpModal.getAttribute("aria-hidden") !== "true") {
  throw new Error("closed real MCP modal must expose aria-hidden=true");
}
console.log("Real MCP modal click lifecycle passed");

const filesShim = new BrowserShim(template);
filesShim.window.SettingsUI = {
  escapeHtml: function (value) {
    return String(value || "");
  },
};
filesShim.window.AliceDispatcher = {
  request: async function () {
    return {
      ok: true,
      status: 200,
      json: async function () {
        return { data: [] };
      },
    };
  },
};
const filesRuntime = filesShim.load(
  [
    "static/core_api.js",
    "static/ui_runtime.js",
    "static/file_manager.js",
    "static/header_actions.js",
  ],
  {
    SettingsUI: filesShim.window.SettingsUI,
    AliceDispatcher: filesShim.window.AliceDispatcher,
  },
);
const filesButton = filesRuntime.document.getElementById("file-manager-btn");
if (!filesButton) throw new Error("real file manager button must exist in index.html");
filesButton.click();
const filesModal = filesRuntime.document.getElementById("file-manager-modal");
const filesClose = filesModal && filesModal.querySelector(".modal-close");
if (!filesModal || !filesClose) {
  throw new Error("real file manager modal controls must be created after click");
}
if (!filesModal.parentNode || filesModal.parentNode.id !== "app-root") {
  throw new Error("real file manager modal must mount inside .alice-pro-app");
}
if (
  filesClose.dataset.action !== "file-manager.close" ||
  filesClose.dataset.modal !== "file-manager-modal"
) {
  throw new Error("file manager close must use dispatcher contract");
}
if (filesModal.hidden || !filesModal.classList.contains("visible")) {
  throw new Error("real file manager button click must open file manager modal");
}
if (filesModal.getAttribute("aria-hidden") !== "false") {
  throw new Error("opened file manager modal must expose aria-hidden=false");
}
filesClose.click();
if (!filesModal.hidden || filesModal.classList.contains("visible")) {
  throw new Error("real file manager close button must close file manager modal");
}
if (filesModal.getAttribute("aria-hidden") !== "true") {
  throw new Error("closed file manager modal must expose aria-hidden=true");
}
console.log("Real file manager modal click lifecycle passed");

const treasuryShim = new BrowserShim(template);
treasuryShim.window.AliceDispatcher = {
  request: async function (url) {
    if (url === "/api/treasury/account") {
      return {
        ok: true,
        status: 200,
        json: async function () {
          return { balance: 100, currency: "RUB", ledger: [] };
        },
      };
    }
    throw new Error("unexpected treasury request: " + url);
  },
};
const treasuryRuntime = treasuryShim.load(
  ["static/core_api.js", "static/ui_runtime.js", "static/treasury.js", "static/header_actions.js"],
  {
    AliceDispatcher: treasuryShim.window.AliceDispatcher,
  },
);
const treasuryButton = treasuryRuntime.document.getElementById("treasury-btn");
if (!treasuryButton) throw new Error("real treasury button must exist in index.html");
treasuryButton.click();
const treasuryModal = treasuryRuntime.document.getElementById("treasury-modal");
const treasuryClose = treasuryModal && treasuryModal.querySelector(".modal-close");
if (!treasuryModal || !treasuryClose) {
  throw new Error("real treasury modal controls must be created after click");
}
if (!treasuryModal.parentNode || treasuryModal.parentNode.id !== "app-root") {
  throw new Error("real treasury modal must mount inside .alice-pro-app");
}
if (
  treasuryClose.dataset.action !== "treasury.close" ||
  treasuryClose.dataset.modal !== "treasury-modal"
) {
  throw new Error("treasury modal close must use dispatcher contract");
}
if (treasuryModal.hidden || !treasuryModal.classList.contains("visible")) {
  throw new Error("real treasury button click must open treasury modal");
}
if (treasuryModal.getAttribute("aria-hidden") !== "false") {
  throw new Error("opened treasury modal must expose aria-hidden=false");
}
treasuryClose.click();
if (!treasuryModal.hidden || treasuryModal.classList.contains("visible")) {
  throw new Error("real treasury close button must close treasury modal");
}
if (treasuryModal.getAttribute("aria-hidden") !== "true") {
  throw new Error("closed treasury modal must expose aria-hidden=true");
}
console.log("Real treasury modal click lifecycle passed");

const credentialsShim = new BrowserShim(template);
credentialsShim.window.AliceDispatcher = {
  request: async function (url) {
    if (url === "/api/provider-credentials/status") {
      return {
        ok: true,
        status: 200,
        json: async function () {
          return { providers: [] };
        },
      };
    }
    throw new Error("unexpected provider credentials request: " + url);
  },
};
const credentialsRuntime = credentialsShim.load(
  [
    "static/core_api.js",
    "static/ui_runtime.js",
    "static/provider_credentials.js",
    "static/header_actions.js",
  ],
  {
    AliceDispatcher: credentialsShim.window.AliceDispatcher,
  },
);
const credentialsButton = credentialsRuntime.document.getElementById("provider-credentials-btn");
const credentialsModal = credentialsRuntime.document.getElementById("provider-credentials-modal");
const credentialsClose = credentialsModal && credentialsModal.querySelector(".modal-close");
if (!credentialsButton || !credentialsModal || !credentialsClose) {
  throw new Error("real provider credentials modal controls must exist");
}
if (credentialsModal.hidden !== true) {
  throw new Error("provider credentials modal must start hidden");
}
credentialsButton.click();
if (credentialsModal.hidden || !credentialsModal.classList.contains("visible")) {
  throw new Error("real provider credentials button click must open modal");
}
if (credentialsModal.getAttribute("aria-hidden") !== "false") {
  throw new Error("opened provider credentials modal must expose aria-hidden=false");
}
if (
  credentialsClose.dataset.action !== "provider-credentials.close" ||
  credentialsClose.dataset.modal !== "provider-credentials-modal"
) {
  throw new Error("provider credentials close must use dispatcher contract");
}
credentialsClose.click();
if (credentialsModal.hidden !== true || credentialsModal.classList.contains("visible")) {
  throw new Error("real provider credentials close button must close modal");
}
if (credentialsModal.getAttribute("aria-hidden") !== "true") {
  throw new Error("closed provider credentials modal must expose aria-hidden=true");
}
console.log("Real provider credentials modal click lifecycle passed");

const departmentsShim = new BrowserShim(template);
departmentsShim.window.AliceDispatcher = {
  request: async function (url) {
    if (url === "/api/departments") {
      return {
        ok: true,
        status: 200,
        json: async function () {
          return { departments: [] };
        },
      };
    }
    throw new Error("unexpected departments request: " + url);
  },
};
const departmentsRuntime = departmentsShim.load(
  [
    "static/core_api.js",
    "static/ui_runtime.js",
    "static/departments.js",
    "static/header_actions.js",
  ],
  {
    AliceDispatcher: departmentsShim.window.AliceDispatcher,
  },
);
const departmentsButton = departmentsRuntime.document.getElementById("departments-btn");
if (!departmentsButton) throw new Error("real departments button must exist in index.html");
departmentsButton.click();
const departmentsModal = departmentsRuntime.document.getElementById("departments-modal");
const departmentsClose = departmentsModal && departmentsModal.querySelector(".modal-close");
if (!departmentsModal || !departmentsClose) {
  throw new Error("real departments modal controls must be created after click");
}
if (!departmentsModal.parentNode || departmentsModal.parentNode.id !== "app-root") {
  throw new Error("real departments modal must mount inside .alice-pro-app");
}
if (
  departmentsClose.dataset.action !== "departments.close" ||
  departmentsClose.dataset.modal !== "departments-modal"
) {
  throw new Error("departments modal close must use dispatcher contract");
}
if (departmentsModal.hidden || !departmentsModal.classList.contains("visible")) {
  throw new Error("real departments button click must open departments modal");
}
if (departmentsModal.getAttribute("aria-hidden") !== "false") {
  throw new Error("opened departments modal must expose aria-hidden=false");
}
departmentsClose.click();
if (!departmentsModal.hidden || departmentsModal.classList.contains("visible")) {
  throw new Error("real departments close button must close departments modal");
}
if (departmentsModal.getAttribute("aria-hidden") !== "true") {
  throw new Error("closed departments modal must expose aria-hidden=true");
}
console.log("Real departments modal click lifecycle passed");

console.log("Real header runtime action tests passed");
