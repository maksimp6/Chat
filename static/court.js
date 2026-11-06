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

  var h = window.LogosCoreAPI.ui.element;

  function api(path) {
    return window.LogosDispatcher.request(path, { headers: { Accept: "application/json" } }).then(function (r) {
      if (!r.ok) return window.LogosHttpError(r);
      return r.json();
    });
  }

  function when(ts) {
    if (!ts) return "";
    // The standard Date format has three fraction digits (more is up to each browser), so microseconds are cut first.
    var date =
      typeof ts === "number" ? new Date(ts * 1000) : new Date(String(ts).replace(/(\.\d{3})\d+/, "$1"));
    // One date look across the app's windows (the treasury writes its dates the same way).
    return isNaN(date.getTime())
      ? String(ts)
      : date.toLocaleString("ru-RU", {
          day: "2-digit",
          month: "2-digit",
          year: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        });
  }

  function badge(verdict) {
    var cls = VERDICT_CLASS[verdict] || "none";
    return h("span", { class: "logos-court-badge logos-court-badge-" + cls }, verdict || "—");
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
    var existing = document.getElementById(MODAL_ID);
    if (existing) existing.remove();

    var UI = window.LogosCoreAPI.ui;
    var body = h("div");
    body.appendChild(
      h(
        "div",
        { class: "logos-court-note" },
        "Здесь заседания только видны. В суд подают словами в чате: «подай этот диалог в суд». " +
          "Пользователь всегда прав: в диалоге «⚖️ Суд: …» судья ищет, в чём виноват ИИ, допрашивает его " +
          "в копии его диалога, выносит вердикты и пишет заключение вины. Записи заседаний не удаляются.",
      ),
    );

    var list = h("div", { class: "logos-dept-list" }, "Загрузка…");
    body.appendChild(list);

    var modal = UI.modal.create({
      id: MODAL_ID,
      title: "Зал заседаний",
      contentClassName: "logos-dept-card",
      body: body,
    });
    UI.modal.open(modal);

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
        // A failure is the shared calm note (static/style.css .logos-error-note).
        list.classList.add("logos-error-note");
        list.textContent = "Ошибка: " + err.message;
      });
  }

  window.openCourtHall = openCourtHall;
})();
