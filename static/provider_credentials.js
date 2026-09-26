(function () {
"use strict";

function makeField(id, title, help, placeholder, type) {
    var wrap = document.createElement("div");
    wrap.className = "provider-field";
    var label = document.createElement("label");
    label.textContent = title;
    label.className = "provider-field-label";
    var input = document.createElement("input");
    input.id = id;
    input.type = type || "password";
    input.autocomplete = "new-password";
    input.placeholder = placeholder || "";
    input.className = "provider-field-input";
    if (help) {
        var small = document.createElement("div");
        small.textContent = help;
        small.className = "provider-field-help";
        wrap.appendChild(small);
    }
    var error = document.createElement("div");
    error.className = "provider-field-error";
    error.className = "provider-field-error";
    wrap.appendChild(label); wrap.appendChild(input); wrap.appendChild(error);
    return wrap;
}

function setFieldError(id, message) {
    var field = document.getElementById(id);
    if (!field) return;
    var error = field.parentElement && field.parentElement.querySelector(".provider-field-error");
    if (!error) return;
    error.textContent = message || "";
    error.classList.toggle("is-visible", Boolean(message));
    field.classList.toggle("has-error", Boolean(message));
}

function clearFieldErrors() {
    setFieldError("provider-yandex-key", "");
    setFieldError("provider-yandex-project", "");
    setFieldError("provider-cloudru-key", "");
}

function providerErrorMessage(data, provider) {
    if (data && data.error === "authorization_failed") {
        return provider === "cloudru"
            ? "Неверный Cloud.ru API key или ключ не имеет доступа к Foundation Models."
            : "Неверный Yandex Cloud API key или ключ не имеет доступа к AI Studio.";
    }
    if (data && data.error === "provider_health_check_failed") {
        return provider === "cloudru"
            ? "Не удалось проверить Cloud.ru API key: сервис недоступен или вернул ошибку."
            : "Не удалось проверить Yandex Cloud API key: сервис недоступен или вернул ошибку.";
    }
    if (data && data.error === "credential_storage_failed") {
        return "Ключ проверен, но не удалось сохранить его на сервере.";
    }
    return (data && (data.detail || data.error)) || "Не удалось подключить провайдера.";
}

function statusText(item) {
    if (!item) return "Не проверено";
    if (item.status === "not_configured") return "Не настроен";
    if (item.status === "configured") {
        return item.provider === "yandex"
            ? "Настроен • ключ сохранён"
            : "Настроен • требуется проверка";
    }
    if (item.status === "connected") return "Подключён • авторизация OK";
    if (item.status === "checking") return "Проверка ещё не выполнена";
    if (item.status === "invalid" || item.status === "forbidden" || item.error === "unauthorized") {
        return "Неверный ключ / нет доступа";
    }
    if (item.status === "unavailable") return "Провайдер недоступен";
    return "Статус неизвестен";
}

function renderStatus(container, providers) {
    container.innerHTML = "";
    (providers || []).forEach(function (item) {
        var card = document.createElement("div");
        card.className = "provider-status-card";
        var title = document.createElement("div");
        title.className = "provider-status-title";
        title.textContent = item.provider === "yandex" ? "Yandex Cloud" : "Cloud.ru";
        var line = document.createElement("div");
        line.textContent = statusText(item);
        line.className = "provider-status-line";
        var meta = document.createElement("div");
        meta.className = "provider-status-meta";
        var fp = item.credential && item.credential.fingerprint;
        var exp = item.credential && item.credential.expires_at;
        meta.textContent = (fp ? "fp: " + fp.slice(0, 12) : "fingerprint: —") + (exp ? " • expires: " + exp : "");
        card.appendChild(title); card.appendChild(line); card.appendChild(meta);
        container.appendChild(card);
    });
}

async function fetchStatus(output) {
    output.textContent = "Проверка…";
    try {
        var response = await window.AliceDispatcher.request("/api/provider-credentials/status", {cache:"no-store", credentials:"same-origin"});
        var data = await response.json();
        if (!response.ok) throw new Error("HTTP " + response.status);
        renderStatus(output, data.providers);
    } catch (error) { output.textContent = "Ошибка проверки: " + error.message; }
}

function build() {
    if (document.getElementById("provider-credentials-modal")) return;
    var modal = document.createElement("div");
    modal.id = "provider-credentials-modal";
    modal.className = "modal provider-credentials-modal";
    var box = document.createElement("div");
    box.className = "modal-content provider-credentials-box";
    var close = document.createElement("button");
    close.textContent = "×"; close.setAttribute("aria-label","Закрыть");
    close.className = "provider-credentials-close";
    close.addEventListener("click", function(){ modal.classList.remove("visible"); modal.setAttribute("aria-hidden", "true"); });
    var title=document.createElement("h3"); title.textContent="Провайдеры"; title.className="provider-credentials-title";
    box.appendChild(close); box.appendChild(title);

    box.appendChild(makeField("provider-yandex-key","Yandex Cloud API key","","Yandex API key","password"));
    box.appendChild(makeField("provider-yandex-project","Yandex Cloud Project ID","Обязателен вместе с Yandex API key.","например: b1g1fekh2198nuan1tnh","text"));
    box.appendChild(makeField("provider-cloudru-key","Cloud.ru API key","","Cloud.ru API key","password"));

    var actions=document.createElement("div"); actions.className="provider-credentials-actions";
    var save=document.createElement("button"); save.textContent="Подключить"; save.className="btn-primary";
    save.addEventListener("click", async function(){
        var y=document.getElementById("provider-yandex-key").value.trim();
        var project=document.getElementById("provider-yandex-project").value.trim();
        var cloudru=document.getElementById("provider-cloudru-key").value.trim();
        clearFieldErrors();
        output.textContent = "";
        if(y && !project){ setFieldError("provider-yandex-project", "Project ID обязателен вместе с Yandex API key."); return; }
        if(!y && project){ setFieldError("provider-yandex-key", "Введите Yandex Cloud API key."); return; }
        if(!y && !cloudru){
            setFieldError("provider-yandex-key", "Введите API key.");
            setFieldError("provider-cloudru-key", "Введите API key.");
            return;
        }
        save.disabled=true;
        try{
            var body={};
            if(y) { body.yandex_api_key=y; body.yandex_project_id=project; }
            if(cloudru) body.cloudru_api_key=cloudru;
            var response=await window.AliceDispatcher.request("/api/provider-credentials",{method:"PUT",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
            var data=await response.json();
            if(!response.ok){
                var provider = data && data.provider;
                var fieldId = provider === "cloudru" ? "provider-cloudru-key" : provider === "yandex" ? "provider-yandex-key" : null;
                var message = providerErrorMessage(data, provider);
                if (fieldId) setFieldError(fieldId, message);
                else output.textContent = message;
                return;
            }
            document.getElementById("provider-yandex-key").value="";
            document.getElementById("provider-yandex-project").value="";
            document.getElementById("provider-cloudru-key").value="";
            await fetchStatus(output);
        }catch(error){ output.textContent="Ошибка подключения: "+error.message; }
        finally { save.disabled=false; }
    });
    actions.appendChild(save); box.appendChild(actions);

    var output=document.createElement("div"); output.id="provider-credentials-status"; output.className="provider-credentials-status"; box.appendChild(output);
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", "provider-credentials-title");
    modal.setAttribute("aria-hidden", "true");
    title.id = "provider-credentials-title";
    modal.appendChild(box);
    var app = document.querySelector(".alice-pro-app");
    (app || document.body).appendChild(modal);
}

window.openProviderCredentialsModal=async function(){
    build();
    var modal=document.getElementById("provider-credentials-modal"); modal.classList.add("visible"); modal.setAttribute("aria-hidden", "false");
    var output=document.getElementById("provider-credentials-status");
    await fetchStatus(output);
};
document.addEventListener("DOMContentLoaded",build);
})();