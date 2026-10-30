(function () {
  "use strict";

  // Group chat: one question of the user is answered by every chosen model in turn, in the same chat.
  // The panel keeps the chosen models; chat.js asks it for them when a message is sent.
  var STORE_KEY = "group_chat_models";
  var modal = null;

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      node.setAttribute(key, attrs[key]);
    });
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function readStored() {
    try {
      var value = JSON.parse(localStorage.getItem(STORE_KEY) || "[]");
      return Array.isArray(value) ? value.filter(function (key) { return typeof key === "string"; }) : [];
    } catch (error) {
      return [];
    }
  }

  function writeStored(keys) {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(keys));
    } catch (error) {
      // The choice then lasts until the page is reloaded.
    }
    memoryOnly = keys;
    syncButton();
  }

  var memoryOnly = readStored();

  function dialogModels() {
    var text = (typeof modelsData !== "undefined" && modelsData.text) || {};
    return Object.keys(text).filter(function (key) {
      return window.isDialogModel(key) && !(modelsData.voice && modelsData.voice[key]) &&
        !(modelsData.image && modelsData.image[key]);
    });
  }

  // The Court of Logos sits in the group chat as a participant: after the models it judges the
  // replies since its last word and writes the verdicts into the chat (court/hearings.py, speak).
  var COURT = "court";
  var COURT_LABEL = "⚖️ Суд Логоса";

  // The chosen participants that can still answer; fewer than two means the chat is not a group.
  function models() {
    var available = dialogModels().concat([COURT]);
    return memoryOnly.filter(function (key) { return available.includes(key); });
  }

  function label(key) {
    if (key === COURT) return COURT_LABEL;
    var entry = (typeof modelsData !== "undefined" && modelsData.text && modelsData.text[key]) || {};
    return entry.name || entry.label || key;
  }

  function syncButton() {
    var button = document.getElementById("group-chat-btn");
    if (!button) return;
    var count = models().length;
    var on = count >= 2;
    button.classList.toggle("active", on);
    button.title = on ? "Групповой чат: " + count + " модели отвечают по очереди" : "Групповой чат";
  }

  function close() {
    if (!modal) return;
    window.LogosCoreAPI.ui.modal.close(modal);
  }

  function open() {
    if (modal) {
      modal.remove();
      modal = null;
    }
    var UI = window.LogosCoreAPI.ui;
    var body = el("div", { class: "logos-dept-list" });
    body.appendChild(
      el(
        "div",
        {},
        "Отметьте двух участников или больше: модели и Суд Логоса. Каждое сообщение получат все отмеченные, по очереди, в этом же чате; суд говорит последним.",
      ),
    );
    var chosen = new Set(models());
    var keys = dialogModels();
    if (!keys.length) body.appendChild(el("div", {}, "Каталог моделей ещё не загружен."));
    // The Court is listed last: it speaks after the models, over what they said.
    keys = keys.concat([COURT]);
    keys.forEach(function (key) {
      var row = el("label", { class: "logos-dept-row" });
      var box = el("input", { type: "checkbox" });
      box.checked = chosen.has(key);
      box.addEventListener("change", function () {
        if (box.checked) chosen.add(key);
        else chosen.delete(key);
        // Order of the catalog is the order of the answers.
        writeStored(keys.filter(function (k) { return chosen.has(k); }));
        status.textContent = summary();
      });
      row.appendChild(box);
      row.appendChild(el("strong", {}, " " + label(key)));
      if (key === COURT) {
        row.appendChild(
          el("span", {}, "судит реплики по записи: вердикт, цитата, правило; заседание над диалогом ведётся в зале ⚖️"),
        );
      }
      body.appendChild(row);
    });
    var status = el("div", {}, "");
    function summary() {
      var count = models().length;
      return count >= 2
        ? "Групповой чат включён: " + count + " модели."
        : "Групповой чат выключен: отмечено меньше двух моделей.";
    }
    status.textContent = summary();
    body.insertBefore(status, body.firstChild.nextSibling);
    modal = UI.modal.create({
      id: "group-chat-modal",
      title: "Групповой чат",
      titleTag: "h3",
      className: "logos-dept-overlay",
      contentClassName: "logos-dept-card",
      closeAction: "group.close",
      body: body,
    });
    (document.querySelector(".logos-pro-app") || document.body).appendChild(modal);
    UI.modal.open(modal);
  }

  // The turn that is not the user's own words: it asks the next model to speak into the chat.
  function followUpText(key, all) {
    var court = all.includes(COURT)
      ? " В чате заседает Суд Логоса: он судит реплики агентов по записи (вердикты ПРАВО, БЕСПОЛЕЗНОЕ, СКОЛЬЗКО, ТЯЖКОЕ с цитатой и правилом); его слово выше можно обсуждать, оспаривать и уточнять."
      : "";
    return (
      "[Групповой чат] Участники: " + all.map(label).join(", ") + ". Сейчас отвечает " + label(key) +
      ". Выше вопрос пользователя и реплики других участников." + court + " Ты сам решаешь, отвечать ли: если есть что " +
      "добавить, напиши реплику, не повторяя сказанное; если нет, ответь одним словом «Пропускаю»."
    );
  }

  // The Court's turn: no model is asked, the server judges and writes the verdicts into the chat.
  function courtTurn(conversationId) {
    return window.LogosDispatcher.request("/api/court/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ conversation_id: conversationId }),
    }).then(function (r) {
      if (!r.ok) return window.LogosHttpError(r).then(function (e) { throw e; });
      return r.json();
    });
  }

  window.LogosGroupChat = {
    models: models, followUpText: followUpText, label: label, open: open, COURT: COURT, courtTurn: courtTurn,
  };
  window.openGroupChatModal = open;
  window.LogosCoreAPI.ui.actions.register("group.close", close);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", syncButton, { once: true });
  else syncButton();
  // The catalog loads after the page: the button is checked again once it has.
  window.addEventListener("logos:model-changed", syncButton);
  window.addEventListener("load", function () { setTimeout(syncButton, 3000); });
})();
