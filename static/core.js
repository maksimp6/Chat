let currentConvId = null;
let conversations = [];
let currentModel = "aliceai-llm";
let currentModelType = "text";
let modelsData = {text: {}, voice: {}};

// Инициализация currentConvId
const urlParams = new URLSearchParams(window.location.search);
const urlConvId = urlParams.get('conversation_id');
if (urlConvId) {
    currentConvId = urlConvId;
    localStorage.setItem("current_conv_id", urlConvId);
} else {
    currentConvId = localStorage.getItem("current_conv_id") || null;
}

// Инициализация темы
const savedTheme = localStorage.getItem("theme") || "light";
document.documentElement.setAttribute("data-theme", savedTheme);

// Глобальная функция смены модели
window.changeModel = function(newModel, skipPatch) {
    if (!modelsData.text[newModel] && !modelsData.voice[newModel]) {
        console.warn("[CORE] Unknown model:", newModel, "-> fallback to aliceai-llm");
        newModel = "aliceai-llm";
    }
    currentModel = newModel;
    currentModelType = (modelsData.voice && modelsData.voice[currentModel]) ? "voice" : "text";
    localStorage.setItem("current_model", currentModel);
    
    if (!skipPatch && currentConvId) {
        fetch("/api/conversations/" + currentConvId, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ model: newModel })
        }).catch(function(e) { console.error("[CORE] Model save failed:", e); });
        
        var conv = conversations.find(function(c) { return c.id === currentConvId; });
        if (conv) conv.model = newModel;
    }
    
    if (typeof updateModelButton === "function") updateModelButton();
    if (typeof updateUIForModel === "function") updateUIForModel();
};

document.addEventListener("DOMContentLoaded", async function() {
    console.log("[CORE] DOMContentLoaded. currentConvId:", currentConvId);

    // Тема
    var themeToggle = document.getElementById("theme-toggle");
    if (themeToggle) {
        themeToggle.textContent = savedTheme === "dark" ? "☀️" : "🌙";
        themeToggle.addEventListener("click", function() {
            var cur = document.documentElement.getAttribute("data-theme");
            var next = cur === "dark" ? "light" : "dark";
            document.documentElement.setAttribute("data-theme", next);
            localStorage.setItem("theme", next);
            themeToggle.textContent = next === "dark" ? "☀️" : "🌙";
        });
    }

    try {
        // 1. Загрузка диалогов
        console.log("[CORE] Загрузка диалогов...");
        const conversationsRes = await fetch("/api/conversations");
        if (!conversationsRes.ok) throw new Error("Failed to load conversations: " + conversationsRes.status);
        const conversationsData = await conversationsRes.json();
        
        // Защита: проверяем, что это массив
        conversations = Array.isArray(conversationsData.conversations) ? conversationsData.conversations : [];
        localStorage.setItem("conversations", JSON.stringify(conversations));
        console.log("[CORE] Диалоги загружены, количество:", conversations.length);
        if (typeof renderSidebar === "function") renderSidebar();

        // 2. Загрузка моделей
        console.log("[CORE] Загрузка моделей...");
        const modelsRes = await fetch("/api/models");
        if (!modelsRes.ok) throw new Error("Failed to load models: " + modelsRes.status);
        modelsData = await modelsRes.json();
        console.log("[CORE] Модели загружены");

        // 3. Определение и установка модели текущего диалога
        if (currentConvId) {
            console.log("[CORE] Поиск диалога:", currentConvId);
            const conv = conversations.find(c => c.id === currentConvId);
            if (conv && conv.model) {
                console.log("[CORE] Установка модели диалога:", conv.model);
                window.changeModel(conv.model, true); // true = skipPatch при инициализации
            }
            
            // 4. Загрузка истории
            if (typeof loadHistory === "function") {
                console.log("[CORE] Загрузка истории для:", currentConvId);
                try {
                    await loadHistory(currentConvId);
                } catch (e) {
                    console.error("[CORE] Failed to load history:", e);
                }

// 👇 НОВОЕ: Синхронизация настроек с сервером при старте
if (typeof window.loadServerConvSettings === "function") {
    await window.loadServerConvSettings(currentConvId);
    console.log("[CORE] Настройки диалога синхронизированы с сервером");
}
            }
        } else {
             if (typeof updateUIForModel === "function") updateUIForModel();
             if (typeof updateModelButton === "function") updateModelButton();
        }
    } catch (error) {
        console.error("[CORE] Initialization error:", error);
        // Фолбэк на localStorage при ошибке сети
        try {
            const localConvs = JSON.parse(localStorage.getItem("conversations") || "[]");
            conversations = Array.isArray(localConvs) ? localConvs : [];
        } catch (e) {
            conversations = [];
        }
        if (typeof renderSidebar === "function") renderSidebar();
    }
});

function getMessages(id) { 
    try {
        return JSON.parse(localStorage.getItem("messages_" + id) || "[]"); 
    } catch (e) {
        return [];
    }
}
function saveMessages(id, msgs) { localStorage.setItem("messages_" + id, JSON.stringify(msgs)); }

window.updateUIForModel = function() {
    var micBtn = document.getElementById("mic-btn");
    var uploadBtn = document.getElementById("upload-image-btn");
    var modeWrap = document.getElementById("response-mode-wrap");
    var voiceUi = document.getElementById("voice-ui");
    var textUi = document.getElementById("text-ui");
    
    var isVoice = !!(modelsData.voice && modelsData.voice[currentModel]);
    var isMultimodal = !!(modelsData.text && modelsData.text[currentModel] && modelsData.text[currentModel].multimodal);
    
    // Переключаем видимость блоков UI
    if (voiceUi) voiceUi.style.display = isVoice ? "flex" : "none";
    if (textUi) textUi.style.display = isVoice ? "none" : "flex";
    
    // Внутренние элементы
    if (micBtn) micBtn.style.display = isVoice ? "flex" : "none";
    if (uploadBtn) uploadBtn.style.display = isMultimodal ? "flex" : "none";
    if (modeWrap) modeWrap.style.display = isVoice ? "flex" : "none";
};