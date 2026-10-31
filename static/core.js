// The server's own reason for a failed response, so the user sees why it failed
// instead of a bare "HTTP 400" or "try again" (#1668). Resolves to an Error.
window.LogosHttpError = function (response) {
  var fallback = "HTTP " + response.status;
  return response.json().then(
    function (body) {
      var error = body && body.error;
      var text =
        (body && (body.message || body.detail || body.title)) ||
        (error && typeof error === "object" ? error.message || error.code : error);
      return new Error(typeof text === "string" && text ? text : fallback);
    },
    function () {
      return new Error(fallback);
    },
  );
};

// "Не удалось …: <reason>" for a toast.
window.LogosErrorText = function (prefix, error) {
  var reason = error && error.message ? error.message : String(error || "");
  return reason ? prefix + ": " + reason : prefix;
};

let currentConvId = null;
let conversations = [];
let currentModel = "qwen3.6-35b-a3b";
let currentModelType = "text";
let modelsData = { text: {}, voice: {}, image: {} };
let modelSelectionSequence = 0;
const modelSelectionSaves = new Map();
let coreInitialized = false;
let coreEnhancementStarted = false;
let modelLoadState = { status: "idle", message: "" };
// Conversation list load: "idle" | "loading" | "ready" | "failed".
let conversationsLoadState = "idle";

// URL is authoritative for the selected conversation when present.
const urlParams = new URLSearchParams(window.location.search);
const urlConvId = urlParams.get("conversation_id");
if (urlConvId) {
  currentConvId = urlConvId;
  localStorage.setItem("current_conv_id", urlConvId);
} else {
  // A value written as "null" by an older build is not a conversation id.
  currentConvId = localStorage.getItem("current_conv_id") || null;
  if (currentConvId === "null") currentConvId = null;
}

const LOGOS_THEMES = {
  light: "Светлая",
  dark: "Тёмная",
  dim: "Приглушённая",
  "high-contrast": "Высокий контраст",
};

function normalizeLogosTheme(value) {
  return Object.prototype.hasOwnProperty.call(LOGOS_THEMES, value) ? value : "light";
}

window.LogosTheme = {
  themes: LOGOS_THEMES,
  getStored: function () {
    return normalizeLogosTheme(
      localStorage.getItem("theme") ||
        (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
    );
  },
  apply: function (value, persist) {
    var theme = normalizeLogosTheme(value);
    document.documentElement.setAttribute("data-theme", theme);
    if (persist !== false) localStorage.setItem("theme", theme);
    var button = document.getElementById("theme-toggle");
    if (button) {
      // The button shows the theme it switches to next.
      var icon = { light: "🌙", dark: "🌓", dim: "◑", "high-contrast": "☀️" }[theme] || "🎨";
      button.textContent = icon;
      button.title = "Цветовая схема: " + LOGOS_THEMES[theme];
      button.setAttribute("aria-label", "Цветовая схема: " + LOGOS_THEMES[theme]);
    }
    return theme;
  },
  cycle: function () {
    var themes = Object.keys(LOGOS_THEMES);
    var current = this.getStored();
    var next = themes[(themes.indexOf(current) + 1) % themes.length];
    return this.apply(next, true);
  },
};

// Keys renamed in #1316; stored conversations may still carry the old names.
const LEGACY_MODEL_KEYS = {
};

const savedTheme = window.LogosTheme.getStored();
window.LogosTheme.apply(savedTheme, false);

function fetchWithTimeout(input, init, timeoutMs) {
  return window.LogosDispatcher.request(input, init, { timeoutMs: timeoutMs });
}

window.isDialogModel = function (modelKey) {
  if (typeof modelKey !== "string" || !modelKey.trim()) return false;
  if (/^(?:emb|cls):\/\//.test(modelKey) || /(?:^|\/)(?:classifier-|text-search-|text-embeddings)/.test(modelKey)) {
    return false;
  }
  var textModels = modelsData.text || {};
  var voiceModels = modelsData.voice || {};
  var imageModels = modelsData.image || {};
  var modelKeys = Object.keys(textModels).concat(Object.keys(voiceModels), Object.keys(imageModels));
  var catalogLoaded = modelKeys.length > 0;
  // The server validates selection when the browser has no loaded catalog.
  if (!catalogLoaded) return true;
  var entry = textModels[modelKey] || voiceModels[modelKey] || imageModels[modelKey];
  return Boolean(entry && ["text", "voice", "image"].includes(entry.type) &&
    entry.dialog_selectable !== false && entry.available !== false);
};

window.changeModel = function (newModel, skipPatch) {
  newModel = LEGACY_MODEL_KEYS[newModel] || newModel;
  if (!window.isDialogModel(newModel)) {
    if (window.LogosToast) window.LogosToast.error("Эта модель не предназначена для диалога");
    return false;
  }
  if (!skipPatch && currentConvId && modelSelectionSaves.has(currentConvId)) {
    if (window.LogosToast) window.LogosToast.error("Дождитесь сохранения выбранной модели");
    return false;
  }
  var previousModel = currentModel;
  var previousType = currentModelType;
  var previousStoredModel = localStorage.getItem("current_model");
  var voiceModels = modelsData.voice || {};
  var imageModels = modelsData.image || {};
  currentModel = newModel;
  currentModelType = voiceModels[currentModel] ? "voice" : imageModels[currentModel] ? "image" : "text";
  localStorage.setItem("current_model", currentModel);

  if (!skipPatch && currentConvId) {
    var selectedConvId = currentConvId;
    var conv = conversations.find(function (c) {
      return c.id === selectedConvId;
    });
    var previousConversationModel = conv && conv.model;
    var selectionId = ++modelSelectionSequence;
    modelSelectionSaves.set(selectedConvId, selectionId);
    window.LogosDispatcher.request("/api/conversations/" + selectedConvId, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: newModel }),
    }).then(function (response) {
      if (!response.ok) return window.LogosHttpError(response).then(function (e) { throw e; });
    }).catch(function (e) {
      console.error("[CORE] Model save failed:", e);
      if (modelSelectionSaves.get(selectedConvId) !== selectionId) return;
      if (conv && conv.model === newModel) conv.model = previousConversationModel;
      if (currentConvId === selectedConvId && currentModel === newModel) {
        currentModel = previousModel;
        currentModelType = previousType;
        if (previousStoredModel === null) localStorage.removeItem("current_model");
        else localStorage.setItem("current_model", previousStoredModel);
        if (typeof updateModelButton === "function") updateModelButton();
        if (typeof updateUIForModel === "function") updateUIForModel();
      }
      if (window.LogosToast) window.LogosToast.error(window.LogosErrorText("Не удалось сохранить выбранную модель", e));
    }).finally(function () {
      if (modelSelectionSaves.get(selectedConvId) === selectionId) modelSelectionSaves.delete(selectedConvId);
    });
    if (conv) conv.model = newModel;
  }

  if (typeof updateModelButton === "function") updateModelButton();
  if (typeof updateUIForModel === "function") updateUIForModel();
  return true;
};

function initializeShell() {
  window.LogosTheme.apply(savedTheme, false);

  if (!window.__logosThemeActionRegistered) {
    window.LogosCoreAPI.ui.actions.register("theme.cycle", function () {
      window.LogosTheme.cycle();
    });
    window.__logosThemeActionRegistered = true;
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
      throw await window.LogosHttpError(response);
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
    var image = payload.image && typeof payload.image === "object" ? payload.image : {};
    if (!Object.keys(text).length && !Object.keys(voice).length && !Object.keys(image).length) {
      reportModelLoad("empty_response");
      failureClassified = true;
      throw new Error("Empty model catalog");
    }
    modelsData = { text: text, voice: voice, image: image };
    modelLoadState = { status: "ready", message: "" };
    reportModelLoad("success", {
      modelCount: Object.keys(text).length + Object.keys(voice).length + Object.keys(image).length,
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

window.LogosModelCatalog = { load: loadModels };

async function enhanceCore() {
  if (coreEnhancementStarted) return;
  coreEnhancementStarted = true;

  try {
    conversationsLoadState = "loading";
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
    conversationsLoadState = "ready";
    if (typeof renderSidebar === "function") renderSidebar();

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
    if (window.LogosCoreAPI && window.LogosCoreAPI.status) {
      window.LogosCoreAPI.status.set("core", "ready", "Ядро готово", { revocable: false });
    }
  } catch (error) {
    if (conversationsLoadState === "loading") conversationsLoadState = "failed";
    console.error("[CORE] Enhancement initialization error:", error);
    if (window.LogosCoreAPI && window.LogosCoreAPI.status) {
      window.LogosCoreAPI.status.set(
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

// Retry button of the sidebar after a failed conversation list load.
window.retryConversationsLoad = function () {
  if (conversationsLoadState !== "failed") return;
  coreEnhancementStarted = false;
  void enhanceCore();
};

function initCore() {
  if (coreInitialized) return;
  coreInitialized = true;

  if (window.LogosCoreAPI && window.LogosCoreAPI.status) {
    window.LogosCoreAPI.status.set("core", "running", "Ядро выполняет инициализацию", {
      revocable: false,
    });
  }
  initializeShell();
  applyConversationCache();
  // Restore the open conversation's model from the local cache, so replies do not go to the default model while the server list loads.
  var cachedCurrentConv = conversations.find(function (c) {
    return c.id === currentConvId;
  });
  if (cachedCurrentConv && cachedCurrentConv.model) window.changeModel(cachedCurrentConv.model, true);
  if (typeof updateUIForModel === "function") updateUIForModel();
  if (typeof updateModelButton === "function") updateModelButton();
  if (window.LogosBoot && typeof window.LogosBoot.markShellReady === "function") {
    window.LogosBoot.markShellReady();
  }

  // Network/API enhancement is deliberately separate from shell startup.
  void enhanceCore();
}

// Deferred scripts run after HTML parsing. Starting here keeps later optional
// resources from delaying the usable shell until DOMContentLoaded.
initCore();

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

// A model reads photos when its catalog entry says so; the server accepts photos only for those models.
window.modelAcceptsPhotos = function (modelKey) {
  var entry = (modelsData.text || {})[modelKey];
  return !!(entry && entry.capabilities && entry.capabilities.multimodal === true);
};

window.updateUIForModel = function () {
  var root = document.querySelector(".logos-pro-app");
  var micBtn = document.getElementById("mic-btn");
  var modeWrap = document.getElementById("response-mode-wrap");
  var voiceUi = document.getElementById("voice-ui");
  var textUi = document.getElementById("text-ui");

  var voiceModels = modelsData.voice || {};
  var isVoice = !!voiceModels[currentModel];
  var isMultimodal = window.modelAcceptsPhotos(currentModel);

  if (!root) return;

  root.classList.toggle("logos-model-voice", isVoice);
  root.classList.toggle("logos-model-multimodal", isMultimodal);

  if (micBtn) micBtn.setAttribute("aria-hidden", isVoice ? "false" : "true");
  if (modeWrap) modeWrap.setAttribute("aria-hidden", isVoice ? "false" : "true");
  if (voiceUi) voiceUi.setAttribute("aria-hidden", isVoice ? "false" : "true");
  if (textUi) textUi.setAttribute("aria-hidden", isVoice ? "true" : "false");

  window.dispatchEvent(new Event("logos:model-changed"));
};
