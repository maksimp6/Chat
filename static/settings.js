(function () {
  "use strict";
  console.log("[SETTINGS] v7 — modules loaded, per-dialog, dark theme");

  document.addEventListener("DOMContentLoaded", function () {
    if (window.SettingsUI && typeof window.SettingsUI.injectModalStyles === "function") {
      window.SettingsUI.injectModalStyles();
    }
  });
})();
