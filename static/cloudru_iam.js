(function () {
"use strict";

function makeInput(id, label, type, placeholder) {
    var wrap = document.createElement("label");
    wrap.style.cssText = "display:block;margin:10px 0;";
    var span = document.createElement("span");
    span.textContent = label;
    span.style.display = "block";
    span.style.marginBottom = "4px";
    var input = document.createElement("input");
    input.id = id;
    input.type = type || "text";
    input.placeholder = placeholder || "";
    input.style.cssText = "width:100%;box-sizing:border-box;padding:8px;";
    wrap.appendChild(span);
    wrap.appendChild(input);
    return wrap;
}

function value(id) {
    var node = document.getElementById(id);
    return node ? node.value.trim() : "";
}

function build() {
    if (document.getElementById("cloudru-iam-modal")) return;
    var modal = document.createElement("div");
    modal.id = "cloudru-iam-modal";
    modal.style.cssText = "display:none;position:fixed;inset:0;z-index:100001;background:rgba(0,0,0,.7);align-items:center;justify-content:center;";
    var box = document.createElement("div");
    box.style.cssText = "background:var(--bg,#1e1e1e);color:var(--text,#fff);padding:20px;border-radius:10px;width:min(520px,92vw);max-height:88vh;overflow:auto;position:relative;";
    var close = document.createElement("button");
    close.textContent = "×";
    close.style.cssText = "position:absolute;right:12px;top:8px;font-size:24px;background:none;border:0;color:inherit;";
    close.onclick = function () { modal.style.display = "none"; };
    var title = document.createElement("h3");
    title.textContent = "Cloud.ru IAM: выпуск API-ключа";
    box.appendChild(close);
    box.appendChild(title);
    box.appendChild(makeInput("cloudru-iam-admin-token", "Admin token (для удалённого deployment)", "password", "не нужен на localhost"));
    box.appendChild(makeInput("cloudru-iam-service-account", "Service account ID", "text", "UUID"));
    box.appendChild(makeInput("cloudru-iam-name", "Название ключа", "text", "Alice Pro"));
    box.appendChild(makeInput("cloudru-iam-description", "Описание", "text", "Для Alice Pro"));
    box.appendChild(makeInput("cloudru-iam-products", "Сервисы Cloud.ru, через запятую", "text", "monaas, foundation-models"));
    box.appendChild(makeInput("cloudru-iam-expires", "Срок действия", "datetime-local"));
    box.appendChild(makeInput("cloudru-iam-ips", "IP/подсети, через запятую", "text", "203.0.113.0/24"));
    var confirmWrap = document.createElement("label");
    confirmWrap.style.cssText = "display:block;margin:14px 0;";
    var confirm = document.createElement("input");
    confirm.id = "cloudru-iam-confirm";
    confirm.type = "checkbox";
    confirmWrap.appendChild(confirm);
    confirmWrap.appendChild(document.createTextNode(" Я подтверждаю выпуск ключа с указанными правами."));
    box.appendChild(confirmWrap);
    var submit = document.createElement("button");
    submit.textContent = "Выпустить ключ";
    submit.className = "btn-primary";
    submit.onclick = submitWizard;
    box.appendChild(submit);
    var output = document.createElement("pre");
    output.id = "cloudru-iam-output";
    output.style.cssText = "display:none;white-space:pre-wrap;word-break:break-word;background:#222;padding:12px;border-radius:6px;margin-top:12px;";
    box.appendChild(output);
    modal.appendChild(box);
    document.body.appendChild(modal);
}

async function submitWizard() {
    var output = document.getElementById("cloudru-iam-output");
    output.style.display = "block";
    output.textContent = "Выпуск ключа…";
    var products = value("cloudru-iam-products").split(",").map(function (x) { return x.trim(); }).filter(Boolean);
    var ips = value("cloudru-iam-ips").split(",").map(function (x) { return x.trim(); }).filter(Boolean);
    var expiry = value("cloudru-iam-expires");
    if (expiry) expiry = new Date(expiry).toISOString();
    var token = value("cloudru-iam-admin-token");
    var headers = { "Content-Type": "application/json" };
    if (token) headers.Authorization = "Bearer " + token;
    try {
        var response = await fetch("/api/cloudru/iam/api-keys", {
            method: "POST",
            headers: headers,
            body: JSON.stringify({
                service_account_id: value("cloudru-iam-service-account"),
                name: value("cloudru-iam-name"),
                description: value("cloudru-iam-description"),
                products: products,
                expires_at: expiry || null,
                ip_addresses: ips,
                confirm: Boolean(document.getElementById("cloudru-iam-confirm").checked)
            })
        });
        var data = await response.json();
        if (!response.ok) throw new Error(data.error || ("HTTP " + response.status));
        output.textContent = "Ключ создан. ID: " + data.id + "\nKey reference: " + data.key_ref + "\n\nKEY SECRET (сохраните сейчас):\n" + data.secret + "\n\nSecret больше не показывается через Cloud.ru после закрытия окна.";
    } catch (error) {
        output.textContent = "Ошибка: " + error.message;
    }
}

window.openCloudRuIamWizard = function () {
    build();
    document.getElementById("cloudru-iam-modal").style.display = "flex";
};
document.addEventListener("DOMContentLoaded", build);
})();