function initModels() {
  if (document.documentElement.dataset.modelsInitialized === "true") return;
  document.documentElement.dataset.modelsInitialized = "true";

  document.addEventListener("logos:modal:before-open", function (event) {
    var detail = event && event.detail;
    if (!detail || detail.modalId !== "model-modal") return;
    window.isCreatingNewChat = false;
    renderModelModal();
  });
}

document.addEventListener("DOMContentLoaded", initModels);

function formatUltraSummary(summary) {
  var parts = ["Ultra"];
  if (summary.status === "blocked_retired") {
    parts.push("снята");
    return parts.join(" · ");
  }
  if (summary.status === "prepared_not_activated") parts.push("не активирован");
  if (summary.context_window_tokens) {
    parts.push("контекст " + summary.context_window_tokens + " ток.");
  }
  parts.push("блокеров: " + (summary.blockers || []).length);
  return parts.join(" · ");
}

function renderModelModal() {
  var modelList = document.getElementById("model-list");
  var modelModal = document.getElementById("model-modal");
  if (!modelList) return;
  modelList.replaceChildren();
  var allModels = Object.assign({}, modelsData.text || {}, modelsData.voice || {});
  if (Object.keys(allModels).length === 0) {
    var state =
      typeof modelLoadState === "undefined" ? { status: "idle", message: "" } : modelLoadState;
    var empty = document.createElement("div");
    empty.className = "model-option model-option-empty";
    empty.setAttribute("role", "status");
    empty.setAttribute("aria-live", "polite");
    empty.textContent =
      state.status === "loading"
        ? state.message
        : state.message || "Модели временно недоступны. Интерфейс продолжает работать.";
    modelList.appendChild(empty);
    if (state.status === "error") {
      var retry = document.createElement("button");
      retry.type = "button";
      retry.className = "logos-btn model-option-retry";
      retry.textContent = "Повторить";
      retry.addEventListener("click", function () {
        if (window.LogosModelCatalog) window.LogosModelCatalog.load().catch(function () {});
      });
      modelList.appendChild(retry);
    }
    return;
  }
  Object.keys(allModels).forEach(function (key) {
    var m = allModels[key];
    var unavailable = m.available === false;
    var div = document.createElement("button");
    div.type = "button";
    div.disabled = unavailable;
    div.className =
      "logos-btn model-option" +
      (key === currentModel ? " active" : "") +
      (unavailable ? " model-option-unavailable" : "");
    var priceText = "";
    if (m.input != null && m.output != null) {
      priceText =
        m.currency === "USD" || m.price_unit === "per_million_tokens"
          ? m.input + " / " + m.output + " USD/1M"
          : m.input + " / " + m.output + " \u20bd/1K";
    }
    var badges = "";
    if (modelsData.voice && modelsData.voice[key]) badges += " \uD83C\uDFA4";
    if (m.multimodal) badges += " \uD83D\uDCF7";
    if (unavailable)
      badges += " \u00B7 \u043D\u0435\u0434\u043E\u0441\u0442\u0443\u043F\u043D\u0430";
    var nameWrap = document.createElement("div");
    var name = document.createElement("strong");
    name.textContent = m.name + badges;
    div.setAttribute("aria-label", m.name + badges);
    nameWrap.appendChild(name);

    var price = document.createElement("div");
    price.className = "model-option-price";
    price.textContent = priceText;

    div.appendChild(nameWrap);
    div.appendChild(price);
    if (m.ultra_profile) {
      var ultra = document.createElement("div");
      ultra.className = "model-option-ultra";
      ultra.textContent = formatUltraSummary(m.ultra_profile);
      ultra.title = (m.ultra_profile.blockers || []).join(", ");
      div.appendChild(ultra);
    }
    div.addEventListener("click", function () {
      if (unavailable) return;
      var creatingNewChat = window.isCreatingNewChat === true;
      if (typeof window.changeModel === "function") {
        // A new chat must not rewrite the model of the conversation that is still current.
        window.changeModel(key, creatingNewChat);
      }
      if (modelModal) window.LogosCoreAPI.ui.modal.close(modelModal);
      if (creatingNewChat) {
        createConversation(key);
        window.isCreatingNewChat = false;
      }
    });
    modelList.appendChild(div);
  });
}

function createConversation(modelKey) {
  window.LogosDispatcher.request("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: modelKey }),
  })
    .then(function (r) {
      return r.json();
    })
    .then(function (data) {
      if (data.error) {
        alert("\u041E\u0448\u0438\u0431\u043A\u0430: " + data.error);
        return;
      }
      conversations.unshift({
        id: data.id,
        title: data.title || "\u041D\u043E\u0432\u044B\u0439 \u0447\u0430\u0442",
        model: modelKey,
      });
      localStorage.setItem("conversations", JSON.stringify(conversations));
      selectConv(data.id);
    })
    .catch(function (e) {
      alert("\u041E\u0448\u0438\u0431\u043A\u0430 \u0441\u0435\u0442\u0438: " + e);
    });
}

function updateModelButton() {
  var btn = document.getElementById("model-btn");
  if (!btn) return;
  var allModels = Object.assign({}, modelsData.text || {}, modelsData.voice || {});
  var m = allModels[currentModel];
  if (m) {
    btn.textContent = "🤖";
    btn.title = "Выбор модели: " + m.name;
    btn.setAttribute("aria-label", "Выбор модели: " + m.name);
  }
}
