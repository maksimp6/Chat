(function () {
  "use strict";

  var mcpServersCache = [];
  var mcpFetchPromise = null;

  window.fetchMcpServers = function () {
    if (mcpFetchPromise) return mcpFetchPromise;
    mcpFetchPromise = window.LogosDispatcher.request("/api/mcp-servers")
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        mcpServersCache = (data && data.data) || [];
        mcpFetchPromise = null;
        return mcpServersCache;
      })
      .catch(function (e) {
        // A failed load is not an empty list: the caller has to show the error instead.
        console.warn("[MCP] Fetch failed:", (e && e.message) || e);
        mcpServersCache = [];
        mcpFetchPromise = null;
        throw e;
      });
    return mcpFetchPromise;
  };


  function closeMcpManagerModal(modal) {
    if (!modal) return;
    window.LogosCoreAPI.ui.modal.close(modal);
    var onClose = modal._onClose;
    modal._onClose = null;
    if (typeof onClose === "function") onClose();
  }

  window.LogosCoreAPI.ui.actions.register("mcp-manager.close", function (payload) {
    var modalId = payload && payload.params && payload.params.modal;
    closeMcpManagerModal(modalId ? document.getElementById(modalId) : null);
  });

  window.openMcpManagerModal = function (onClose) {
    var UI = window.SettingsUI;
    var CoreUI = window.LogosCoreAPI.ui;
    UI.injectModalStyles();

    var existing = document.getElementById("mcp-manager-modal");
    if (existing) existing.remove();

    var md = document.createElement("div");
    md.className = "settings-mcp-body";
    md.innerHTML =
      '<div style="font-size:12px;color:var(--m-muted,#666);margin-bottom:10px;">Настройки только для чтения: серверы показаны без изменения.</div>' +
      '<div id="mcp-mgr-list"></div>';

    var ov = CoreUI.modal.create({
      id: "mcp-manager-modal",
      title: "MCP Серверы",
      className: "mcp-manager-modal",
      contentClassName: "settings-mcp-modal-content",
      closeAction: "mcp-manager.close",
      body: md,
    });
    ov._onClose = onClose;
    var modalContent = ov.querySelector(".modal-content");
    modalContent.style.cssText =
      "background:var(--m-bg,#fff);border-radius:12px;padding:24px;box-shadow:0 8px 32px var(--m-shadow,rgba(0,0,0,0.3));color:var(--m-text,#222);";
    (document.querySelector(".logos-pro-app") || document.body).appendChild(ov);
    CoreUI.modal.open(ov);

    ov.addEventListener("click", function (e) {
      if (e.target === ov) closeMcpManagerModal(ov);
    });

    function renderList(servers) {
      var list = document.getElementById("mcp-mgr-list");
      if (!servers || servers.length === 0) {
        list.innerHTML =
          '<div style="padding:20px;text-align:center;color:var(--m-muted,#999);">Нет MCP серверов.</div>';
        return;
      }
      list.innerHTML = servers
        .map(function (srv) {
          var connInfo = srv.connector_id ? srv.connector_id : srv.server_url || "(нет URL)";
          return (
            '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;min-width:0;padding:10px;border:1px solid var(--m-section-border,#eee);border-radius:6px;margin-bottom:8px;background:var(--m-card,#fff);">' +
            '<div style="min-width:0;overflow-wrap:anywhere"><div style="font-weight:bold;font-size:14px;color:var(--m-text,#222);">' +
            UI.escapeHtml(srv.name) +
            "</div>" +
            '<div style="font-size:12px;color:var(--m-muted,#666);">' +
            UI.escapeHtml(connInfo) +
            "</div></div>" +
            "</div>"
          );
        })
        .join("");
    }

    function renderLoadError() {
      var list = document.getElementById("mcp-mgr-list");
      if (!list) return;
      list.replaceChildren();
      var message = document.createElement("p");
      message.textContent = "Не удалось загрузить MCP серверы. Проверьте соединение.";
      var retry = document.createElement("button");
      retry.type = "button";
      retry.className = "logos-btn";
      retry.textContent = "Повторить";
      retry.addEventListener("click", refreshList);
      list.append(message, retry);
    }

    function refreshList() {
      window.fetchMcpServers().then(renderList, renderLoadError);
    }

    refreshList();
  };

})();
