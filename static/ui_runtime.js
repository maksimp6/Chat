"use strict";

(function () {
  function mount() {
    if (!window.LogosCoreAPI || !window.LogosCoreAPI.ui) {
      throw new Error("LogosCoreAPI.ui is required before UI runtime");
    }
    if (!document.body) throw new Error("document.body is required for UI runtime");
    window.LogosUIRuntime = Object.freeze({
      unmountClicks: window.LogosCoreAPI.ui.events.mountClicks(document.body),
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount, { once: true });
  } else {
    mount();
  }
})();
