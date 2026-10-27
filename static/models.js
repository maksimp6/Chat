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
  if (summary.default_profile === "ultra") parts.push("профиль по умолчанию");
  if (summary.context_window_tokens) {
    parts.push("контекст " + Number(summary.context_window_tokens).toLocaleString("ru-RU") + "\u00a0ток.");
  }
  parts.push("ограничений: " + (summary.blockers || []).length);
  return parts.join(" · ");
}

function renderModelModal() {
  var modelList = document.getElementById("model-list");
  var modelModal = document.getElementById("model-modal");
  if (!modelList) return;
  modelList.replaceChildren();
  var allModels = Object.assign({}, modelsData.text || {}, modelsData.voice || {}, modelsData.image || {});
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
    if (!["text", "voice", "image"].includes(m.type) || m.dialog_selectable === false) return;
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
      priceText = m.input + " / " + m.output + " \u20bd/1K";
    } else if (m.pricing_status === "unknown") {
      priceText = "Стоимость не определена";
    }
    var badges = "";
    if (modelsData.voice && modelsData.voice[key]) badges += " \uD83C\uDFA4";
    if (window.modelAcceptsPhotos(key)) badges += " \uD83D\uDCF7";
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
    var lifecycle = m.lifecycle || {};
    var endDate = lifecycle.shutdown_on || lifecycle.available_until;
    if (endDate) {
      var support = document.createElement("div");
      support.className = "model-option-lifecycle";
      support.textContent = lifecycle.shutdown_on
        ? "Отключение: " + endDate
        : "Доступна до: " + endDate;
      div.appendChild(support);
    }
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
        if (window.changeModel(key, creatingNewChat) === false) return;
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

function createConversation(modelKey, options) {
  options = options || {};
  if (typeof window.isDialogModel === "function" && !window.isDialogModel(modelKey)) {
    if (window.LogosToast) window.LogosToast.error("Эта модель не предназначена для диалога");
    return Promise.resolve(null);
  }
  var selectedBeforeCreation = currentConvId;
  return window.LogosDispatcher.request("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: modelKey }),
  })
    .then(async function (r) {
      var data = await r.json().catch(function () { return {}; });
      if (!r.ok || data.error) {
        throw new Error(data.error || data.detail || data.title || "HTTP " + r.status);
      }
      if (typeof data.id !== "string" || !window.LogosWikifunctions.trim(data.id)) {
        throw new Error("Сервер не вернул идентификатор диалога");
      }
      var conversation = {
        id: data.id,
        title: data.title || "\u041D\u043E\u0432\u044B\u0439 \u0447\u0430\u0442",
        model: modelKey,
      };
      var existing = conversations.findIndex(function (item) { return item.id === data.id; });
      if (existing >= 0) conversations[existing] = conversation;
      else conversations.unshift(conversation);
      localStorage.setItem("conversations", JSON.stringify(conversations));
      if (options.select !== false && currentConvId === selectedBeforeCreation) {
        await selectConv(data.id);
      } else if (typeof renderSidebar === "function") {
        renderSidebar();
      }
      return data.id;
    })
    .catch(function (e) {
      console.error("[MODELS] network error", e);
      LogosToast.error("Ошибка сети. Проверьте соединение.");
      return null;
    });
}

function updateModelButton() {
  var btn = document.getElementById("model-btn");
  if (!btn) return;
  var allModels = Object.assign({}, modelsData.text || {}, modelsData.voice || {}, modelsData.image || {});
  var m = allModels[currentModel];
  if (m) {
    btn.textContent = "🤖";
    btn.title = "Выбор модели: " + m.name;
    btn.setAttribute("aria-label", "Выбор модели: " + m.name);
  }
}
