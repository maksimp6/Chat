(function () {
  function byId(id) {
    return document.getElementById(id);
  }

  window.openMemoryModal = function () {
    var modal = byId("memoryModal");
    if (modal) modal.hidden = false;
    window.loadMemoryData();
  };

  window.closeMemoryModal = function () {
    var modal = byId("memoryModal");
    if (modal) modal.hidden = true;
  };

  window.loadMemoryData = async function () {
    try {
      var res = await fetch("/api/memory/manage");
      var data = await res.json();
      byId("memEnabled").checked = data.config.enabled;
      byId("memLimit").value = data.config.max_context_facts;
      byId("memCount").textContent = data.facts.length;

      var list = byId("memoryFactsList");
      list.replaceChildren();
      if (data.facts.length === 0) {
        var empty = document.createElement("p");
        empty.className = "memory-empty";
        empty.textContent = "Память пуста.";
        list.appendChild(empty);
        return;
      }

      data.facts.forEach(function (fact) {
        var item = document.createElement("div");
        item.className = "memory-fact";
        var category = document.createElement("span");
        category.className = "memory-fact-category";
        category.textContent = "[" + String(fact.category || "") + "]";
        item.appendChild(category);
        item.appendChild(document.createTextNode(" " + String(fact.fact || "")));
        list.appendChild(item);
      });
    } catch (error) {
      console.error("Failed to load memory panel", error);
    }
  };

  window.updateMemoryConfig = async function () {
    var enabled = byId("memEnabled").checked;
    var limit = parseInt(byId("memLimit").value, 10) || 15;
    await fetch("/api/memory/config", {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({enabled: enabled, max_context_facts: limit})
    });
  };

  function bindMemoryPanelEvents() {
    var closeButton = byId("memoryCloseBtn");
    if (closeButton && !closeButton.dataset.bound) {
      closeButton.addEventListener("click", window.closeMemoryModal);
      closeButton.dataset.bound = "true";
    }
    var enabled = byId("memEnabled");
    if (enabled && !enabled.dataset.bound) {
      enabled.addEventListener("change", window.updateMemoryConfig);
      enabled.dataset.bound = "true";
    }
    var limit = byId("memLimit");
    if (limit && !limit.dataset.bound) {
      limit.addEventListener("change", window.updateMemoryConfig);
      limit.dataset.bound = "true";
    }
    var clearButton = byId("memoryClearBtn");
    if (clearButton && !clearButton.dataset.bound) {
      clearButton.addEventListener("click", function () { window.clearMemory(null); });
      clearButton.dataset.bound = "true";
    }
  }

  function bindMemoryPanelEvents() {
    var openButton = byId("memory-btn");
    if (openButton && !openButton.dataset.bound) {
      openButton.addEventListener("click", window.openMemoryModal);
      openButton.dataset.bound = "true";
    }

    var closeButton = byId("memoryCloseBtn");
    if (closeButton && !closeButton.dataset.bound) {
      closeButton.addEventListener("click", window.closeMemoryModal);
      closeButton.dataset.bound = "true";
    }
    var enabled = byId("memEnabled");
    if (enabled && !enabled.dataset.bound) {
      enabled.addEventListener("change", window.updateMemoryConfig);
      enabled.dataset.bound = "true";
    }
    var limit = byId("memLimit");
    if (limit && !limit.dataset.bound) {
      limit.addEventListener("change", window.updateMemoryConfig);
      limit.dataset.bound = "true";
    }
    var clearButton = byId("memoryClearBtn");
    if (clearButton && !clearButton.dataset.bound) {
      clearButton.addEventListener("click", function () { window.clearMemory(null); });
      clearButton.dataset.bound = "true";
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindMemoryPanelEvents, { once: true });
  } else {
    bindMemoryPanelEvents();
  }

  window.clearMemory = async function (category) {
    if (!confirm("Очистить память?")) return;
    await fetch("/api/memory/clear", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({category: category})
    });
    window.loadMemoryData();
  };
})();
