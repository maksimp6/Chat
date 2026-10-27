(function () {
  "use strict";

  function makeInput(id, label, type, placeholder) {
    var wrap = document.createElement("label");
    wrap.className = "cloudru-iam-field";
    var span = document.createElement("span");
    span.textContent = label;
    span.className = "cloudru-iam-label";
    var input = document.createElement("input");
    input.id = id;
    input.type = type || "text";
    input.placeholder = placeholder || "";
    input.className = "cloudru-iam-input";
    wrap.appendChild(span);
    wrap.appendChild(input);
    return wrap;
  }

  function value(id) {
    var node = document.getElementById(id);
    return node ? window.LogosWikifunctions.trim(node.value) : "";
  }

  function build() {
    if (document.getElementById("cloudru-iam-modal")) return;
    var UI = window.LogosCoreAPI.ui;
    var body = document.createElement("div");
    body.className = "cloudru-iam-body";
    var modal = UI.modal.create({
      id: "cloudru-iam-modal",
      title: "Cloud.ru IAM: выпуск API-ключа",
      titleTag: "h3",
      className: "cloudru-iam-modal",
      contentClassName: "cloudru-iam-box",
      closeAction: "cloudru-iam.close",
      body: body,
    });
    var box = body;
    box.appendChild(
      makeInput(
        "cloudru-iam-admin-token",
        "Токен администратора (для удалённого развёртывания)",
        "password",
        "не нужен на localhost",
      ),
    );
    box.appendChild(makeInput("cloudru-iam-service-account", "Service account ID", "text", "UUID"));
    box.appendChild(makeInput("cloudru-iam-name", "Название ключа", "text", "Logos"));
    box.appendChild(makeInput("cloudru-iam-description", "Описание", "text", "Для Logos"));
    box.appendChild(
      makeInput(
        "cloudru-iam-products",
        "Сервисы Cloud.ru, через запятую",
        "text",
        "monaas, foundation-models",
      ),
    );
    box.appendChild(makeInput("cloudru-iam-expires", "Срок действия", "datetime-local"));
    box.appendChild(
      makeInput("cloudru-iam-ips", "IP/подсети, через запятую", "text", "203.0.113.0/24"),
    );
    var confirmWrap = document.createElement("label");
    confirmWrap.className = "cloudru-iam-confirm";
    var confirm = document.createElement("input");
    confirm.id = "cloudru-iam-confirm";
    confirm.type = "checkbox";
    confirmWrap.appendChild(confirm);
    confirmWrap.appendChild(
      document.createTextNode(" Я подтверждаю выпуск ключа с указанными правами."),
    );
    box.appendChild(confirmWrap);
    var submit = document.createElement("button");
    submit.id = "cloudru-iam-submit";
    submit.textContent = "Выпустить ключ";
    submit.className = "logos-btn btn-primary";
    submit.addEventListener("click", submitWizard);
    box.appendChild(submit);
    var output = document.createElement("pre");
    output.id = "cloudru-iam-output";
    output.className = "cloudru-iam-output";
    // The secret must wrap on a 360 px screen instead of running past the edge.
    output.style.whiteSpace = "pre-wrap";
    output.style.overflowWrap = "anywhere";
    box.appendChild(output);
    (document.querySelector(".logos-pro-app") || document.body).appendChild(modal);
  }

  async function submitWizard() {
    var output = document.getElementById("cloudru-iam-output");
    output.classList.add("visible");
    output.textContent = "Выпуск ключа…";
    var products = value("cloudru-iam-products")
      .split(",")
      .map(function (x) {
        return window.LogosWikifunctions.trim(x);
      })
      .filter(Boolean);
    var ips = value("cloudru-iam-ips")
      .split(",")
      .map(function (x) {
        return window.LogosWikifunctions.trim(x);
      })
      .filter(Boolean);
    var expiry = value("cloudru-iam-expires");
    if (expiry) expiry = new Date(expiry).toISOString();
    var token = value("cloudru-iam-admin-token");
    var headers = { "Content-Type": "application/json" };
    if (token) headers.Authorization = "Bearer " + token;
    // A second click would issue a second key; keep the button off until the request ends.
    var submitBtn = document.getElementById("cloudru-iam-submit");
    if (submitBtn) submitBtn.disabled = true;
    try {
      var response = await window.LogosDispatcher.request("/api/cloudru/iam/api-keys", {
        method: "POST",
        headers: headers,
        body: JSON.stringify({
          service_account_id: value("cloudru-iam-service-account"),
          name: value("cloudru-iam-name"),
          description: value("cloudru-iam-description"),
          products: products,
          expires_at: expiry || null,
          ip_addresses: ips,
          confirm: Boolean(document.getElementById("cloudru-iam-confirm").checked),
        }),
      });
      var data = await response.json();
      if (!response.ok) throw new Error(data.error || "HTTP " + response.status);
      output.textContent =
        "Ключ создан. ID: " +
        data.id +
        "\nСсылка на ключ: " +
        data.key_ref +
        "\n\nСекрет ключа (сохраните сейчас):\n" +
        data.secret +
        "\n\nСекрет больше не показывается в Cloud.ru после закрытия окна.";
    } catch (error) {
      output.textContent = "Ошибка: " + error.message;
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  window.LogosCoreAPI.ui.actions.register("cloudru-iam.close", function (payload) {
    var modal = payload.element.closest(".modal");
    if (!modal) return;
    // The issued secret, the admin token and the form must not outlive the dialog.
    modal.querySelectorAll("input").forEach(function (input) {
      input.value = "";
      input.checked = false;
    });
    var output = document.getElementById("cloudru-iam-output");
    if (output) {
      output.textContent = "";
      output.classList.remove("visible");
    }
    window.LogosCoreAPI.ui.modal.close(modal);
  });

  document.addEventListener("DOMContentLoaded", build);
})();
