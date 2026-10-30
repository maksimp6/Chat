(function () {
  "use strict";

  // Зал заседаний Суда Логоса только показывает заседания (court/hearings.py, GET /api/court/hearings).
  // Всё остальное говорится в чате: иск — инструмент court, action sue, дальше судья ведёт диалог «⚖️ Суд: …».

  var MODAL_ID = "court-hall-modal";

  var VERDICT_CLASS = {
    "ПРАВО": "ok",
    "БЕСПОЛЕЗНОЕ": "useless",
    "СКОЛЬЗКО": "slippery",
    "ТЯЖКОЕ": "grave",
  };

  function h(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      node.setAttribute(key, attrs[key]);
    });
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function api(path) {
    return window.LogosDispatcher.request(path, { headers: { Accept: "application/json" } }).then(function (r) {
      if (!r.ok) return window.LogosHttpError(r).then(function (e) { throw e; });
      return r.json();
    });
  }

  function when(ts) {
    if (!ts) return "";
    var date = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
    return isNaN(date.getTime()) ? String(ts) : date.toLocaleString();
  }

  function badge(verdict) {
    var cls = VERDICT_CLASS[verdict] || "none";
    return h("span", { class: "logos-court-badge logos-court-badge-" + cls }, verdict || "—");
  }

  function closeModal() {
    var existing = document.getElementById(MODAL_ID);
    if (existing) existing.remove();
  }

  function summaryLine(summary) {
    return ["ПРАВО", "БЕСПОЛЕЗНОЕ", "СКОЛЬЗКО", "ТЯЖКОЕ"]
      .map(function (key) {
        return key + " " + ((summary || {})[key] || 0);
      })
      .join(" · ");
  }

  function renderHearing(hearing) {
    var row = h("div", { class: "logos-dept-row logos-court-row" });
    var title = h("strong", {});
    title.appendChild(badge(hearing.verdict));
    title.appendChild(document.createTextNode(" " + (hearing.title || hearing.conversation_id)));
    row.appendChild(title);
    row.appendChild(
      h(
        "span",
        {},
        [
          when(hearing.opened_at),
          hearing.owner_verdict ? "вердикт владельца: " + hearing.owner_verdict : "",
          "сообщений " + hearing.messages,
          "дел " + hearing.cases,
          hearing.closure === "CLOSED" ? "закрыто" : "открыто",
          summaryLine(hearing.summary),
        ].filter(Boolean).join(" · "),
      ),
    );
    row.appendChild(
      h(
        "div",
        { class: hearing.conclusion ? "" : "logos-court-muted" },
        hearing.conclusion
          ? "Заключение вины: " + hearing.conclusion.text + " · " + when(hearing.conclusion.at)
          : "Заключения вины ещё нет.",
      ),
    );
    return row;
  }

  function openCourtHall() {
    closeModal();

    var overlay = h("div", { id: MODAL_ID, class: "logos-dept-overlay" });
    var card = h("div", { class: "logos-dept-card logos-court-card" });
    var head = h("div", { class: "logos-dept-head" });
    head.appendChild(h("h3", {}, "⚖️ Зал заседаний"));
    var close = h("button", { type: "button", "aria-label": "Закрыть" }, "×");
    close.onclick = closeModal;
    head.appendChild(close);
    card.appendChild(head);

    card.appendChild(
      h(
        "div",
        { class: "logos-court-note" },
        "Здесь заседания только видны. В суд подают словами в чате: «подай этот диалог в суд». " +
          "Пользователь всегда прав: в диалоге «⚖️ Суд: …» судья ищет, в чём виноват ИИ, допрашивает его " +
          "в копии его диалога, выносит вердикты и пишет заключение вины. Записи заседаний не удаляются.",
      ),
    );

    var list = h("div", { class: "logos-dept-list" }, "Загрузка…");
    card.appendChild(list);
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) closeModal();
    });

    api("/api/court/hearings")
      .then(function (data) {
        list.textContent = "";
        var hearings = Array.isArray(data.hearings) ? data.hearings : [];
        if (!hearings.length) {
          list.appendChild(h("div", {}, "Заседаний ещё не было."));
          return;
        }
        hearings.forEach(function (hearing) {
          list.appendChild(renderHearing(hearing));
        });
      })
      .catch(function (err) {
        list.textContent = "Ошибка: " + err.message;
      });
  }

  window.openCourtHall = openCourtHall;
})();
