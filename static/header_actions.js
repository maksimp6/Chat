(function () {
  "use strict";

  // Every opener shows its window at once and fills it when the data arrives. A tap whose window
  // script did not load is named in the console instead of doing nothing silently.
  function call(name) {
    return function () {
      if (typeof window[name] === "function") return window[name]();
      console.error("[HEADER] " + name + " is unavailable: its script did not load");
    };
  }

  function registerHeaderActions() {
    var actions = window.LogosCoreAPI.ui.actions;
    actions.register("header.tools.open", call("openToolsModal"));
    actions.register("header.ssh.open", call("openSshRuntimeModal"));
    actions.register("header.environments.open", call("openEnvironmentsModal"));
    actions.register("header.mcp.open", call("openMcpManagerModal"));
    actions.register("header.rdc.open", call("openRdcModal"));
    actions.register("header.logs.open", call("openLogsModal"));
    actions.register("header.settings.open", call("openSettingsModal"));
    actions.register("header.files.open", call("openFileManagerModal"));
    actions.register("header.treasury.open", call("openTreasuryPanel"));
    actions.register("header.departments.open", call("openDepartmentsModal"));
    actions.register("header.court.open", call("openCourtHall"));
    actions.register("header.group.open", call("openGroupChatModal"));
    actions.register("header.credentials.open", call("openProviderCredentialsModal"));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", registerHeaderActions, { once: true });
  } else {
    registerHeaderActions();
  }
})();
