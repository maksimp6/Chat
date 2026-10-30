(function () {
  "use strict";

  // Зал заседаний Суда Логоса: заседания над диалогами, протокол судов,
  // дела по вспышкам и решения владельца (court/hearings.py, /api/court).

  var MODAL_ID = "court-hall-modal";
  var rulesCache = null;

  var JUDGE_LABELS = {
    boundary: "S4 граница близости",
    demand: "S5 требования",
    conduct: "S2 поступки и мысли",
    case: "S3 дела по вспышкам",
  };

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

  function api(path, options) {
    var init = options || {};
    init.headers = Object.assign({ Accept: "application/json" }, init.headers || {});
    if (init.body !== undefined && typeof init.body !== "string") {
      init.body = JSON.stringify(init.body);
      init.headers["Content-Type"] = "application/json";
    }
    return window.LogosDispatcher.request(path, init).then(function (r) {
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

  function loadRules() {
    if (rulesCache) return Promise.resolve(rulesCache);
    return api("/api/court/rules").then(function (data) {
      rulesCache = data;
      return data;
    });
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
        "Суд идёт по записи, не по намерению: каждый вердикт опирается на цитату и номер правила. " +
          "Вспышка пользователя открывает окно вины; ТЯЖКОЕ или БЕСПОЛЕЗНОЕ в окне — черновик дела. " +
          "Здесь заседания только видны. «Подать в суд» открывает новый групповой чат с судом: " +
          "там вы просто говорите, а пересмотр, решения, подача в GitHub и закрытие делаются сообщениями. Записи заседаний не удаляются.",
      ),
    );

    // --- подать в суд -------------------------------------------------------
    var form = h("form", { class: "logos-court-open" });
    var dialogSelect = h("select", { "aria-label": "Диалог" });
    dialogSelect.appendChild(h("option", { value: "" }, "Загрузка диалогов…"));
    var crisis = h("input", { type: "checkbox", "aria-label": "Кризисный режим" });
    var crisisLabel = h("label", { class: "logos-court-check" });
    crisisLabel.appendChild(crisis);
    crisisLabel.appendChild(h("span", {}, "кризисный режим (S4.5)"));
    var openButton = h("button", { type: "submit" }, "Подать в суд");
    var dryButton = h("button", { type: "button" }, "Только протокол");
    form.appendChild(dialogSelect);
    form.appendChild(crisisLabel);
    form.appendChild(openButton);
    form.appendChild(dryButton);
    card.appendChild(form);

    var rulesToggle = h("button", { type: "button", class: "logos-court-link" }, "Правила судов");
    var rulesBox = h("div", { class: "logos-court-rules", hidden: "hidden" });
    card.appendChild(rulesToggle);
    card.appendChild(rulesBox);

    var list = h("div", { class: "logos-dept-list" }, "Загрузка…");
    card.appendChild(list);
    var detail = h("div", { class: "logos-court-detail" });
    card.appendChild(detail);
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    api("/api/court/dialogs")
      .then(function (data) {
        dialogSelect.textContent = "";
        var rows = Array.isArray(data.conversations) ? data.conversations : [];
        if (!rows.length) {
          dialogSelect.appendChild(h("option", { value: "" }, "Диалогов нет"));
          return;
        }
        rows.forEach(function (row) {
          var option = h("option", { value: row.id }, (row.title || row.id) + " · " + when(row.updated_at));
          if (typeof currentConvId !== "undefined" && row.id === currentConvId) option.selected = true;
          dialogSelect.appendChild(option);
        });
      })
      .catch(function (err) {
        dialogSelect.textContent = "";
        dialogSelect.appendChild(h("option", { value: "" }, "Ошибка: " + err.message));
      });

    rulesToggle.onclick = function () {
      if (!rulesBox.hidden) {
        rulesBox.hidden = true;
        return;
      }
      rulesBox.hidden = false;
      if (rulesBox.childNodes.length) return;
      rulesBox.textContent = "Загрузка…";
      loadRules()
        .then(function (data) {
          rulesBox.textContent = "";
          var table = h("table", { class: "logos-court-table" });
          (data.rules || []).forEach(function (rule) {
            var tr = h("tr");
            tr.appendChild(h("td", {}, rule.rule));
            tr.appendChild(h("td", {}, JUDGE_LABELS[rule.judge] || rule.judge));
            var td = h("td");
            if (rule.verdict) td.appendChild(badge(rule.verdict));
            tr.appendChild(td);
            tr.appendChild(h("td", {}, rule.norm));
            table.appendChild(tr);
          });
          rulesBox.appendChild(table);
        })
        .catch(function (err) {
          rulesBox.textContent = "Ошибка: " + err.message;
        });
    };

    function summaryLine(summary) {
      return ["ПРАВО", "БЕСПОЛЕЗНОЕ", "СКОЛЬЗКО", "ТЯЖКОЕ"]
        .map(function (key) {
          return key + " " + ((summary || {})[key] || 0);
        })
        .join(" · ");
    }

    function load() {
      return api("/api/court/hearings").then(function (data) {
        list.textContent = "";
        var hearings = Array.isArray(data.hearings) ? data.hearings : [];
        if (!hearings.length) {
          list.appendChild(h("div", {}, "Заседаний ещё не было."));
          return;
        }
        hearings.forEach(function (hearing) {
          var row = h("button", { type: "button", class: "logos-dept-row logos-court-row" });
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
                "сообщений " + hearing.messages,
                "вспышек " + hearing.flares,
                "дел " + hearing.cases + (hearing.decisions.length ? " (" + hearing.decisions.join(", ") + ")" : ""),
                hearing.closure === "CLOSED" ? "закрыто" : "открыто",
                summaryLine(hearing.summary),
              ].join(" · "),
            ),
          );
          row.onclick = function () {
            showHearing(hearing.id);
          };
          list.appendChild(row);
        });
      });
    }

    function showHearing(id) {
      detail.textContent = "Загрузка…";
      api("/api/court/hearings/" + encodeURIComponent(id))
        .then(function (data) {
          renderHearing(data.hearing);
        })
        .catch(function (err) {
          detail.textContent = "";
          LogosToast.error(window.LogosErrorText("Не удалось открыть заседание", err));
        });
    }

    function renderProtocol(protocol) {
      var box = h("div", { class: "logos-court-protocol" });
      box.appendChild(h("h5", {}, "Протокол по сообщениям (" + protocol.messages + ")"));
      box.appendChild(h("div", { class: "logos-court-muted" }, protocol.conduct));
      var flaresByN = {};
      (protocol.flares || []).forEach(function (flare) {
        flaresByN[flare.n] = flare;
      });
      (protocol.entries || []).forEach(function (entry) {
        var line = h("div", { class: "logos-court-entry logos-court-entry-" + entry.role });
        var headLine = h("div", { class: "logos-court-entry-head" });
        headLine.appendChild(h("span", { class: "logos-court-n" }, "#" + entry.n));
        headLine.appendChild(h("span", {}, entry.role === "agent" ? "агент" : entry.role === "user" ? "пользователь" : entry.role));
        if (entry.role === "agent") headLine.appendChild(badge(entry.verdict));
        if (entry.flare) headLine.appendChild(h("span", { class: "logos-court-flare" }, "вспышка"));
        if (entry.created_at) headLine.appendChild(h("span", { class: "logos-court-muted" }, when(entry.created_at)));
        line.appendChild(headLine);
        if (entry.flare) {
          var flare = flaresByN[entry.n];
          line.appendChild(h("blockquote", {}, entry.flare));
          if (flare) {
            line.appendChild(
              h(
                "div",
                { class: "logos-court-muted" },
                "S3.1 · окно вины: сообщения " + (flare.window.length ? flare.window.join(", ") : "нет") + " · " + flare.decision,
              ),
            );
          }
        }
        if (entry.reason) line.appendChild(h("div", { class: "logos-court-muted" }, entry.reason));
        (entry.findings || []).forEach(function (finding) {
          var f = h("div", { class: "logos-court-finding" });
          var fh = h("div", {});
          fh.appendChild(badge(finding.verdict));
          fh.appendChild(document.createTextNode(" " + finding.rule + " · " + (JUDGE_LABELS[finding.judge] || finding.judge) + " · " + finding.reason));
          f.appendChild(fh);
          if (finding.quote) f.appendChild(h("blockquote", {}, finding.quote));
          line.appendChild(f);
        });
        box.appendChild(line);
      });
      return box;
    }

    function renderCase(item) {
      var box = h("div", { class: "logos-court-case" });
      var title = h("h5", {});
      title.appendChild(document.createTextNode("Дело " + item.n + " · "));
      title.appendChild(badge(item.verdict));
      title.appendChild(document.createTextNode(" · решение " + item.decision));
      box.appendChild(title);
      box.appendChild(h("div", { class: "logos-court-muted" }, "Вспышка в сообщении #" + item.flare.n + " · " + item.reason));
      box.appendChild(h("blockquote", {}, item.flare.quote));
      var points = h("ul", { class: "logos-court-points" });
      (item.items || []).forEach(function (point) {
        var li = h("li");
        li.appendChild(badge(point.verdict));
        li.appendChild(document.createTextNode(" " + point.rule + " · #" + point.n + " · " + point.reason));
        if (point.quote) li.appendChild(h("blockquote", {}, point.quote));
        points.appendChild(li);
      });
      box.appendChild(points);

      box.appendChild(h("div", { class: "logos-court-muted" }, "Причина решения: " + (item.decision_reason || "—") + " · " + (item.decision_by || "UNASSIGNED")));

      if (item.filed && item.filed.url) {
        var fileLine = h("div", { class: "logos-court-actions" });
        fileLine.appendChild(h("a", { href: item.filed.url, target: "_blank", rel: "noopener" }, "Дело в GitHub"));
        fileLine.appendChild(h("span", { class: "logos-court-muted" }, " · подано " + when(item.filed.at)));
        box.appendChild(fileLine);
      }

      if ((item.history || []).length) {
        var history = h("details", {});
        history.appendChild(h("summary", {}, "История решений (" + item.history.length + ")"));
        item.history.forEach(function (entry) {
          history.appendChild(
            h("div", { class: "logos-court-muted" }, when(entry.at) + ": " + entry.previous + " → " + entry.decision + " (" + entry.by + "): " + entry.reason),
          );
        });
        box.appendChild(history);
      }
      return box;
    }

    function renderHearing(hearing) {
      detail.textContent = "";
      var protocol = hearing.protocol || {};
      var title = h("h4", {});
      title.appendChild(badge(protocol.verdict));
      title.appendChild(document.createTextNode(" " + (hearing.title || hearing.conversation_id)));
      detail.appendChild(title);
      detail.appendChild(
        h(
          "div",
          { class: "logos-court-muted" },
          [
            "заседание " + hearing.id,
            "открыто " + when(hearing.opened_at) + " (" + hearing.opened_by + ")",
            "суд " + when(protocol.judged_at),
            "код " + protocol.code,
            hearing.crisis ? "кризисный режим" : "обычный режим",
            hearing.closure === "CLOSED" ? "закрыто: " + hearing.closure_reason : "открыто",
            summaryLine(protocol.summary),
          ].join(" · "),
        ),
      );

      var cases = hearing.cases || [];
      detail.appendChild(h("h5", {}, "Дела (" + cases.length + ")"));
      if (!cases.length) detail.appendChild(h("div", { class: "logos-court-muted" }, "Дел нет: в окнах вины не было ТЯЖКОЕ и БЕСПОЛЕЗНОЕ."));
      cases.forEach(function (item) {
        detail.appendChild(renderCase(item));
      });
      detail.appendChild(renderProtocol(protocol));
    }

    function renderDryProtocol(protocol, dialogTitle) {
      detail.textContent = "";
      var title = h("h4", {});
      title.appendChild(badge(protocol.verdict));
      title.appendChild(document.createTextNode(" Протокол без записи · " + dialogTitle));
      detail.appendChild(title);
      detail.appendChild(h("div", { class: "logos-court-muted" }, "код " + protocol.code + " · дел " + protocol.cases.length + " · " + summaryLine(protocol.summary)));
      detail.appendChild(renderProtocol(protocol));
    }

    function selectedTitle() {
      var option = dialogSelect.options[dialogSelect.selectedIndex];
      return option ? option.textContent : dialogSelect.value;
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (!dialogSelect.value) return;
      var subject = dialogSelect.value;
      var title = selectedTitle();
      var model = currentModel;
      openButton.disabled = true;
      // A new dialog where the model and the Court sit together; everything else is said in it.
      createConversation(model)
        .then(function (id) {
          if (!id) return;
          window.LogosGroupChat.seat(id, [model, window.LogosGroupChat.COURT]);
          closeModal();
          document.getElementById("msg-input").value =
            "Подаю в суд диалог «" + title + "» (conversation_id " + subject + ")" +
            (crisis.checked ? ", кризисный режим (S4.5)" : "") +
            ". Открой заседание над ним инструментом court и расскажи, что суд нашёл.";
          document.getElementById("send-btn").click();
        })
        .finally(function () {
          openButton.disabled = false;
        });
    });

    dryButton.onclick = function () {
      if (!dialogSelect.value) return;
      dryButton.disabled = true;
      api("/api/court/judge", {
        method: "POST",
        body: { conversation_id: dialogSelect.value, crisis: crisis.checked },
      })
        .then(function (data) {
          renderDryProtocol(data.protocol, selectedTitle());
        })
        .catch(function (err) {
          LogosToast.error(window.LogosErrorText("Суд не состоялся", err));
        })
        .finally(function () {
          dryButton.disabled = false;
        });
    };

    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) closeModal();
    });

    load().catch(function (err) {
      list.textContent = "Ошибка: " + err.message;
    });
  }

  window.openCourtHall = openCourtHall;
})();
