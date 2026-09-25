    var modelBtn = document.getElementById("model-btn");
    var closeModalBtn = document.getElementById("close-modal");

    if (closeModalBtn) closeModalBtn.addEventListener("click", function() {
        window.isCreatingNewChat = false;
        var modal = document.getElementById("model-modal");
        if (modal) modal.classList.remove("visible");
    });

    if (modelBtn) modelBtn.addEventListener("click", function() {
        window.isCreatingNewChat = false;
        if (typeof renderModelModal === "function") renderModelModal();
        var modal = document.getElementById("model-modal");
        if (modal) modal.classList.add("visible");
    });

function renderModelModal() {
    var modelList = document.getElementById("model-list");
    var modelModal = document.getElementById("model-modal");
    if (!modelList) return;
    modelList.innerHTML = "";
    var allModels = Object.assign({}, modelsData.text || {}, modelsData.voice || {});
    if (Object.keys(allModels).length === 0) {
        modelList.innerHTML = '<div class="model-option" style="cursor:default;opacity:.7;">Модели временно недоступны. Интерфейс продолжает работать.</div>';
        return;
    }
    Object.keys(allModels).forEach(function(key) {
        var m = allModels[key];
        var div = document.createElement("div");
        div.className = "model-option" + (key === currentModel ? " active" : "");
        var priceText = (m.input && m.output) ? (m.input + " / " + m.output + " \u20bd/1K") : "";
        var badges = "";
        if (modelsData.voice && modelsData.voice[key]) badges += " \uD83C\uDFA4";
        if (m.multimodal) badges += " \uD83D\uDCF7";
        div.innerHTML = '<div><strong>' + m.name + badges + '</strong></div>' +
                        '<div style="font-size:11px;color:var(--text-secondary)">' + priceText + '</div>';
        div.addEventListener("click", function() {
            if (typeof window.changeModel === "function") {
                window.changeModel(key);
            }
            if (modelModal) modelModal.classList.remove("visible");
            if (window.isCreatingNewChat) {
                createConversation(key);
                window.isCreatingNewChat = false;
            }
        });
        modelList.appendChild(div);
    });
}

function createConversation(modelKey) {
    fetch("/api/conversations", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({model: modelKey})
    }).then(function(r) { return r.json(); }).then(function(data) {
        if (data.error) { alert("\u041E\u0448\u0438\u0431\u043A\u0430: " + data.error); return; }
        conversations.unshift({id: data.id, title: data.title || "\u041D\u043E\u0432\u044B\u0439 \u0447\u0430\u0442", model: modelKey});
        localStorage.setItem("conversations", JSON.stringify(conversations));
        selectConv(data.id);
    }).catch(function(e) { alert("\u041E\u0448\u0438\u0431\u043A\u0430 \u0441\u0435\u0442\u0438: " + e); });
}

function updateModelButton() {
    var btn = document.getElementById("model-btn");
    if (!btn) return;
    var allModels = Object.assign({}, modelsData.text, modelsData.voice);
    var m = allModels[currentModel];
    if (m) {
        btn.textContent = "🤖";
        btn.title = "Выбор модели: " + m.name;
        btn.setAttribute("aria-label", "Выбор модели: " + m.name);
    }
}
