(function () {
  "use strict";

  function byId(id) {
    return document.getElementById(id);
  }

  // Errors stay visible in the panel; the console alone is not enough for the user.
  function reportStatus(message) {
    var status = byId("memoryStatus");
    if (status) status.textContent = message || "";
  }

  var CATEGORY_LABELS = { general: "общее" };

  // Config controls take part in a save only after the stored settings were read. Otherwise a
  // save would send values the server never confirmed and overwrite them.
  var configLoaded = false;

  function setConfigControlsReady(ready) {
    configLoaded = ready;
    ["memEnabled", "memLimit"].forEach(function (id) {
      var control = byId(id);
      if (control) control.disabled = !ready;
    });
  }

  window.loadMemoryData = async function () {
    // Old facts stay out of view while the request runs (пункт 51).
    var pending = byId("memoryFactsList");
    if (pending) pending.textContent = "Загрузка…";
    setConfigControlsReady(false);
    try {
      var response = await window.LogosDispatcher.request("/api/memory/manage");
      if (!response.ok) throw new Error("HTTP " + response.status);
      var data = await response.json();
      var enabled = byId("memEnabled");
      var limit = byId("memLimit");
      var count = byId("memCount");
      var list = byId("memoryFactsList");
      var facts = Array.isArray(data.facts) ? data.facts : [];
      var config = data.config || {};

      if (enabled) enabled.checked = Boolean(config.enabled);
      // A stored 0 is a real limit (no facts in context), so only a missing value falls back.
      if (limit) {
        limit.value = String(
          Number.isInteger(config.max_context_facts) ? config.max_context_facts : 15,
        );
      }
      setConfigControlsReady(true);
      if (count) count.textContent = facts.length;
      reportStatus("");
      if (!list) return;

      list.replaceChildren();
      if (facts.length === 0) {
        var empty = document.createElement("p");
        empty.className = "memory-empty";
        empty.textContent = "Память пуста.";
        list.appendChild(empty);
        return;
      }

      facts.forEach(function (fact) {
        var item = document.createElement("div");
        item.className = "memory-fact";
        var category = document.createElement("span");
        category.className = "memory-fact-category";
        category.textContent =
          "[" + (CATEGORY_LABELS[fact.category] || String(fact.category || "общее")) + "]";
        item.appendChild(category);
        item.appendChild(document.createTextNode(" " + String(fact.fact || "")));
        list.appendChild(item);
      });
    } catch (error) {
      console.error("[Memory] Failed to load memory:", error);
      setConfigControlsReady(false);
      var failed = byId("memoryFactsList");
      if (failed) failed.replaceChildren();
      reportStatus("Не удалось загрузить память.");
    }
  };

  window.updateMemoryConfig = async function (event) {
    var control = event && event.target;
    if (!configLoaded || !control) return;
    // Send only the control the user changed; the other stored settings stay as they are.
    var body = {};
    if (control.id === "memEnabled") {
      body.enabled = Boolean(control.checked);
    } else if (control.id === "memLimit") {
      var maxFacts = parseInt(control.value, 10);
      if (Number.isNaN(maxFacts)) {
        // An empty limit is not a value to save; show the stored one again.
        await window.loadMemoryData();
        return;
      }
      body.max_context_facts = maxFacts;
    } else {
      return;
    }
    try {
      var response = await window.LogosDispatcher.request("/api/memory/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
      await window.loadMemoryData();
    } catch (error) {
      console.error("[Memory] Failed to update config:", error);
      // Re-read the server state so the controls show what was actually saved.
      await window.loadMemoryData();
      reportStatus("Не удалось сохранить настройки памяти.");
    }
  };

  window.clearMemory = async function (category) {
    var confirmed = await window.LogosDialog.confirm("Очистить память?", {
      confirmText: "Очистить",
      danger: true,
    });
    if (!confirmed) return;
    try {
      var response = await window.LogosDispatcher.request("/api/memory/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category: category }),
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
      await window.loadMemoryData();
    } catch (error) {
      console.error("[Memory] Failed to clear memory:", error);
      reportStatus("Не удалось очистить память.");
    }
  };

  function bindMemoryPanelEvents() {
    if (window.__logosMemoryPanelBound === true) return;
    window.__logosMemoryPanelBound = true;

    document.addEventListener(
      "click",
      function (event) {
        var target =
          event.target && event.target.closest ? event.target.closest("#memoryClearBtn") : null;
        if (!target) return;

        if (target.id === "memoryClearBtn") {
          event.preventDefault();
          window.clearMemory(null);
        }
      },
      true,
    );

    document.addEventListener("logos:modal:before-open", function (event) {
      var detail = event && event.detail;
      if (!detail || detail.modalId !== "memoryModal") return;
      window.loadMemoryData();
    });

    var configBindings = [
      ["memEnabled", "change", window.updateMemoryConfig],
      ["memLimit", "change", window.updateMemoryConfig],
    ];

    configBindings.forEach(function (binding) {
      var element = byId(binding[0]);
      if (!element || element.dataset.bound === "true") return;
      element.addEventListener(binding[1], binding[2]);
      element.dataset.bound = "true";
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindMemoryPanelEvents, { once: true });
  } else {
    bindMemoryPanelEvents();
  }
})();
