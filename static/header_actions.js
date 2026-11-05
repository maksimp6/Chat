(function () {
  "use strict";

  function call(name, fallbackMessage) {
    return function () {
      if (typeof window[name] === "function") return window[name]();
      if (fallbackMessage) console.error(fallbackMessage);
    };
  }

  function registerHeaderActions() {
    if (window.__logosHeaderActionsBound === true) return;
    window.__logosHeaderActionsBound = true;

    var actions = window.LogosCoreAPI.ui.actions;
    actions.register("header.tools.open", call("openToolsModal"));
    actions.register(
      "header.ssh.open",
      call("openSshRuntimeModal", "[SSH Runtime] Modal script is unavailable"),
    );
    actions.register("header.mcp.open", call("openMcpManagerModal"));
    actions.register("header.settings.open", call("openSettingsModal"));
    actions.register("header.files.open", call("openFileManagerModal"));
    actions.register("header.treasury.open", call("openTreasuryPanel"));
    actions.register("header.dozzle.open", call("openDozzleLogs"));
    actions.register("header.departments.open", call("openDepartmentsModal"));
    actions.register("header.court.open", call("openCourtHall"));
    actions.register("header.group.open", call("openGroupChatModal"));
    actions.register("header.credentials.open", call("openProviderCredentialsModal"));
    // 🖥️ connects Logos to RDC: the OAuth connect of its «RDC — Desktop Commander» server, as «🖥 Подключить RDC» in
    // «Внешние MCP шлюзы» does (owner, 05.11.2026: a link to the RDC site did not connect Logos).
    actions.register("header.rdc.connect", function () {
      if (typeof window.startMcpOAuth !== "function" || typeof window.fetchMcpServers !== "function") {
        console.error("[RDC] The MCP script is unavailable");
        return;
      }
      window.startMcpOAuth(
        window.fetchMcpServers().then(function (servers) {
          var rdc = servers.filter(function (server) {
            return server && server.server_label === "rdc";
          })[0];
          if (!rdc) throw new Error("сервер RDC не найден");
          return rdc.id;
        }),
      );
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", registerHeaderActions, { once: true });
  } else {
    registerHeaderActions();
  }
})();
