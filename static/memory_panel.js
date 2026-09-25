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
