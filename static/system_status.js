"use strict";

(function () {
  var severity = {
    ready: 0,
    running: 1,
    waiting: 1,
    degraded: 2,
    blocked: 3,
    revoked: 4,
    error: 4,
    stopped: 1,
  };

  var labels = {
    ready: "Готов",
    running: "Работает",
    waiting: "Ожидание",
    degraded: "Ограниченный режим",
    blocked: "Заблокировано",
    revoked: "Доступ отозван",
    error: "Ошибка",
    stopped: "Остановлено",
  };

  function getRoot() {
    return document.getElementById("logos-system-status");
  }

  function statusIcon(state) {
    if (state === "error" || state === "revoked" || state === "blocked") return "✕";
    if (state === "degraded") return "!";
    if (state === "running" || state === "waiting") return "◐";
    return "✓";
  }

  function overallState(snapshot) {
    var state = "ready";
    Object.keys(snapshot).forEach(function (moduleId) {
      var current = snapshot[moduleId];
      if (severity[current.status] > severity[state]) state = current.status;
    });
    return state;
  }

  function render(snapshot) {
    var root = getRoot();
    var button = document.getElementById("system-status-btn");
    if (!root || !button) return;

    var state = overallState(snapshot);
    root.dataset.state = state;
    button.dataset.state = state;
    button.textContent = statusIcon(state);
    button.title = "Состояние системы: " + labels[state];
    button.setAttribute("aria-label", "Состояние системы: " + labels[state]);

    var list = root.querySelector(".logos-system-status-list");
    if (!list) return;

    list.innerHTML = "";
    Object.keys(snapshot)
      .sort()
      .forEach(function (moduleId) {
        var item = snapshot[moduleId];
        var row = document.createElement("div");
        row.className = "logos-system-status-row";
        row.dataset.status = item.status;

        var indicator = document.createElement("span");
        indicator.className = "logos-system-status-indicator";
        indicator.textContent = statusIcon(item.status);
        indicator.setAttribute("aria-hidden", "true");

        var text = document.createElement("div");
        text.className = "logos-system-status-text";

        var title = document.createElement("strong");
        title.textContent = moduleId;
        var detail = document.createElement("span");
        // A status the panel has no label for is shown as unknown, never as "undefined" (#1300).
        var label = labels[item.status] || "Неизвестное состояние";
        detail.textContent = label + (item.message ? ": " + item.message : "");

        text.appendChild(title);
        text.appendChild(detail);
        row.appendChild(indicator);
        row.appendChild(text);

        if (item.metadata && item.metadata.revocable === true && item.status !== "revoked") {
          var revokeButton = document.createElement("button");
          revokeButton.type = "button";
          revokeButton.className = "logos-btn logos-system-status-revoke";
          revokeButton.textContent = "Отозвать";
          revokeButton.addEventListener("click", function () {
            window.LogosCoreAPI.revocation.revoke("module", moduleId, "user_requested");
          });
          row.appendChild(revokeButton);
        }

        list.appendChild(row);
      });
  }

  function bind() {
    var button = document.getElementById("system-status-btn");
    var panel = getRoot();
    if (!button || !panel || button.dataset.statusBound === "true") return;

    button.dataset.statusBound = "true";
    var header = document.getElementById("header");
    var panels = window.LogosCoreAPI.ui.panel;

    function isOpen() {
      return !panel.hasAttribute("hidden");
    }

    // The popover starts at the header's real bottom edge. The header grows when its second row wraps on a phone
    // (#1718) and by the safe-area inset; with the fixed --header-h the dimmed layer lay over the lower icon rows, and
    // a tap on one of them only closed the popover.
    function place() {
      if (!header) return;
      var bottom = Math.max(0, Math.round(header.getBoundingClientRect().bottom));
      panel.style.setProperty("--status-panel-top", bottom + "px");
    }

    function close() {
      var hadFocus = panel.contains(document.activeElement);
      panel.setAttribute("hidden", "");
      button.setAttribute("aria-expanded", "false");
      panels.closed(panel);
      // Closed from inside (Escape, ×), focus goes back to the button instead of dropping to the page.
      if (hadFocus) button.focus();
    }

    function open() {
      place();
      panel.removeAttribute("hidden");
      button.setAttribute("aria-expanded", "true");
      // Registered, the popover closes when a window or another panel opens, and on Escape.
      panels.opened(panel, close);
    }

    window.LogosCoreAPI.ui.actions.register("system-status.toggle", function () {
      if (isOpen()) close();
      else open();
    });

    var closeButton = panel.querySelector(".logos-system-status-close");
    if (closeButton) closeButton.addEventListener("click", close);

    panel.addEventListener("click", function (event) {
      if (event.target === panel) close();
    });

    // Any other tap in the header (a window, the menu, a link, the theme) closes the popover before the control acts:
    // one panel is open at a time, and the tap still does what it was for.
    if (header) {
      header.addEventListener(
        "click",
        function (event) {
          if (isOpen() && !button.contains(event.target)) close();
        },
        true,
      );
    }

    window.addEventListener("resize", function () {
      if (isOpen()) place();
    });

    // A subscriber is told about one module at a time; the panel always lists every module.
    var unsubscribe = window.LogosCoreAPI.status.subscribe(function () {
      render(window.LogosCoreAPI.status.snapshot());
    });
    window.LogosCoreAPI.status.set("ui", "ready", "Приборная панель активна", { revocable: false });

    window.addEventListener("logos:revocation", function (event) {
      var detail = event.detail || {};
      if (detail.type === "module") {
        render(window.LogosCoreAPI.status.snapshot());
      }
    });

    window.addEventListener("beforeunload", unsubscribe, { once: true });
  }

  function init() {
    if (!window.LogosCoreAPI) {
      console.error("[System Status] Core API unavailable");
      return;
    }
    bind();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
