let currentConvId = null;
let conversations = [];
let currentModel = "aliceai-llm";
let currentModelType = "text";
let modelsData = { text: {}, voice: {} };
let coreInitialized = false;
let coreEnhancementStarted = false;
let modelLoadState = { status: "idle", message: "" };

// URL is authoritative for the selected conversation when present.
const urlParams = new URLSearchParams(window.location.search);
const urlConvId = urlParams.get("conversation_id");
if (urlConvId) {
  currentConvId = urlConvId;
  localStorage.setItem("current_conv_id", urlConvId);
} else {
  currentConvId = localStorage.getItem("current_conv_id") || null;
}

const ALICE_THEMES = {
  light: "Светлая",
  dark: "Тёмная",
  dim: "Приглушённая",
  "high-contrast": "Высокий контраст",
};

function normalizeAliceTheme(value) {
  return Object.prototype.hasOwnProperty.call(ALICE_THEMES, value) ? value : "light";
}

window.AliceTheme = {
  themes: ALICE_THEMES,
  getStored: function () {
    return normalizeAliceTheme(localStorage.getItem("theme") || "light");
  },
  apply: function (value, persist) {
    var theme = normalizeAliceTheme(value);
    document.documentElement.setAttribute("data-theme", theme);
    if (persist !== false) localStorage.setItem("theme", theme);
    var button = document.getElementById("theme-toggle");
    if (button) {
      var icon = theme === "dark" ? "☀️" : theme === "light" ? "🌙" : "🎨";
      button.textContent = icon;
      button.title = "Цветовая схема: " + ALICE_THEMES[theme];
      button.setAttribute("aria-label", "Цветовая схема: " + ALICE_THEMES[theme]);
    }
    return theme;
  },
  cycle: function () {
    var themes = Object.keys(ALICE_THEMES);
    var current = this.getStored();
    var next = themes[(themes.indexOf(current) + 1) % themes.length];
    return this.apply(next, true);
  },
};

const savedTheme = window.AliceTheme.getStored();
window.AliceTheme.apply(savedTheme, false);

function fetchWithTimeout(input, init, timeoutMs) {
  return window.AliceDispatcher.request(input, init, { timeoutMs: timeoutMs });
}

window.changeModel = function (newModel, skipPatch) {
  var textModels = modelsData.text || {};
  var voiceModels = modelsData.voice || {};
  if (!textModels[newModel] && !voiceModels[newModel]) {
    console.warn("[CORE] Unknown model:", newModel, "-> fallback to aliceai-llm");
    newModel = "aliceai-llm";
  }
  currentModel = newModel;
  currentModelType = voiceModels[currentModel] ? "voice" : "text";
  localStorage.setItem("current_model", currentModel);

  if (!skipPatch && currentConvId) {
    window.AliceDispatcher.request("/api/conversations/" + currentConvId, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: newModel }),
    }).catch(function (e) {
      console.error("[CORE] Model save failed:", e);
    });

    var conv = conversations.find(function (c) {
      return c.id === currentConvId;
    });
    if (conv) conv.model = newModel;
  }

  if (typeof updateModelButton === "function") updateModelButton();
  if (typeof updateUIForModel === "function") updateUIForModel();
};

function initializeShell() {
  window.AliceTheme.apply(savedTheme, false);

  if (!window.__aliceThemeActionRegistered) {
    window.AliceCoreAPI.ui.actions.register("theme.cycle", function () {
      window.AliceTheme.cycle();
    });
    window.__aliceThemeActionRegistered = true;
  }
}

function loadCachedConversations() {
  try {
    var localConvs = JSON.parse(localStorage.getItem("conversations") || "[]");
    return Array.isArray(localConvs) ? localConvs : [];
  } catch (e) {
    return [];
  }
}

function applyConversationCache() {
  conversations = loadCachedConversations();
  if (typeof renderSidebar === "function") renderSidebar();
}

function reportModelLoad(event, detail) {
  var context = Object.assign({ event: event, endpoint: "/api/models" }, detail || {});
  var method = event === "success" || event === "request_start" ? "info" : "error";
  console[method]("[MODEL_CATALOG]", context);
}

async function loadModels() {
  var failureClassified = false;
  modelLoadState = { status: "loading", message: "Загрузка моделей…" };
  reportModelLoad("request_start");
  if (typeof renderModelModal === "function") renderModelModal();
  try {
    var response = await fetchWithTimeout(
      "/api/models",
      { credentials: "same-origin", cache: "no-store" },
      10000,
    );
    if (!response.ok) {
      reportModelLoad("http_error", { status: response.status });
      failureClassified = true;
      throw new Error("HTTP " + response.status);
    }
    var payload;
    try {
      payload = await response.json();
    } catch (error) {
      reportModelLoad("parsing_error", { errorType: error && error.name });
      failureClassified = true;
      throw error;
    }
    if (
      !payload ||
      typeof payload !== "object" ||
      Array.isArray(payload) ||
      (payload.text !== undefined &&
        (typeof payload.text !== "object" || Array.isArray(payload.text))) ||
      (payload.voice !== undefined &&
        (typeof payload.voice !== "object" || Array.isArray(payload.voice)))
    ) {
      reportModelLoad("invalid_response", { payloadType: typeof payload });
      failureClassified = true;
      throw new Error("Invalid model catalog");
    }
    var text = payload.text && typeof payload.text === "object" ? payload.text : {};
    var voice = payload.voice && typeof payload.voice === "object" ? payload.voice : {};
    if (!Object.keys(text).length && !Object.keys(voice).length) {
      reportModelLoad("empty_response");
      failureClassified = true;
      throw new Error("Empty model catalog");
    }
    modelsData = { text: text, voice: voice };
    modelLoadState = { status: "ready", message: "" };
    reportModelLoad("success", {
      modelCount: Object.keys(text).length + Object.keys(voice).length,
    });
    if (typeof renderModelModal === "function") renderModelModal();
    return modelsData;
  } catch (error) {
    if (!failureClassified) {
      reportModelLoad("network_error", { errorType: error && error.name });
    }
    modelLoadState = {
      status: "error",
      message: "Не удалось загрузить модели. Проверьте соединение и повторите попытку.",
    };
    if (typeof renderModelModal === "function") renderModelModal();
    throw error;
  }
}

window.AliceModelCatalog = { load: loadModels };

async function enhanceCore() {
  if (coreEnhancementStarted) return;
  coreEnhancementStarted = true;

  try {
    console.log("[CORE] Загрузка диалогов...");
    var conversationsRes = await fetchWithTimeout(
      "/api/conversations",
      { credentials: "same-origin", cache: "no-store" },
      10000,
    );
    if (!conversationsRes.ok) {
      throw new Error("Failed to load conversations: " + conversationsRes.status);
    }

    var conversationsData = await conversationsRes.json();
    conversations = Array.isArray(conversationsData.conversations)
      ? conversationsData.conversations
      : [];
    localStorage.setItem("conversations", JSON.stringify(conversations));
    if (typeof renderSidebar === "function") renderSidebar();

    console.log("[CORE] Загрузка моделей...");
    try {
      await loadModels();
    } catch (error) {
      // Model selection is optional; conversations and history remain usable.
    }

    if (currentConvId) {
      var conv = conversations.find(function (c) {
        return c.id === currentConvId;
      });
      if (conv && conv.model) window.changeModel(conv.model, true);

      if (typeof loadHistory === "function") {
        try {
          await loadHistory(currentConvId);
        } catch (e) {
          console.error("[CORE] Failed to load history:", e);
        }
      }

      if (typeof window.loadServerConvSettings === "function") {
        try {
          await window.loadServerConvSettings(currentConvId);
        } catch (e) {
          console.error("[CORE] Failed to sync conversation settings:", e);
        }
      }
    }

    if (!currentConvId && typeof updateModelButton === "function") {
      updateModelButton();
    }
    if (typeof updateUIForModel === "function") updateUIForModel();
    if (window.AliceCoreAPI && window.AliceCoreAPI.status) {
      window.AliceCoreAPI.status.set("core", "ready", "Ядро готово", { revocable: false });
    }
  } catch (error) {
    console.error("[CORE] Enhancement initialization error:", error);
    if (window.AliceCoreAPI && window.AliceCoreAPI.status) {
      window.AliceCoreAPI.status.set(
        "core",
        "degraded",
        "Расширенная инициализация завершилась ошибкой",
        { revocable: false },
      );
    }
    if (!conversations.length) applyConversationCache();
    if (typeof updateUIForModel === "function") updateUIForModel();
    if (typeof updateModelButton === "function") updateModelButton();
  }
}

function initCore() {
  if (coreInitialized) return;
  coreInitialized = true;

  console.log("[CORE] Shell initialization. currentConvId:", currentConvId);
  if (window.AliceCoreAPI && window.AliceCoreAPI.status) {
    window.AliceCoreAPI.status.set("core", "running", "Ядро выполняет инициализацию", {
      revocable: false,
    });
  }
  initializeShell();
  applyConversationCache();
  if (typeof updateUIForModel === "function") updateUIForModel();
  if (typeof updateModelButton === "function") updateModelButton();

  // Network/API enhancement is deliberately separate from shell startup.
  void enhanceCore();
}

document.addEventListener("DOMContentLoaded", initCore);

function getMessages(id) {
  try {
    return JSON.parse(localStorage.getItem("messages_" + id) || "[]");
  } catch (e) {
    return [];
  }
}

function saveMessages(id, msgs) {
  localStorage.setItem("messages_" + id, JSON.stringify(msgs));
}

window.updateUIForModel = function () {
  var root = document.querySelector(".alice-pro-app");
  var micBtn = document.getElementById("mic-btn");
  var uploadBtn = document.getElementById("upload-image-btn");
  var modeWrap = document.getElementById("response-mode-wrap");
  var voiceUi = document.getElementById("voice-ui");
  var textUi = document.getElementById("text-ui");

  var textModels = modelsData.text || {};
  var voiceModels = modelsData.voice || {};
  var isVoice = !!voiceModels[currentModel];
  var isMultimodal = !!(textModels[currentModel] && textModels[currentModel].multimodal);

  if (!root) return;

  root.classList.toggle("alice-model-voice", isVoice);
  root.classList.toggle("alice-model-multimodal", isMultimodal);

  if (micBtn) micBtn.setAttribute("aria-hidden", isVoice ? "false" : "true");
  if (uploadBtn) uploadBtn.setAttribute("aria-hidden", isMultimodal ? "false" : "true");
  if (modeWrap) modeWrap.setAttribute("aria-hidden", isVoice ? "false" : "true");
  if (voiceUi) voiceUi.setAttribute("aria-hidden", isVoice ? "false" : "true");
  if (textUi) textUi.setAttribute("aria-hidden", isVoice ? "true" : "false");
};
