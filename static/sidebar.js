document.addEventListener("DOMContentLoaded", function() {
    var sidebar = document.getElementById("sidebar");
    var menuBtn = document.getElementById("menu-btn");
    var closeBtn = document.getElementById("close-sidebar-btn");
    var overlay = document.getElementById("overlay");
    var newChatBtn = document.getElementById("new-chat-btn");

    function openSidebar() { sidebar.classList.add("open"); overlay.classList.add("visible"); }
    function closeSidebar() { sidebar.classList.remove("open"); overlay.classList.remove("visible"); }

    if (menuBtn) menuBtn.addEventListener("click", function() { sidebar.classList.contains("open") ? closeSidebar() : openSidebar(); });
    if (closeBtn) closeBtn.addEventListener("click", closeSidebar);
    if (overlay) overlay.addEventListener("click", closeSidebar);
    if (newChatBtn) newChatBtn.addEventListener("click", function() {
        window.isCreatingNewChat = true;
        if (typeof renderModelModal === "function") renderModelModal();
        var modal = document.getElementById("model-modal");
        if (modal) modal.classList.add("visible");
    });
    if (typeof renderSidebar === "function") renderSidebar();
});

function renderSidebar() {
    var list = document.getElementById("conv-list");
    if (!list) return;
    list.innerHTML = "";
    if (!conversations.length) {
        list.innerHTML = '<div style="padding:20px;color:var(--text-secondary);text-align:center">Нет диалогов</div>';
        return;
    }
    conversations.forEach(function(conv) {
        var div = document.createElement("div");
        div.className = "conv-item" + (conv.id === currentConvId ? " active" : "");
        
        var titleSpan = document.createElement("span");
        titleSpan.className = "conv-title";
        titleSpan.textContent = conv.title;
        
        // Двойной клик для переименования
        titleSpan.addEventListener("dblclick", function(e) {
            e.stopPropagation();
            var newName = prompt("Новое имя диалога:", conv.title);
            if (newName && newName.trim() !== "") {
                fetch("/api/conversations/" + conv.id, {
                    method: "PATCH",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({ title: newName.trim() })
                })
                .then(r => {
                    if (!r.ok) throw new Error("Network response was not ok");
                    return r.json();
                })
                .then(data => {
                    if (data.status === "ok") {
                        conv.title = newName.trim();
                        localStorage.setItem("conversations", JSON.stringify(conversations));
                        renderSidebar();
                    } else {
                        alert("Ошибка переименования: " + (data.error || "Неизвестная ошибка"));
                    }
                })
                .catch(err => {
                    console.error("[SIDEBAR] Rename error:", err);
                    alert("Ошибка сети при переименовании");
                });
            }
        });

        var delBtn = document.createElement("button");
        delBtn.className = "delete-btn";
        delBtn.textContent = "×";
        delBtn.addEventListener("click", function(e) { 
            e.stopPropagation(); 
            if(confirm("Удалить этот диалог?")) {
                fetch("/api/conversations/" + conv.id, { method: "DELETE" })
                .then(r => {
                    if (!r.ok) throw new Error("Network response was not ok");
                    deleteConv(conv.id);
                })
                .catch(err => {
                    console.error("[SIDEBAR] Delete error:", err);
                    alert("Ошибка сети при удалении");
                });
            }
        });
        
        div.appendChild(titleSpan);
        div.appendChild(delBtn);
        div.addEventListener("click", function() { selectConv(conv.id); });
        list.appendChild(div);
    });
}

function selectConv(id, updateHistory) {
    if (updateHistory === undefined) updateHistory = true;
    currentConvId = id;
    localStorage.setItem("current_conv_id", id);
    if (updateHistory) {
        var newUrl = new URL(window.location);
        newUrl.searchParams.set('conversation_id', id);
        window.history.pushState({convId: id}, '', newUrl);
    }
    var conv = conversations.find(function(c) { return c.id === id; });
    if (conv) {
        if (typeof window.changeModel === "function") {
            window.changeModel(conv.model || "aliceai-llm", true);
        }
    }
if (typeof window.loadServerConvSettings === "function") {
    window.loadServerConvSettings(id).then(function() {
        // Настройки для этого диалога загружены в кэш
    });
}

    if (typeof loadHistory === "function") loadHistory(id);
    if (typeof renderSidebar === "function") renderSidebar();
    var sidebar = document.getElementById("sidebar");
    var overlay = document.getElementById("overlay");
    if (sidebar) sidebar.classList.remove("open");
    if (overlay) overlay.classList.remove("visible");
}

function deleteConv(id) {
    conversations = conversations.filter(function(c) { return c.id !== id; });
    localStorage.setItem("conversations", JSON.stringify(conversations));
    localStorage.removeItem("messages_" + id);
    
    if (currentConvId === id) {
        currentConvId = conversations.length ? conversations[0].id : null;
        localStorage.setItem("current_conv_id", currentConvId);
        if (currentConvId) {
            selectConv(currentConvId);
        } else {
            var chatbox = document.getElementById("chatbox");
            if (chatbox) chatbox.innerHTML = '<div class="empty-state">Нажмите + Новый чат</div>';
            if (typeof window.changeModel === "function") {
                window.changeModel("aliceai-llm", true);
            }
        }
    }
    if (typeof renderSidebar === "function") renderSidebar();
}

window.addEventListener('popstate', function(event) {
    var params = new URLSearchParams(window.location.search);
    var convId = params.get('conversation_id');
    if (convId && convId !== currentConvId) {
        selectConv(convId, false);
    } else if (!convId && currentConvId) {
        currentConvId = null;
        localStorage.removeItem("current_conv_id");
        var chatbox = document.getElementById("chatbox");
        if (chatbox) chatbox.innerHTML = '<div class="empty-state">Нажмите + Новый чат</div>';
        if (typeof renderSidebar === "function") renderSidebar();
    }
});
