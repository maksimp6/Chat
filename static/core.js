let currentConvId = null;
let conversations = [];
let currentModel = "aliceai-llm";
let currentModelType = "text";
let modelsData = {text: {}, voice: {}};
let coreInitialized = false;
let coreEnhancementStarted = false;

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
    "high-contrast": "Высокий контраст"
};

function normalizeAliceTheme(value) {
    return Object.prototype.hasOwnProperty.call(ALICE_THEMES, value) ? value : "light";
}

window.AliceTheme = {
    themes: ALICE_THEMES,
    getStored: function() {
        return normalizeAliceTheme(localStorage.getItem("theme") || "light");
    },
    apply: function(value, persist) {
        var theme = normalizeAliceTheme(value);
        document.documentElement.setAttribute("data-theme", theme);
        if (persist !== false) localStorage.setItem("theme", theme);
        var button = document.getElementById("theme-toggle");
        if (button) {
            var icon = theme === "dark" ? "☀️" : (theme === "light" ? "🌙" : "🎨");
            button.textContent = icon;
            button.title = "Цветовая схема: " + ALICE_THEMES[theme];
            button.setAttribute("aria-label", "Цветовая схема: " + ALICE_THEMES[theme]);
        }
        return theme;
    },
    cycle: function() {
        var themes = Object.keys(ALICE_THEMES);
        var current = this.getStored();
        var next = themes[(themes.indexOf(current) + 1) % themes.length];
        return this.apply(next, true);
    }
};

const savedTheme = window.AliceTheme.getStored();
window.AliceTheme.apply(savedTheme, false);

function fetchWithTimeout(input, init, timeoutMs) {
    if (typeof AbortController === "undefined") return fetch(input, init);
    var controller = new AbortController();
    var options = Object.assign({}, init || {}, {signal: controller.signal});
    var timer = setTimeout(function() { controller.abort(); }, timeoutMs);
    return fetch(input, options).finally(function() { clearTimeout(timer); });
}

window.changeModel = function(newModel, skipPatch) {
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
        fetch("/api/conversations/" + currentConvId, {
            method: "PATCH",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({model: newModel})
        }).catch(function(e) {
            console.error("[CORE] Model save failed:", e);
        });

        var conv = conversations.find(function(c) { return c.id === currentConvId; });
        if (conv) conv.model = newModel;
    }

    if (typeof updateModelButton === "function") updateModelButton();
    if (typeof updateUIForModel === "function") updateUIForModel();
};

function initializeShell() {
    window.AliceTheme.apply(savedTheme, false);

    var themeToggle = document.getElementById("theme-toggle");
    if (!themeToggle || themeToggle.dataset.coreBound === "true") return;

    themeToggle.dataset.coreBound = "true";
    themeToggle.addEventListener("click", function() {
        window.AliceTheme.cycle();
    });
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

async function enhanceCore() {
    if (coreEnhancementStarted) return;
    coreEnhancementStarted = true;

    try {
        console.log("[CORE] Загрузка диалогов...");
        var conversationsRes = await fetchWithTimeout(
            "/api/conversations",
            {credentials: "same-origin", cache: "no-store"},
            10000
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
        var modelsRes = await fetchWithTimeout(
            "/api/models",
            {credentials: "same-origin", cache: "no-store"},
            10000
        );
        if (!modelsRes.ok) {
            throw new Error("Failed to load models: " + modelsRes.status);
        }

        var loadedModels = await modelsRes.json();
        modelsData = loadedModels && typeof loadedModels === "object"
            ? {
                text: loadedModels.text && typeof loadedModels.text === "object" ? loadedModels.text : {},
                voice: loadedModels.voice && typeof loadedModels.voice === "object" ? loadedModels.voice : {}
            }
            : {text: {}, voice: {}};

        if (currentConvId) {
            var conv = conversations.find(function(c) { return c.id === currentConvId; });
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
                    await Promise.race([
                        window.loadServerConvSettings(currentConvId),
                        new Promise(function(resolve) { setTimeout(resolve, 5000); })
                    ]);
                } catch (e) {
                    console.error("[CORE] Failed to sync conversation settings:", e);
                }
            }
        }

        if (!currentConvId && typeof updateModelButton === "function") {
            updateModelButton();
        }
        if (typeof updateUIForModel === "function") updateUIForModel();
    } catch (error) {
        console.error("[CORE] Enhancement initialization error:", error);
        if (!conversations.length) applyConversationCache();
        if (typeof updateUIForModel === "function") updateUIForModel();
        if (typeof updateModelButton === "function") updateModelButton();
    }
}

function initCore() {
    if (coreInitialized) return;
    coreInitialized = true;

    console.log("[CORE] Shell initialization. currentConvId:", currentConvId);
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

window.updateUIForModel = function() {
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
