(function () {
  function bindHeaderActions() {
    var bindings = [
      ["tools-btn", "click", function () { window.openToolsModal(); }],
      ["mcp-btn", "click", function () { window.openMcpManagerModal(); }],
      ["settings-btn", "click", function () { window.openSettingsModal(); }],
      ["file-manager-btn", "click", function () { window.openFileManagerModal(); }],
      ["treasury-btn", "click", function () { window.openTreasuryPanel(); }],
      ["dozzle-btn", "click", function () { window.openDozzleLogs(); }],
      ["departments-btn", "click", function () { window.openDepartmentsModal(); }],
      ["update-app-btn", "click", function () {
        if (window.AliceAndroid) {
          window.AliceAndroid.openUpdater();
        } else {
          alert("Обновление APK доступно только в Android-приложении.");
        }
      }],
      ["provider-credentials-btn", "click", function () {
        if (window.openProviderCredentialsModal) {
          window.openProviderCredentialsModal();
        }
      }],
      ["upload-image-btn", "click", function () { window.uploadImage(); }]
    ];

    bindings.forEach(function (binding) {
      var element = document.getElementById(binding[0]);
      if (!element || element.dataset.bound === "true") return;
      element.addEventListener(binding[1], binding[2]);
      element.dataset.bound = "true";
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindHeaderActions, { once: true });
  } else {
    bindHeaderActions();
  }
})();
