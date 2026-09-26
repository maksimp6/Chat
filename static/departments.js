(function () {
  "use strict";

  var modal = null;

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      node.setAttribute(key, attrs[key]);
    });
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function close() {
    if (!modal) return;
    window.AliceCoreAPI.ui.modal.close(modal);
  }

  function open() {
    if (modal) {
      modal.remove();
      modal = null;
    }

    var UI = window.AliceCoreAPI.ui;
    var list = el("div", { class: "alice-dept-list" }, "Загрузка…");
    modal = UI.modal.create({
      id: "departments-modal",
      title: "Departments",
      titleTag: "h3",
      className: "alice-dept-overlay",
      contentClassName: "alice-dept-card",
      closeAction: "departments.close",
      body: list,
    });

    (document.querySelector(".alice-pro-app") || document.body).appendChild(modal);
    UI.modal.open(modal);

    window.AliceDispatcher.request("/api/departments", { headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (data) {
        list.textContent = "";
        (data.departments || []).forEach(function (department) {
          var row = el("button", { class: "alice-dept-row" });
          row.appendChild(el("strong", {}, department.name));
          row.appendChild(
            el("span", {}, (department.description || department.type || "").slice(0, 120)),
          );
          row.onclick = function () {
            if (
              department.id === "partner-relations" &&
              typeof window.openPartnerRelationsModal === "function"
            ) {
              close();
              window.openPartnerRelationsModal();
              return;
            }
            window.AliceDispatcher.request(
              "/api/departments/" + encodeURIComponent(department.id) + "/sessions",
              { method: "POST", headers: { Accept: "application/json" } },
            )
              .then(function (response) {
                return response.json().then(function (payload) {
                  return { ok: response.ok, data: payload };
                });
              })
              .then(function (result) {
                if (!result.ok) {
                  alert(result.data.error || "Не удалось начать сессию");
                  return;
                }
                row.querySelector("span").textContent = "Сессия: " + result.data.session.id;
              });
          };
          list.appendChild(row);
        });
        if (!list.children.length) {
          list.appendChild(el("div", {}, "Пока нет активных departments."));
        }
      })
      .catch(function (error) {
        list.textContent = "Ошибка: " + error.message;
      });
  }

  window.AliceCoreAPI.ui.actions.register("departments.close", close);
  window.openDepartmentsModal = open;
  window.closeDepartmentsModal = close;
})();
