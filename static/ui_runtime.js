"use strict";

(function () {
  function mount() {
    if (!window.AliceCoreAPI || !window.AliceCoreAPI.ui) {
      throw new Error("AliceCoreAPI.ui is required before UI runtime");
    }
    if (!document.body) throw new Error("document.body is required for UI runtime");
    window.AliceUIRuntime = Object.freeze({
      unmountClicks: window.AliceCoreAPI.ui.events.mountClicks(document.body),
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount, { once: true });
  } else {
    mount();
  }
})();
