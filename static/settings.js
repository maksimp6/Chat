(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    if (window.SettingsUI && typeof window.SettingsUI.injectModalStyles === "function") {
      window.SettingsUI.injectModalStyles();
    }
  });
})();
