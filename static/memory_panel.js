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

  window.loadMemoryData = async function () {
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
        category.textContent = "[" + String(fact.category || "general") + "]";
        item.appendChild(category);
        item.appendChild(document.createTextNode(" " + String(fact.fact || "")));
        list.appendChild(item);
      });
    } catch (error) {
      console.error("[Memory] Failed to load memory:", error);
      reportStatus("Не удалось загрузить память.");
    }
  };

  window.updateMemoryConfig = async function () {
    var enabled = byId("memEnabled");
    var limit = byId("memLimit");
    var maxFacts = parseInt(limit && limit.value, 10);
    try {
      var response = await window.LogosDispatcher.request("/api/memory/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: Boolean(enabled && enabled.checked),
          max_context_facts: Number.isNaN(maxFacts) ? 15 : maxFacts,
        }),
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
    if (!confirm("Очистить память?")) return;
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
