(function () {
  function byId(id) {
    return document.getElementById(id);
  }

  window.openMemoryModal = function () {
    var modal = byId("memoryModal");
    if (modal) modal.style.display = "flex";
    window.loadMemoryData();
  };

  window.closeMemoryModal = function () {
    var modal = byId("memoryModal");
    if (modal) modal.style.display = "none";
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
        empty.style.cssText = "color:#888;font-size:13px;";
        empty.textContent = "Память пуста.";
        list.appendChild(empty);
        return;
      }

      data.facts.forEach(function (fact) {
        var item = document.createElement("div");
        item.style.cssText = "background:#2a2a2a;padding:8px;margin-bottom:6px;border-radius:4px;font-size:12px;";
        var category = document.createElement("span");
        category.style.cssText = "color:#4da6ff;font-weight:bold;text-transform:uppercase;";
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

  document.addEventListener("DOMContentLoaded", bindMemoryPanelEvents);

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
