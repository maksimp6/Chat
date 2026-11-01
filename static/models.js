function createConversation(modelKey, options) {
  options = options || {};
  if (modelKey !== undefined && typeof window.isDialogModel === "function" && !window.isDialogModel(modelKey)) {
    if (window.LogosToast) window.LogosToast.error("Эта модель не предназначена для диалога");
    return Promise.resolve(null);
  }
  var selectedBeforeCreation = currentConvId;
  return window.LogosDispatcher.request("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // Without a model the server gives a text dialog the chat model.
    body: JSON.stringify(modelKey === undefined ? {} : { model: modelKey }),
  })
    .then(async function (r) {
      var data = await r.json().catch(function () { return {}; });
      if (!r.ok || data.error) {
        var failure = new Error(data.error || data.detail || data.title || "HTTP " + r.status);
        failure.serverResponse = true;
        failure.detail = typeof data.detail === "string" ? data.detail : "";
        throw failure;
      }
      if (typeof data.id !== "string" || !data.id.trim()) {
        throw new Error("Сервер не вернул идентификатор диалога");
      }
      var conversation = {
        id: data.id,
        title: data.title || "\u041D\u043E\u0432\u044B\u0439 \u0447\u0430\u0442",
        model: data.model || modelKey,
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
      if (e && e.serverResponse) {
        // A server refusal names its reason; the network text is only for transport failures.
        var reasons = {
          model_not_available_for_dialog: "Эта модель недоступна для диалога. Выберите другую модель.",
          model_catalog_unavailable: "Каталог моделей временно недоступен. Повторите попытку позже.",
        };
        LogosToast.error(Object.prototype.hasOwnProperty.call(reasons, e.message) ? reasons[e.message] : e.detail || e.message);
      } else {
        LogosToast.error("Ошибка сети. Проверьте соединение.");
      }
      return null;
    });
}
