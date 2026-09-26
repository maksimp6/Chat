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
    var registrations = [
      ["header.tools.open", call("openToolsModal")],
      ["header.ssh.open", call("openSshRuntimeModal", "[SSH Runtime] Modal script is unavailable")],
      ["header.mcp.open", call("openMcpManagerModal")],
      ["header.settings.open", call("openSettingsModal")],
      ["header.files.open", call("openFileManagerModal")],
      ["header.treasury.open", call("openTreasuryPanel")],
      ["header.dozzle.open", call("openDozzleLogs")],
      ["header.departments.open", call("openDepartmentsModal")],
      ["header.credentials.open", call("openProviderCredentialsModal")],
      ["header.memory.open", call("openMemoryModal", "[Memory] Modal script is unavailable")],
      ["header.update.open", function () {
        if (window.AliceAndroid) {
          window.AliceAndroid.openUpdater();
        } else {
          alert("Обновление APK доступно только в Android-приложении.");
        }
      }]
    ];

    registrations.forEach(function (entry) {
      actions.register(entry[0], entry[1]);
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
