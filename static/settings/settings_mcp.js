(function () {
  "use strict";

  var mcpServersCache = [];
  var mcpFetchPromise = null;

  window.fetchMcpServers = function () {
    if (mcpFetchPromise) return mcpFetchPromise;
    mcpFetchPromise = window.LogosDispatcher.request("/api/mcp-servers")
      .then(function (r) {
        if (!r.ok) return window.LogosHttpError(r).then(function (e) { throw e; });
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

  // The OAuth connect of one MCP server. The window for the provider's sign-in opens now, inside the tap, so the browser
  // lets it open, and goes to the sign-in page once the server has started the connect; with that window blocked, this
  // page itself goes there.
  window.startMcpOAuth = function (serverId) {
    var popup = window.open("", "mcp_oauth", "width=520,height=680");
    var redirectUri = window.location.origin + "/api/mcp/oauth/callback";
    return window.LogosDispatcher.request("/api/mcp-servers/" + encodeURIComponent(serverId) + "/oauth/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ redirect_uri: redirectUri }),
    })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok || !body.authorize_url) {
            throw new Error((body && (body.message || body.error)) || "HTTP " + r.status);
          }
          return body.authorize_url;
        });
      })
      .then(function (url) {
        if (popup && !popup.closed) popup.location = url;
        else window.location = url;
      })
      .catch(function (e) {
        if (popup && !popup.closed) popup.close();
        LogosToast.error("Не удалось начать OAuth: " + (e.message || e));
      });
  };


  // The RDC window: whether Logos is connected to RDC, and the button that connects it (owner, 05.11.2026: a link to the
  // RDC site did not connect Logos). The state is read again when the sign-in tab or popup hands the page back.
  window.openRdcModal = function () {
    var CoreUI = window.LogosCoreAPI.ui;
    var existing = document.getElementById("rdc-modal");
    if (existing) existing.remove();

    var body = document.createElement("div");
    var state = document.createElement("p");
    state.className = "rdc-state";
    state.setAttribute("role", "status");
    state.textContent = "Загрузка…";
    var address = document.createElement("p");
    address.className = "rdc-address";
    var connect = document.createElement("button");
    connect.type = "button";
    connect.className = "logos-btn btn-primary";
    connect.hidden = true;
    body.appendChild(state);
    body.appendChild(address);
    body.appendChild(connect);

    var modal = CoreUI.modal.create({ id: "rdc-modal", title: "RDC", body: body });
    (document.querySelector(".logos-pro-app") || document.body).appendChild(modal);
    CoreUI.modal.open(modal);

    var serverId = null;

    function load() {
      window
        .fetchMcpServers()
        .then(function (servers) {
          var rdc = servers.filter(function (server) {
            return server && server.server_label === "rdc";
          })[0];
          if (!rdc) throw new Error("сервер RDC не найден");
          serverId = rdc.id;
          state.classList.remove("logos-error-note");
          state.textContent = rdc.has_authorization ? "Логос подключён к RDC." : "Логос не подключён к RDC.";
          address.textContent = rdc.server_url || "";
          connect.textContent = rdc.has_authorization ? "Подключить заново" : "Подключить";
          connect.hidden = false;
        })
        .catch(function (error) {
          state.classList.add("logos-error-note");
          state.textContent = "Не удалось проверить RDC: " + ((error && error.message) || error);
        });
    }

    connect.addEventListener("click", function () {
      connect.disabled = true;
      window.startMcpOAuth(serverId).then(function () {
        connect.disabled = false;
      });
    });

    function onReturn() {
      if (!document.hidden) load();
    }
    document.addEventListener("visibilitychange", onReturn);
    window.addEventListener("focus", onReturn);
    modal.addEventListener("close", function () {
      document.removeEventListener("visibilitychange", onReturn);
      window.removeEventListener("focus", onReturn);
    });

    load();
  };

  window.openMcpManagerModal = function () {
    var UI = window.SettingsUI;
    var CoreUI = window.LogosCoreAPI.ui;

    var existing = document.getElementById("mcp-manager-modal");
    if (existing) existing.remove();

    var md = document.createElement("div");
    md.className = "settings-mcp-body";
    md.innerHTML =
      '<div class="mcp-manager-note">Настройки только для чтения: серверы показаны без изменения.</div>' +
      '<div id="mcp-mgr-list"></div>';

    var ov = CoreUI.modal.create({
      id: "mcp-manager-modal",
      title: "MCP серверы",
      className: "mcp-manager-modal",
      contentClassName: "settings-mcp-modal-content",
      body: md,
    });
    // The window wears the shared modal shell (static/style.css), so no look is set inline here.
    (document.querySelector(".logos-pro-app") || document.body).appendChild(ov);
    CoreUI.modal.open(ov);

    function renderList(servers) {
      var list = document.getElementById("mcp-mgr-list");
      if (!servers || servers.length === 0) {
        list.innerHTML = '<div class="mcp-manager-empty">Нет MCP серверов.</div>';
        return;
      }
      list.innerHTML = servers
        .map(function (srv) {
          var connInfo = srv.connector_id ? srv.connector_id : srv.server_url || "(нет URL)";
          return (
            '<div class="mcp-server-row">' +
            '<div class="mcp-server-info"><div class="mcp-server-name">' +
            UI.escapeHtml(srv.name) +
            "</div>" +
            '<div class="mcp-server-meta">' +
            UI.escapeHtml(connInfo) +
            "</div></div>" +
            (srv.server_url
              ? '<div class="mcp-server-actions"><button type="button" class="logos-btn mcp-oauth-btn" data-id="' +
                UI.escapeHtml(srv.id) +
                '">' +
                (srv.server_label === "rdc" ? "🖥 Подключить RDC" : "⚡ OAuth") +
                "</button></div>"
              : "") +
            "</div>"
          );
        })
        .join("");

      list.querySelectorAll(".mcp-oauth-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var self = this;
          self.disabled = true;
          window.startMcpOAuth(this.getAttribute("data-id")).then(function () {
            self.disabled = false;
          });
        });
      });
    }

    function renderLoadError() {
      var list = document.getElementById("mcp-mgr-list");
      if (!list) return;
      list.replaceChildren();
      // The shared calm note, with the retry under its text.
      var message = document.createElement("div");
      message.className = "logos-error-note";
      message.textContent = "Не удалось загрузить MCP серверы. Проверьте соединение.";
      var retry = document.createElement("button");
      retry.type = "button";
      retry.className = "logos-btn";
      retry.textContent = "Повторить";
      retry.addEventListener("click", refreshList);
      message.appendChild(retry);
      list.append(message);
    }

    function refreshList() {
      window.fetchMcpServers().then(renderList, renderLoadError);
    }

    refreshList();
  };

})();
