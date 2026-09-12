document.addEventListener("DOMContentLoaded", function() {
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
});

function renderModelModal() {
    var modelList = document.getElementById("model-list");
    var modelModal = document.getElementById("model-modal");
    if (!modelList) return;
    modelList.innerHTML = "";
    var allModels = Object.assign({}, modelsData.text, modelsData.voice);
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
        conversations.unshift({id: data.id, title: "\u041D\u043E\u0432\u044B\u0439 \u0434\u0438\u0430\u043B\u043E\u0433", model: modelKey});
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
        var name = m.name.length > 22 ? m.name.substring(0, 22) + "..." : m.name;
        btn.textContent = name;
    }
}
