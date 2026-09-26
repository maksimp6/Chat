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
    return document.getElementById("alice-system-status");
  }

  function statusIcon(state) {
    if (state === "error" || state === "revoked" || state === "blocked") return "●";
    if (state === "degraded") return "●";
    if (state === "running" || state === "waiting") return "◐";
    return "●";
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

    var list = root.querySelector(".alice-system-status-list");
    if (!list) return;

    list.innerHTML = "";
    Object.keys(snapshot)
      .sort()
      .forEach(function (moduleId) {
        var item = snapshot[moduleId];
        var row = document.createElement("div");
        row.className = "alice-system-status-row";
        row.dataset.status = item.status;

        var indicator = document.createElement("span");
        indicator.className = "alice-system-status-indicator";
        indicator.textContent = statusIcon(item.status);
        indicator.setAttribute("aria-hidden", "true");

        var text = document.createElement("div");
        text.className = "alice-system-status-text";

        var title = document.createElement("strong");
        title.textContent = moduleId;
        var detail = document.createElement("span");
        detail.textContent = labels[item.status] + (item.message ? ": " + item.message : "");

        text.appendChild(title);
        text.appendChild(detail);
        row.appendChild(indicator);
        row.appendChild(text);

        if (item.metadata && item.metadata.revocable === true && item.status !== "revoked") {
          var revokeButton = document.createElement("button");
          revokeButton.type = "button";
          revokeButton.className = "alice-btn alice-system-status-revoke";
          revokeButton.textContent = "Отозвать";
          revokeButton.addEventListener("click", function () {
            window.AliceCoreAPI.revocation.revoke("module", moduleId, "user_requested");
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
    window.AliceCoreAPI.ui.actions.register("system-status.toggle", function () {
      var hidden = panel.hasAttribute("hidden");
      if (hidden) panel.removeAttribute("hidden");
      else panel.setAttribute("hidden", "");
      button.setAttribute("aria-expanded", hidden ? "true" : "false");
    });

    var closeButton = panel.querySelector(".alice-system-status-close");
    if (closeButton) {
      closeButton.addEventListener("click", function () {
        panel.setAttribute("hidden", "");
        button.setAttribute("aria-expanded", "false");
      });
    }

    panel.addEventListener("click", function (event) {
      if (event.target === panel) {
        panel.setAttribute("hidden", "");
        button.setAttribute("aria-expanded", "false");
      }
    });

    var unsubscribe = window.AliceCoreAPI.status.subscribe(render);
    window.AliceCoreAPI.status.set("ui", "ready", "Приборная панель активна", { revocable: false });

    window.addEventListener("alice:revocation", function (event) {
      var detail = event.detail || {};
      if (detail.type === "module") {
        render(window.AliceCoreAPI.status.snapshot());
      }
    });

    window.addEventListener("beforeunload", unsubscribe, { once: true });
  }

  function init() {
    if (!window.AliceCoreAPI) {
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
