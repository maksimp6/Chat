(function () {
  "use strict";
  console.log("[SETTINGS] v7 — modules loaded, per-dialog, dark theme");

  document.addEventListener("DOMContentLoaded", function () {
    // Инжект CSS-переменных для тёмной/светлой темы
    if (window.SettingsUI && typeof window.SettingsUI.injectModalStyles === "function") {
      window.SettingsUI.injectModalStyles();
    }

    // Привязка кнопки настроек к открытию модалки
    var btn = document.getElementById("settings-btn");
    if (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        if (typeof window.openSettingsModal === "function") window.openSettingsModal();
      });
    }
  });
})();
