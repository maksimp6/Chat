(function () {
  "use strict";

  function h(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      node.setAttribute(key, attrs[key]);
    });
    if (text !== undefined) node.textContent = text;
    return node;
  }

  var STATUS_LABELS = {
    lead: "Лид",
    active: "Активен",
    negotiating: "Переговоры",
    paused: "Пауза",
    completed: "Завершён",
    archived: "В архиве",
  };

  var DIRECTION_LABELS = { outgoing: "исходящее", incoming: "входящее" };
  var FOLLOWUP_LABELS = { open: "открыто", completed: "завершено", cancelled: "отменено" };

  function statusLabel(status) {
    return STATUS_LABELS[status] || status || "";
  }

  // Превью обрывается по слову, чтобы не резать слово посередине.
  function preview(text, limit) {
    if (text.length <= limit) return text;
    var cut = text.slice(0, limit);
    var space = cut.lastIndexOf(" ");
    if (space > limit / 2) cut = cut.slice(0, space);
    return cut + "…";
  }

  function emptyLine(text) {
    return h("div", { class: "logos-partner-empty" }, text);
  }

  function openPartnerRelationsModal() {
    var existing = document.getElementById("partner-relations-modal");
    if (existing) existing.remove();

    var card = h("div");

    var notice = h(
      "div",
      { class: "logos-partner-note" },
      "Партнёры и история принадлежат текущему пользователю. Отправка внешних сообщений выполняется через инструмент модели.",
    );
    card.appendChild(notice);

    var form = h("form", { class: "logos-partner-form" });
    var name = h("input", {
      required: "required",
      placeholder: "Имя / компания",
      "aria-label": "Имя или компания",
    });
    var organization = h("input", { placeholder: "Организация", "aria-label": "Организация" });
    var email = h("input", { type: "email", placeholder: "Email", "aria-label": "Email" });
    var phone = h("input", { placeholder: "Телефон", "aria-label": "Телефон" });
    var create = h("button", { type: "submit", class: "logos-btn btn-primary" }, "Добавить партнёра");
    function field(title, input) {
      var wrap = h("label", { class: "logos-partner-field" });
      wrap.appendChild(h("span", {}, title));
      wrap.appendChild(input);
      return wrap;
    }
    form.appendChild(field("Имя или компания", name));
    form.appendChild(field("Организация", organization));
    form.appendChild(field("Email", email));
    form.appendChild(field("Телефон", phone));
    form.appendChild(create);
    card.appendChild(form);

    var list = h("div", { class: "logos-dept-list" });
    list.textContent = "Загрузка…";
    card.appendChild(list);

    var UI = window.LogosCoreAPI.ui;
    var modal = UI.modal.create({
      id: "partner-relations-modal",
      title: "Партнёры",
      contentClassName: "logos-dept-card",
      body: card,
    });
    (document.querySelector(".logos-pro-app") || document.body).appendChild(modal);
    UI.modal.open(modal);

    function load() {
      return window.LogosDispatcher.request("/api/partners", {
        headers: { Accept: "application/json" },
      })
        .then(function (r) {
          return r.json().then(function (data) {
            if (!r.ok) throw new Error(data.error || "HTTP " + r.status);
            return data;
          });
        })
        .then(function (data) {
          // A later load after a failed one clears the failure's note look.
          list.classList.remove("logos-error-note");
          list.textContent = "";
          var partners = Array.isArray(data.partners) ? data.partners : [];
          if (!partners.length) {
            list.appendChild(h("div", {}, "Пока нет партнёров."));
            return;
          }
          partners.forEach(function (partner) {
            var row = h("button", { type: "button", class: "logos-dept-row" });
            row.appendChild(h("strong", {}, partner.name));
            row.appendChild(
              h(
                "span",
                {},
                [partner.organization, statusLabel(partner.status), partner.email]
                  .filter(Boolean)
                  .join(" · "),
              ),
            );
            row.onclick = function () {
              window.LogosDispatcher.request("/api/partners/" + encodeURIComponent(partner.id), {
                headers: { Accept: "application/json" },
              })
                .then(function (r) {
                  return r.json().then(function (data) {
                    if (!r.ok) throw new Error(data.error || "HTTP " + r.status);
                    return data.partner;
                  });
                })
                .then(showDetails)
                .catch(function (err) {
                  LogosToast.error(window.LogosErrorText("Не удалось открыть партнёра", err));
                });
            };
            list.appendChild(row);
          });
        });
    }

    // Details must show the server's current state, not the copy taken before the change.
    function reloadPartner(id) {
      return window.LogosDispatcher.request("/api/partners/" + encodeURIComponent(id), {
        headers: { Accept: "application/json" },
      })
        .then(function (r) {
          return r.json().then(function (data) {
            if (!r.ok) throw new Error(data.error || "HTTP " + r.status);
            return data.partner;
          });
        })
        .then(function (fresh) {
          load();
          showDetails(fresh);
        });
    }

    function showDetails(partner) {
      var detail = document.getElementById("partner-relations-detail");
      if (detail) detail.remove();
      detail = h("div", { id: "partner-relations-detail", class: "logos-partner-detail" });
      detail.appendChild(h("h4", {}, partner.name + " · " + statusLabel(partner.status)));

      var status = h("select", { "aria-label": "Статус партнёра" });
      ["lead", "active", "negotiating", "paused", "completed", "archived"].forEach(
        function (value) {
          var option = h("option", { value: value }, STATUS_LABELS[value]);
          if (value === partner.status) option.selected = true;
          status.appendChild(option);
        },
      );
      var statusButton = h("button", { type: "button", class: "logos-btn" }, "Сохранить статус");
      statusButton.onclick = function () {
        window.LogosDispatcher.request(
          "/api/partners/" + encodeURIComponent(partner.id) + "/status",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: status.value }),
          },
        )
          .then(function (r) {
            return r.json().then(function (data) {
              if (!r.ok) throw new Error(data.error || "HTTP " + r.status);
              load();
              showDetails(data.partner);
            });
          })
          .catch(function (err) {
            LogosToast.error(window.LogosErrorText("Не удалось сменить статус", err));
          });
      };
      // The select and its button share one row.
      var statusRow = h("div", { class: "logos-partner-status-row" });
      statusRow.appendChild(status);
      statusRow.appendChild(statusButton);
      detail.appendChild(statusRow);

      detail.appendChild(h("h5", {}, "Контакты (" + (partner.contacts || []).length + ")"));
      if (!(partner.contacts || []).length) detail.appendChild(emptyLine("Пока пусто."));
      (partner.contacts || []).forEach(function (contact) {
        detail.appendChild(
          h(
            "div",
            { class: "logos-partner-line" },
            [contact.name, contact.role, contact.email || contact.phone]
              .filter(Boolean)
              .join(" · "),
          ),
        );
      });

      detail.appendChild(h("h5", {}, "Переписка (" + (partner.messages || []).length + ")"));
      if (!(partner.messages || []).length) detail.appendChild(emptyLine("Пока пусто."));
      (partner.messages || []).forEach(function (message) {
        detail.appendChild(
          h(
            "div",
            { class: "logos-partner-line" },
            "[" +
              (DIRECTION_LABELS[message.direction] || message.direction) +
              "] " +
              message.subject +
              (message.body ? ": " + preview(message.body, 160) : ""),
          ),
        );
      });

      detail.appendChild(h("h5", {}, "Напоминания (" + (partner.followups || []).length + ")"));
      if (!(partner.followups || []).length) detail.appendChild(emptyLine("Пока пусто."));
      (partner.followups || []).forEach(function (followup) {
        var line = h("div", { class: "logos-partner-line" });
        line.appendChild(h("span", {}, followup.title + " · " + (FOLLOWUP_LABELS[followup.status] || followup.status)));
        if (followup.status === "open") {
          var done = h("button", { type: "button", class: "logos-btn" }, "Готово");
          done.onclick = function () {
            window.LogosDispatcher.request(
              "/api/partners/followups/" + encodeURIComponent(followup.id) + "/complete",
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ status: "completed" }),
              },
            )
              .then(function (r) {
                return r.json().then(function (data) {
                  if (!r.ok) throw new Error(data.error || "HTTP " + r.status);
                  return reloadPartner(partner.id);
                });
              })
              .catch(function (err) {
                LogosToast.error(window.LogosErrorText("Не удалось завершить", err));
              });
          };
          line.appendChild(done);
        }
        detail.appendChild(line);
      });
      card.appendChild(detail);
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      create.disabled = true;
      window.LogosDispatcher.request("/api/partners", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.value,
          organization: organization.value,
          email: email.value,
          phone: phone.value,
        }),
      })
        .then(function (r) {
          return r.json().then(function (data) {
            if (!r.ok) throw new Error(data.error || "HTTP " + r.status);
            return data;
          });
        })
        .then(function () {
          form.reset();
          return load();
        })
        .catch(function (err) {
          LogosToast.error(window.LogosErrorText("Не удалось сохранить партнёра", err));
        })
        .finally(function () {
          create.disabled = false;
        });
    });

    load().catch(function (err) {
      // A failure is the shared calm note (static/style.css .logos-error-note).
      list.classList.add("logos-error-note");
      list.textContent = "Ошибка: " + err.message;
    });
  }

  window.openPartnerRelationsModal = openPartnerRelationsModal;
})();
