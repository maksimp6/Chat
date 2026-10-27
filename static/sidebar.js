function initSidebar() {
  if (document.documentElement.dataset.sidebarInitialized === "true") return;
  document.documentElement.dataset.sidebarInitialized = "true";
  var sidebar = document.getElementById("sidebar");
  var menuBtn = document.getElementById("menu-btn");
  var closeBtn = document.getElementById("close-sidebar-btn");
  var overlay = document.getElementById("overlay");
  var newChatBtn = document.getElementById("new-chat-btn");

  function openSidebar() {
    if (!sidebar) return;
    sidebar.classList.add("open");
    if (overlay) overlay.classList.add("visible");
    if (menuBtn) menuBtn.setAttribute("aria-expanded", "true");
  }
  function closeSidebar() {
    if (sidebar) sidebar.classList.remove("open");
    if (overlay) overlay.classList.remove("visible");
    if (menuBtn) menuBtn.setAttribute("aria-expanded", "false");
  }

  if (menuBtn) {
    menuBtn.setAttribute(
      "aria-expanded",
      sidebar ? (sidebar.classList.contains("open") ? "true" : "false") : "false",
    );
    menuBtn.setAttribute("aria-controls", "sidebar");
    window.LogosCoreAPI.ui.actions.register("sidebar.toggle", function () {
      if (!sidebar) return;
      sidebar.classList.contains("open") ? closeSidebar() : openSidebar();
    });
  }
  if (closeBtn) closeBtn.addEventListener("click", closeSidebar);
  if (overlay) overlay.addEventListener("click", closeSidebar);
  if (newChatBtn)
    newChatBtn.addEventListener("click", function () {
      window.isCreatingNewChat = true;
      if (typeof renderModelModal === "function") renderModelModal();
      var modal = document.getElementById("model-modal");
      if (modal) window.LogosCoreAPI.ui.modal.open(modal);
    });
  if (typeof renderSidebar === "function") renderSidebar();
  if (window.__logosSidebarHistoryBound !== true) {
    window.addEventListener("popstate", handleHistoryNavigation);
    window.__logosSidebarHistoryBound = true;
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initSidebar, { once: true });
} else {
  initSidebar();
}

function renderSidebar() {
  var list = document.getElementById("conv-list");
  if (!list) return;
  list.replaceChildren();
  var items = Array.isArray(conversations) ? conversations : [];
  if (!items.length) {
    var empty = document.createElement("div");
    empty.className = "conv-empty";
    empty.textContent = "Нет диалогов";
    list.appendChild(empty);
    return;
  }
  items.forEach(function (conv) {
    var div = document.createElement("div");
    div.className = "conv-item" + (conv.id === currentConvId ? " active" : "");

    var titleSpan = document.createElement("span");
    titleSpan.className = "conv-title";
    titleSpan.textContent = conv.title;

    // Сохраняет новое имя; при неудаче диалог остаётся с прежним именем.
    function saveTitle(name) {
      window.LogosDispatcher.request("/api/conversations/" + conv.id, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: name }),
      })
        .then((r) => {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then((data) => {
          if (data.status === "ok") {
            conv.title = name;
            localStorage.setItem("conversations", JSON.stringify(conversations));
            renderSidebar();
          } else {
            LogosToast.error("Не удалось переименовать диалог.");
          }
        })
        .catch((err) => {
          console.error("[SIDEBAR] Rename error:", err);
          LogosToast.error("Нет связи с сервером. Диалог не переименован.");
        });
    }

    // Поле редактирования на месте названия: Enter сохраняет, Escape отменяет, уход с поля сохраняет.
    function startRename() {
      var input = document.createElement("input");
      input.type = "text";
      input.className = "conv-rename-input";
      input.maxLength = 80;
      input.value = conv.title;
      input.setAttribute("aria-label", "Новое имя диалога");
      titleSpan.replaceWith(input);
      input.focus();
      input.select();
      var finished = false;
      function finish(save) {
        if (finished) return;
        finished = true;
        var name = window.LogosWikifunctions.trim(input.value);
        input.replaceWith(titleSpan);
        if (save && name && name !== conv.title) saveTitle(name);
      }
      input.addEventListener("keydown", function (e) {
        if (e.isComposing) return;
        if (e.key === "Enter") {
          e.preventDefault();
          finish(true);
        } else if (e.key === "Escape") {
          e.preventDefault();
          finish(false);
        }
      });
      input.addEventListener("blur", function () {
        finish(true);
      });
      // The editor sits inside the chat link: its clicks must neither select the chat nor follow the link.
      input.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
      });
    }

    // Двойной клик (мышь) и кнопка «✎» (сенсорный экран) открывают одно и то же поле.
    titleSpan.addEventListener("dblclick", function (e) {
      e.preventDefault();
      e.stopPropagation();
      startRename();
    });

    var renameBtn = document.createElement("button");
    renameBtn.type = "button";
    renameBtn.className = "logos-btn rename-btn";
    renameBtn.textContent = "✎";
    renameBtn.setAttribute("aria-label", "Переименовать диалог");
    renameBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      startRename();
    });

    var delBtn = document.createElement("button");
    delBtn.className = "logos-btn delete-btn";
    delBtn.textContent = "×";
    delBtn.setAttribute("aria-label", "Удалить диалог");
    delBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      if (confirm("Удалить этот диалог?")) {
        window.LogosDispatcher.request("/api/conversations/" + conv.id, { method: "DELETE" })
          .then((r) => {
            if (!r.ok) throw new Error("HTTP " + r.status);
            deleteConv(conv.id);
          })
          .catch((err) => {
            console.error("[SIDEBAR] Delete error:", err);
            LogosToast.error("Не удалось удалить диалог. Попробуйте ещё раз.");
          });
      }
    });

    var link = document.createElement("a");
    link.className = "conv-link";
    link.href = "?conversation_id=" + encodeURIComponent(conv.id);
    link.setAttribute("aria-current", conv.id === currentConvId ? "page" : "false");
    link.appendChild(titleSpan);
    div.appendChild(link);
    div.appendChild(renameBtn);
    div.appendChild(delBtn);
    div.addEventListener("click", function (e) {
      if (e.target === delBtn || e.target === renameBtn || e.target.closest(".conv-link")) return;
      selectConv(conv.id);
    });
    link.addEventListener("click", function (e) {
      e.preventDefault();
      // The second click of a double-click (rename) must not select and reload the conversation again.
      if (e.detail > 1) return;
      selectConv(conv.id);
    });
    list.appendChild(div);
  });
}

function selectConv(id, updateHistory) {
  if (updateHistory === undefined) updateHistory = true;
  currentConvId = id;
  localStorage.setItem("current_conv_id", id);
  window.dispatchEvent(new CustomEvent("logos:conversation-selected", { detail: { id: id } }));
  if (updateHistory) {
    var newUrl = new URL(window.location);
    newUrl.searchParams.set("conversation_id", id);
    window.history.pushState({ convId: id }, "", newUrl);
  }
  var conv = conversations.find(function (c) {
    return c.id === id;
  });
  if (conv) {
    if (typeof window.changeModel === "function") {
      window.changeModel(conv.model || "yandexgpt-5-pro", true);
    }
  }
  if (typeof window.loadServerConvSettings === "function") {
    window.loadServerConvSettings(id).catch(function (error) {
      console.warn("[SIDEBAR] Settings load failed:", error);
    });
  }

  var historyReady = typeof loadHistory === "function" ? loadHistory(id) : undefined;
  if (typeof renderSidebar === "function") renderSidebar();
  var sidebar = document.getElementById("sidebar");
  var overlay = document.getElementById("overlay");
  if (sidebar) sidebar.classList.remove("open");
  if (overlay) overlay.classList.remove("visible");
  var menuBtn = document.getElementById("menu-btn");
  if (menuBtn) menuBtn.setAttribute("aria-expanded", "false");
  return historyReady;
}

function deleteConv(id) {
  conversations = conversations.filter(function (c) {
    return c.id !== id;
  });
  localStorage.setItem("conversations", JSON.stringify(conversations));
  localStorage.removeItem("messages_" + id);

  if (currentConvId === id) {
    currentConvId = conversations.length ? conversations[0].id : null;
    if (currentConvId) {
      localStorage.setItem("current_conv_id", currentConvId);
      selectConv(currentConvId);
    } else {
      // Nothing is left to reopen: do not restore the deleted id on reload.
      localStorage.removeItem("current_conv_id");
      var newUrl = new URL(window.location);
      newUrl.searchParams.delete("conversation_id");
      window.history.replaceState(null, "", newUrl);
      var chatbox = document.getElementById("chatbox");
      if (chatbox) {
        chatbox.replaceChildren();
        var empty = document.createElement("div");
        empty.className = "empty-state";
        empty.textContent = "Нажмите + Новый чат";
        chatbox.appendChild(empty);
      }
      if (typeof window.changeModel === "function") {
        window.changeModel("yandexgpt-5-pro", true);
      }
    }
  }
  if (typeof renderSidebar === "function") renderSidebar();
}

function handleHistoryNavigation(event) {
  var params = new URLSearchParams(window.location.search);
  var convId = params.get("conversation_id");
  if (convId && convId !== currentConvId) {
    selectConv(convId, false);
  } else if (!convId && currentConvId) {
    currentConvId = null;
    localStorage.removeItem("current_conv_id");
    var chatbox = document.getElementById("chatbox");
    if (chatbox) {
      chatbox.replaceChildren();
      var empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "Нажмите + Новый чат";
      chatbox.appendChild(empty);
    }
    if (typeof renderSidebar === "function") renderSidebar();
  }
}
