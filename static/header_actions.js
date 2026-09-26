(function () {
  "use strict";

  function call(name, fallbackMessage) {
    return function () {
      if (typeof window[name] === "function") return window[name]();
      if (fallbackMessage) console.error(fallbackMessage);
    };
  }

  function registerHeaderActions() {
    if (window.__aliceHeaderActionsBound === true) return;
    window.__aliceHeaderActionsBound = true;

    var actions = window.AliceCoreAPI.ui.actions;
    actions.register("header.tools.open", call("openToolsModal"));
    actions.register("header.ssh.open", call("openSshRuntimeModal", "[SSH Runtime] Modal script is unavailable"));
    actions.register("header.mcp.open", call("openMcpManagerModal"));
    actions.register("header.settings.open", call("openSettingsModal"));
    actions.register("header.files.open", call("openFileManagerModal"));
    actions.register("header.treasury.open", call("openTreasuryPanel"));
    actions.register("header.dozzle.open", call("openDozzleLogs"));
    actions.register("header.departments.open", call("openDepartmentsModal"));
    actions.register("header.credentials.open", call("openProviderCredentialsModal"));
    actions.register("header.memory.open", call("openMemoryModal", "[Memory] Modal script is unavailable"));
    actions.register("header.update.open", function () {
      if (window.AliceAndroid) {
        window.AliceAndroid.openUpdater();
      } else {
        alert("Обновление APK доступно только в Android-приложении.");
      }
    });

    var upload = document.getElementById("upload-image-btn");
    if (upload && upload.dataset.bound !== "true") {
      upload.addEventListener("click", function () { window.uploadImage(); });
      upload.dataset.bound = "true";
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", registerHeaderActions, {once: true});
  } else {
    registerHeaderActions();
  }
})();
