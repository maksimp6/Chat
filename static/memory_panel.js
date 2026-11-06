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

  async function loadMemoryData() {
    // Old facts stay out of view while the request runs (пункт 51).
    var pending = byId("memoryFactsList");
    if (pending) pending.textContent = "Загрузка…";
    try {
      var response = await window.LogosDispatcher.request("/api/memory/manage");
      if (!response.ok) throw await window.LogosHttpError(response);
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
          Number.isInteger(config.max_context_facts) ? config.max_context_facts : 100,
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
        // The category is drawn as a chip, so it needs no brackets around it.
        var category = document.createElement("span");
        category.className = "memory-fact-category";
        category.textContent = CATEGORY_LABELS[fact.category] || String(fact.category || "общее");
        var text = document.createElement("span");
        text.textContent = String(fact.fact || "");
        item.appendChild(category);
        item.appendChild(text);
        list.appendChild(item);
      });
    } catch (error) {
      console.error("[Memory] Failed to load memory:", error);
      var failed = byId("memoryFactsList");
      if (failed) failed.replaceChildren();
      reportStatus(window.LogosErrorText("Не удалось загрузить память", error));
    }
  }

  function bindMemoryPanelEvents() {
    document.addEventListener("logos:modal:before-open", function (event) {
      var detail = event && event.detail;
      if (!detail || detail.modalId !== "memoryModal") return;
      loadMemoryData();
    });

  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindMemoryPanelEvents, { once: true });
  } else {
    bindMemoryPanelEvents();
  }
})();
