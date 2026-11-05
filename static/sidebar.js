function initSidebar() {
  if (document.documentElement.dataset.sidebarInitialized === "true") return;
  document.documentElement.dataset.sidebarInitialized = "true";
  var sidebar = document.getElementById("sidebar");
  var menuBtn = document.getElementById("menu-btn");
  var closeBtn = document.getElementById("close-sidebar-btn");
  var overlay = document.getElementById("overlay");
  var newChatBtn = document.getElementById("new-chat-btn");
  var search = document.getElementById("conv-search");

  function openSidebar() {
    if (!sidebar) return;
    sidebar.classList.add("open");
    if (overlay) overlay.classList.add("visible");
    if (menuBtn) menuBtn.setAttribute("aria-expanded", "true");
  }
  // focusMenu: Escape always hands focus back to the menu button.
  function closeSidebar(focusMenu) {
    hideSidebarPanel(focusMenu === true);
  }

  if (menuBtn) {
    menuBtn.setAttribute(
      "aria-expanded",
      sidebar ? (sidebar.classList.contains("open") ? "true" : "false") : "false",
    );
    menuBtn.setAttribute("aria-controls", "sidebar");
    window.LogosCoreAPI.ui.actions.register("sidebar.toggle", function (payload, event) {
      if (!sidebar) return;
      if (sidebar.classList.contains("open")) {
        closeSidebar();
        return;
      }
      openSidebar();
      // A key press (a click with no pointer detail) moves focus into the panel; a tap keeps the phone keyboard shut.
      if (search && event && event.type === "click" && event.detail === 0) search.focus();
    });
  }
  if (closeBtn)
    closeBtn.addEventListener("click", function () {
      closeSidebar();
    });
  if (overlay)
    overlay.addEventListener("click", function () {
      closeSidebar();
    });
  if (newChatBtn)
    newChatBtn.addEventListener("click", function () {
      createConversation();
    });
  if (search) {
    search.addEventListener("input", function () {
      renderSidebar();
    });
    // Escape clears a query first; on an empty field it closes the panel like anywhere else.
    search.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || e.isComposing || !search.value) return;
      e.preventDefault();
      search.value = "";
      renderSidebar();
    });
  }
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape" || e.isComposing || e.defaultPrevented) return;
    if (!sidebar || !sidebar.classList.contains("open")) return;
    // A window opened over the panel (the delete confirmation, a modal) takes its own Escape.
    if (document.querySelector("dialog[open], .modal.visible")) return;
    e.preventDefault();
    closeSidebar(true);
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

// Closes the panel. Focus left inside it would sit on a hidden control, so it goes back to the menu button.
function hideSidebarPanel(focusMenu) {
  var sidebar = document.getElementById("sidebar");
  var overlay = document.getElementById("overlay");
  var menuBtn = document.getElementById("menu-btn");
  var focusInside = !!sidebar && sidebar.contains(document.activeElement);
  if (sidebar) sidebar.classList.remove("open");
  if (overlay) overlay.classList.remove("visible");
  if (menuBtn) {
    menuBtn.setAttribute("aria-expanded", "false");
    if (focusMenu || focusInside) menuBtn.focus({ preventScroll: true });
  }
}

// A refused request carries the HTTP status and the server's error code, so the message can tell
// the server's answer from a missing connection.
function sidebarHttpError(response) {
  return response
    .json()
    .catch(function () {
      return {};
    })
    .then(function (body) {
      var error = new Error("HTTP " + response.status);
      error.status = response.status;
      error.code = body && typeof body.error === "string" ? body.error : "";
      throw error;
    });
}

// What happened and what to do, in Russian: the server's answer is named with its HTTP status and is not
// called a network error; only a request that got no answer is.
function sidebarFailureText(err, notDone) {
  var status = err && err.status;
  if (status === 404 && err.code === "conversation_not_found") {
    return "Диалог не найден на сервере: его уже удалили. Обновите страницу, чтобы обновить список диалогов.";
  }
  if (status >= 500) return "Ошибка на сервере (код " + status + "). " + notDone + " Повторите позже.";
  if (status) return "Сервер отклонил запрос (код " + status + "). " + notDone + " Обновите страницу и повторите.";
  if (err && (err.name === "TimeoutError" || err.name === "AbortError")) {
    return "Сервер не ответил вовремя, результат не подтверждён. Обновите страницу и проверьте список диалогов.";
  }
  if (err && err.name === "SyntaxError") {
    return "Ответ сервера не удалось прочитать. Обновите страницу и проверьте список диалогов.";
  }
  return "Нет связи с сервером. " + notDone + " Проверьте соединение и повторите.";
}

// Search ignores case and treats «ё» as «е», the way people type it.
function sidebarFoldText(text) {
  return String(text == null ? "" : text)
    .toLowerCase()
    .replace(/ё/g, "е");
}

function sidebarSearchQuery() {
  var input = document.getElementById("conv-search");
  return input ? sidebarFoldText(input.value.trim()) : "";
}

// Nominative month names: the group reads «Октябрь 2026», not the genitive a date formatter gives. A function, not
// a top-level array: initSidebar() runs above while this deferred script is still being read.
function sidebarMonthName(month) {
  return [
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
  ][month];
}

// The group of a dialog by its last activity (unix seconds). `order` sorts the groups, newest first.
function sidebarGroupOf(conv, now) {
  var seconds = Number(conv.updated_at) || Number(conv.created_at);
  // A dialog created in this tab has no server time yet: it is new.
  var time = seconds > 0 ? new Date(seconds * 1000) : now;
  function dayStart(daysBack) {
    return new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysBack);
  }
  if (time >= dayStart(0)) return { key: "today", label: "Сегодня", order: 4e6 };
  if (time >= dayStart(1)) return { key: "yesterday", label: "Вчера", order: 3e6 };
  if (time >= dayStart(7)) return { key: "week", label: "Последние 7 дней", order: 2e6 };
  if (time >= dayStart(30)) return { key: "month", label: "Последние 30 дней", order: 1e6 };
  var month = time.getMonth();
  var year = time.getFullYear();
  return {
    key: "m" + year + "-" + month,
    label: sidebarMonthName(month) + " " + year,
    order: year * 12 + month,
  };
}

function renderSidebar() {
  var list = document.getElementById("conv-list");
  if (!list) return;
  list.replaceChildren();
  var items = Array.isArray(conversations) ? conversations : [];
  if (!items.length) {
    var empty = document.createElement("div");
    empty.className = "conv-empty";
    if (conversationsLoadState === "failed") {
      empty.textContent = "Не удалось загрузить диалоги";
      var retryBtn = document.createElement("button");
      retryBtn.type = "button";
      retryBtn.className = "logos-btn";
      retryBtn.textContent = "Повторить";
      retryBtn.addEventListener("click", function () {
        if (typeof window.retryConversationsLoad === "function") window.retryConversationsLoad();
      });
      empty.appendChild(retryBtn);
    } else {
      empty.textContent = "Нет диалогов";
    }
    list.appendChild(empty);
    return;
  }

  var query = sidebarSearchQuery();
  var shown = query
    ? items.filter(function (conv) {
        return sidebarFoldText(conv.title).indexOf(query) !== -1;
      })
    : items;
  if (!shown.length) {
    var none = document.createElement("div");
    none.className = "conv-empty";
    none.setAttribute("role", "status");
    none.textContent = "Ничего не найдено";
    list.appendChild(none);
    return;
  }

  // The Court's dialog is always first, in its own group; the rest keep the list's order inside their group.
  var now = new Date();
  var groups = [];
  var byKey = {};
  shown.forEach(function (conv) {
    var group =
      conv.court === true ? { key: "pinned", label: "Закреплено", order: 1e9 } : sidebarGroupOf(conv, now);
    var bucket = byKey[group.key];
    if (!bucket) {
      bucket = byKey[group.key] = { label: group.label, order: group.order, items: [] };
      groups.push(bucket);
    }
    bucket.items.push(conv);
  });
  groups.sort(function (a, b) {
    return b.order - a.order;
  });
  groups.forEach(function (group, index) {
    var section = document.createElement("div");
    section.className = "conv-group";
    section.setAttribute("role", "group");
    var heading = document.createElement("div");
    heading.className = "conv-group-title";
    heading.id = "conv-group-" + index;
    heading.textContent = group.label;
    section.setAttribute("aria-labelledby", heading.id);
    section.appendChild(heading);
    group.items.forEach(function (conv) {
      section.appendChild(renderSidebarRow(conv));
    });
    list.appendChild(section);
  });
}

function renderSidebarRow(conv) {
  var div = document.createElement("div");
  div.className =
    "conv-item" + (conv.id === currentConvId ? " active" : "") + (conv.court === true ? " conv-court" : "");
  // The full name for a title that the row cuts short.
  div.title = conv.title || "";

  var titleSpan = document.createElement("span");
  titleSpan.className = "conv-title";
  titleSpan.textContent = conv.title;

  // Saves the new name; if that fails, the dialog keeps its old name.
  function saveTitle(name) {
    window.LogosDispatcher.request("/api/conversations/" + conv.id, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: name }),
    })
      .then((r) => {
        if (!r.ok) return sidebarHttpError(r);
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
        LogosToast.error(sidebarFailureText(err, "Диалог не переименован."));
      });
  }

  // An edit field in place of the name: Enter saves, Escape cancels, leaving the field saves.
  function startRename() {
    var input = document.createElement("input");
    input.type = "text";
    input.className = "conv-rename-input";
    input.maxLength = 80;
    input.value = conv.title;
    input.setAttribute("aria-label", "Новое имя диалога");
    titleSpan.replaceWith(input);
    // The row's buttons would cover the end of the field.
    div.classList.add("is-renaming");
    input.focus();
    input.select();
    var finished = false;
    function finish(save) {
      if (finished) return;
      finished = true;
      var name = input.value.trim();
      input.replaceWith(titleSpan);
      div.classList.remove("is-renaming");
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

  // A double click (mouse) and the pencil button (touch screen) open the same field.
  titleSpan.addEventListener("dblclick", function (e) {
    e.preventDefault();
    e.stopPropagation();
    startRename();
  });

  var renameBtn = document.createElement("button");
  renameBtn.type = "button";
  renameBtn.className = "logos-btn rename-btn";
  renameBtn.textContent = "✎";
  renameBtn.title = "Переименовать";
  renameBtn.setAttribute("aria-label", "Переименовать диалог");
  renameBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    startRename();
  });

  var delBtn = document.createElement("button");
  delBtn.type = "button";
  delBtn.className = "logos-btn delete-btn";
  delBtn.textContent = "×";
  delBtn.title = "Удалить";
  delBtn.setAttribute("aria-label", "Удалить диалог");
  delBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    window.LogosDialog.confirm("Удалить этот диалог?", { confirmText: "Удалить", danger: true }).then(
      function (confirmed) {
        if (!confirmed) return;
        window.LogosDispatcher.request("/api/conversations/" + conv.id, { method: "DELETE" })
          .then((r) => {
            if (!r.ok) return sidebarHttpError(r);
            deleteConv(conv.id);
          })
          .catch((err) => {
            console.error("[SIDEBAR] Delete error:", err);
            LogosToast.error(sidebarFailureText(err, "Диалог не удалён."));
          });
      },
    );
  });

  var link = document.createElement("a");
  link.className = "conv-link";
  link.href = "?conversation_id=" + encodeURIComponent(conv.id);
  link.setAttribute("aria-current", conv.id === currentConvId ? "page" : "false");
  link.appendChild(titleSpan);

  // The buttons lie over the row's right end, so the name keeps the full width.
  var actions = document.createElement("span");
  actions.className = "conv-actions";
  actions.appendChild(renameBtn);
  if (conv.court !== true) actions.appendChild(delBtn);

  div.appendChild(link);
  div.appendChild(actions);
  div.addEventListener("click", function (e) {
    if (e.target.closest(".conv-actions, .conv-link")) return;
    selectConv(conv.id);
  });
  link.addEventListener("click", function (e) {
    e.preventDefault();
    // The second click of a double-click (rename) must not select and reload the conversation again.
    if (e.detail > 1) return;
    selectConv(conv.id);
  });
  return div;
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
      window.changeModel(conv.model || "ollama-qwen3-4b", true);
    }
  }
  if (typeof window.loadServerConvSettings === "function") {
    window.loadServerConvSettings(id).catch(function (error) {
      console.warn("[SIDEBAR] Settings load failed:", error);
    });
  }

  // Read before the list is redrawn: the redraw drops the focused row.
  var sidebar = document.getElementById("sidebar");
  var focusInSidebar = !!sidebar && sidebar.contains(document.activeElement);
  var historyReady = typeof loadHistory === "function" ? loadHistory(id) : undefined;
  // A query left in the field would keep the list filtered on the next opening and could hide the chat just opened.
  var search = document.getElementById("conv-search");
  if (search && search.value) search.value = "";
  if (typeof renderSidebar === "function") renderSidebar();
  hideSidebarPanel(focusInSidebar);
  return historyReady;
}

function deleteConv(id) {
  conversations = conversations.filter(function (c) {
    return c.id !== id;
  });
  localStorage.setItem("conversations", JSON.stringify(conversations));
  localStorage.removeItem("messages_" + id);

  if (currentConvId === id) {
    // The next dialog opens, never the Court's own.
    var next = conversations.find(function (c) {
      return c.court !== true;
    });
    currentConvId = next ? next.id : null;
    if (currentConvId) {
      localStorage.setItem("current_conv_id", currentConvId);
      selectConv(currentConvId);
    } else {
      // Nothing is left to reopen: do not restore the deleted id on reload.
      localStorage.removeItem("current_conv_id");
      var newUrl = new URL(window.location);
      newUrl.searchParams.delete("conversation_id");
      window.history.replaceState(null, "", newUrl);
      showSidebarEmptyChat();
      if (typeof window.changeModel === "function") {
        window.changeModel("ollama-qwen3-4b", true);
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
    showSidebarEmptyChat();
    if (typeof renderSidebar === "function") renderSidebar();
  }
}

// The same empty screen chat.js draws (the «Л» mark, «Логос», «Начните диалог»); the plain line is only for a page
// where chat.js did not load.
function showSidebarEmptyChat() {
  var chatbox = document.getElementById("chatbox");
  if (!chatbox) return;
  chatbox.replaceChildren();
  if (typeof showEmptyChat === "function") {
    showEmptyChat(chatbox);
    return;
  }
  var empty = document.createElement("div");
  empty.className = "empty-state";
  empty.textContent = "Нажмите «Новый чат»";
  chatbox.appendChild(empty);
}
