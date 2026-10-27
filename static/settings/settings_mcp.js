(function () {
  "use strict";

  var mcpServersCache = [];
  var mcpFetchPromise = null;

  window.fetchMcpServers = function () {
    if (mcpFetchPromise) return mcpFetchPromise;
    mcpFetchPromise = window.LogosDispatcher.request("/api/mcp-servers")
      .then(function (r) {
        if (!r.ok) {
          console.warn("[MCP] Server returned " + r.status);
          return { data: [] };
        }
        return r.json();
      })
      .then(function (data) {
        mcpServersCache = (data && data.data) || [];
        mcpFetchPromise = null;
        return mcpServersCache;
      })
      .catch(function (e) {
        console.warn("[MCP] Fetch failed:", e.message || e);
        mcpServersCache = [];
        mcpFetchPromise = null;
        return [];
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
      '<button class="logos-btn settings-contract-btn" id="mcp-mgr-add">+ Добавить сервер</button>' +
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
          '<div style="padding:20px;text-align:center;color:var(--m-muted,#999);">Нет MCP серверов. Добавьте первый.</div>';
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
            '<div style="display:flex;gap:6px;">' +
            '<button class="logos-btn mcp-edit-btn settings-contract-btn" data-id="' +
            UI.escapeHtml(srv.id) +
            '">Изменить</button>' +
            '<button class="logos-btn mcp-del-btn settings-contract-btn" data-id="' +
            UI.escapeHtml(srv.id) +
            '">Удалить</button>' +
            "</div></div>"
          );
        })
        .join("");

      list.querySelectorAll(".mcp-edit-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var sid = this.getAttribute("data-id");
          var srv = servers.find(function (s) {
            return s.id === sid;
          });
          if (srv) openMcpEditor(srv);
        });
      });

      list.querySelectorAll(".mcp-del-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var sid = this.getAttribute("data-id");
          if (!confirm("Удалить MCP сервер?")) return;
          window.LogosDispatcher.request("/api/mcp-servers/" + sid, { method: "DELETE" })
            .then(function (r) {
              if (!r.ok) throw new Error("HTTP " + r.status);
              refreshList();
            })
            .catch(function (e) {
              LogosToast.error("Не удалось выполнить действие. Попробуйте ещё раз.");
            });
        });
      });
    }

    function refreshList() {
      window.fetchMcpServers().then(renderList);
    }

    var editorEl = null;

    function openMcpEditor(srv) {
      if (editorEl) editorEl.remove();
      var isNew = !srv;
      srv = srv || {};

      // Stored secrets are not sent to the browser; the field only says that one is saved.
      var SAVED_HINT = "сохранён — оставьте пустым, чтобы не менять";

      var CONNECTOR_CHOICES = [
        ["", "(нет — использовать server_url)"],
        ["termux_api", "📱 Termux API (Android Hardware)"],
        ["local_git", "📁 Local Git"],
        ["connector_dropbox", "Dropbox"],
        ["connector_gmail", "Gmail"],
        ["connector_googlecalendar", "Google Calendar"],
        ["connector_googledrive", "Google Drive"],
        ["connector_microsoftteams", "Microsoft Teams"],
        ["connector_outlookcalendar", "Outlook Calendar"],
        ["connector_outlookemail", "Outlook Email"],
        ["connector_sharepoint", "SharePoint"],
      ];

      function connSelected(val) {
        return CONNECTOR_CHOICES.map(function (choice) {
          var selected = choice[0] === val ? " selected" : "";
          return (
            '<option value="' + UI.escapeHtml(choice[0]) + '"' + selected + ">" +
            UI.escapeHtml(choice[1]) + "</option>"
          );
        }).join("");
      }

      var e = document.createElement("div");
      e.style.cssText =
        "border:1px solid var(--m-accent,#4a90d9);border-radius:8px;padding:16px;margin-top:12px;background:var(--m-editor,#f8f9fa);";

      e.innerHTML =
        '<h3 style="margin:0 0 10px;font-size:15px;color:var(--m-text,#222);">' +
        (isNew ? "Новый MCP сервер" : "Редактирование") +
        "</h3>" +
        '<div style="margin-bottom:8px;">' +
        UI.lbl("Название") +
        UI.inp("mcp-ed-name", "text", srv.name || "", {"style": "width:100%;"}) +
        "</div>" +
        '<div style="margin-bottom:8px;">' +
        UI.lbl("Connector ID") +
        UI.sel("mcp-ed-connector", connSelected(srv.connector_id || "")) +
        "</div>" +
        '<div style="margin-bottom:8px;">' +
        UI.lbl("Server URL") +
        UI.inp(
          "mcp-ed-url",
          "text",
          srv.server_url || "",
          {"style": "width:100%;", "placeholder": "https://mcp.example.com/sse"},
        ) +
        "</div>" +
        UI.gap2(
          "<div>" +
            UI.lbl("Server Label") +
            UI.inp("mcp-ed-label", "text", srv.server_label || "", {"style": "width:100%;"}) +
            "</div>",
          "<div>" +
            UI.lbl("Authorization") +
            UI.inp(
              "mcp-ed-auth",
              "password",
              "",
              { style: "width:100%;", placeholder: srv.has_authorization ? SAVED_HINT : "" },
            ) +
            "</div>",
        ) +
        '<div style="margin-bottom:8px;">' +
        UI.lbl("Description") +
        UI.inp("mcp-ed-desc", "text", srv.server_description || "", {"style": "width:100%;"}) +
        "</div>" +
        '<div style="margin-bottom:8px;">' +
        UI.lbl("Headers (JSON)") +
        '<textarea id="mcp-ed-headers" style="width:100%;min-height:40px;font-family:monospace;font-size:11px;padding:6px;border:1px solid var(--m-border,#ddd);border-radius:4px;background:var(--m-input-bg,#f5f5f5);color:var(--m-input-text,#222);box-sizing:border-box;" placeholder="' +
        UI.escapeHtml(srv.has_headers ? SAVED_HINT : '{"X-Key":"val"}') +
        '"></textarea></div>' +
        '<div style="margin-bottom:8px;">' +
        UI.lbl("Allowed Tools") +
        UI.inp("mcp-ed-allowed", "text", srv.allowed_tools || "", {"style": "width:100%;"}) +
        "</div>" +
        '<div style="margin-bottom:4px;">' +
        UI.chk("mcp-ed-allowed-ro", srv.allowed_tools_read_only || false, "Read-only filter") +
        "</div>" +
        '<div style="margin-bottom:8px;">' +
        UI.chk("mcp-ed-defer", srv.defer_loading || false, "Defer Loading") +
        "</div>" +
        '<div style="display:flex;gap:8px;margin-top:10px;">' +
        UI.btn("mcp-ed-save", isNew ? "Создать" : "Сохранить", "accent") +
        UI.btn("mcp-ed-cancel", "Отмена") +
        "</div>";

      document.getElementById("mcp-mgr-list").insertAdjacentElement("beforebegin", e);
      editorEl = e;

      document.getElementById("mcp-ed-cancel").addEventListener("click", function () {
        editorEl.remove();
        editorEl = null;
      });

      document.getElementById("mcp-ed-save").addEventListener("click", function () {
        var saveBtn = this;
        var payload = {
          name: window.LogosWikifunctions.trim(document.getElementById("mcp-ed-name").value),
          connector_id: document.getElementById("mcp-ed-connector").value,
          server_url: window.LogosWikifunctions.trim(document.getElementById("mcp-ed-url").value),
          server_label: window.LogosWikifunctions.trim(document.getElementById("mcp-ed-label").value),
          authorization: window.LogosWikifunctions.trim(document.getElementById("mcp-ed-auth").value),
          server_description: window.LogosWikifunctions.trim(document.getElementById("mcp-ed-desc").value),
          headers: window.LogosWikifunctions.trim(document.getElementById("mcp-ed-headers").value),
          allowed_tools: window.LogosWikifunctions.trim(document.getElementById("mcp-ed-allowed").value),
          allowed_tools_read_only: document.getElementById("mcp-ed-allowed-ro").checked,
          require_approval: "never",
          defer_loading: document.getElementById("mcp-ed-defer").checked,
        };
        // An empty secret field keeps the stored value, so it is not sent at all.
        if (!payload.authorization) delete payload.authorization;
        if (!payload.headers) delete payload.headers;
        if (!payload.name || (!payload.server_url && !payload.connector_id)) {
          LogosToast.error("Заполните название и URL/Connector");
          return;
        }
        var method = isNew ? "POST" : "PUT";
        var url = isNew ? "/api/mcp-servers" : "/api/mcp-servers/" + srv.id;
        saveBtn.disabled = true;
        window.LogosDispatcher.request(url, {
          method: method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
          .then(function (r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
          })
          .then(function () {
            if (editorEl === e) {
              e.remove();
              editorEl = null;
            }
            refreshList();
          })
          .catch(function (e) {
            saveBtn.disabled = false;
            LogosToast.error("Не удалось выполнить действие. Попробуйте ещё раз.");
          });
      });
    }

    document.getElementById("mcp-mgr-add").addEventListener("click", function () {
      openMcpEditor(null);
    });
    refreshList();
  };

})();
