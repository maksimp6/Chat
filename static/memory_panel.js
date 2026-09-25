(function () {
  "use strict";

  function byId(id) {
    return document.getElementById(id);
  }

  window.openMemoryModal = function () {
    var modal = byId("memoryModal");
    if (modal) {
      modal.hidden = false;
      window.loadMemoryData();
    }
  };

  window.closeMemoryModal = function () {
    var modal = byId("memoryModal");
    if (modal) modal.hidden = true;
  };

  window.loadMemoryData = async function () {
    try {
      var response = await fetch("/api/memory/manage");
      if (!response.ok) throw new Error("HTTP " + response.status);
      var data = await response.json();
      var enabled = byId("memEnabled");
      var limit = byId("memLimit");
      var count = byId("memCount");
      var list = byId("memoryFactsList");
      var facts = Array.isArray(data.facts) ? data.facts : [];

      if (enabled) enabled.checked = Boolean(data.config && data.config.enabled);
      if (limit) limit.value = (data.config && data.config.max_context_facts) || 15;
      if (count) count.textContent = facts.length;
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
    }
  };

  window.updateMemoryConfig = async function () {
    var enabled = byId("memEnabled");
    var limit = byId("memLimit");
    try {
      var response = await fetch("/api/memory/config", {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          enabled: Boolean(enabled && enabled.checked),
          max_context_facts: parseInt(limit && limit.value, 10) || 15
        })
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
      await window.loadMemoryData();
    } catch (error) {
      console.error("[Memory] Failed to update config:", error);
    }
  };

  window.clearMemory = async function (category) {
    if (!confirm("Очистить память?")) return;
    try {
      var response = await fetch("/api/memory/clear", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({category: category})
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
      await window.loadMemoryData();
    } catch (error) {
      console.error("[Memory] Failed to clear memory:", error);
    }
  };

  function bindMemoryPanelEvents() {
    var bindings = [
      ["memory-btn", "click", window.openMemoryModal],
      ["memoryCloseBtn", "click", window.closeMemoryModal],
      ["memEnabled", "change", window.updateMemoryConfig],
      ["memLimit", "change", window.updateMemoryConfig],
      ["memoryClearBtn", "click", function () { window.clearMemory(null); }]
    ];

    bindings.forEach(function (binding) {
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
