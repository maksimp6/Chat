(function () {
"use strict";

function makeField(id, title, help, placeholder, type) {
    var wrap = document.createElement("div");
    wrap.style.cssText = "margin:14px 0;";
    var label = document.createElement("label");
    label.textContent = title;
    label.style.cssText = "display:block;font-weight:600;margin-bottom:6px;";
    var input = document.createElement("input");
    input.id = id;
    input.type = type || "password";
    input.autocomplete = "new-password";
    input.placeholder = placeholder || "";
    input.style.cssText = "width:100%;box-sizing:border-box;padding:10px;border:1px solid #555;border-radius:7px;background:var(--input-bg,#222);color:var(--text,#fff);";
    if (help) {
        var small = document.createElement("div");
        small.textContent = help;
        small.style.cssText = "margin-top:5px;font-size:12px;opacity:.7;line-height:1.35;";
        wrap.appendChild(small);
    }
    var error = document.createElement("div");
    error.className = "provider-field-error";
    error.style.cssText = "display:none;margin-top:6px;font-size:12px;color:#ff6b6b;line-height:1.35;";
    wrap.appendChild(label); wrap.appendChild(input); wrap.appendChild(error);
    return wrap;
}

function setFieldError(id, message) {
    var field = document.getElementById(id);
    if (!field) return;
    var error = field.parentElement && field.parentElement.querySelector(".provider-field-error");
    if (!error) return;
    error.textContent = message || "";
    error.style.display = message ? "block" : "none";
    field.style.borderColor = message ? "#ff6b6b" : "#555";
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
        card.style.cssText = "padding:12px;margin:8px 0;border:1px solid #444;border-radius:8px;";
        var title = document.createElement("div");
        title.style.fontWeight = "700";
        title.textContent = item.provider === "yandex" ? "Yandex Cloud" : "Cloud.ru";
        var line = document.createElement("div");
        line.textContent = statusText(item);
        line.style.marginTop = "5px";
        var meta = document.createElement("div");
        meta.style.cssText = "margin-top:7px;font-size:11px;opacity:.65;";
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
        var response = await fetch("/api/provider-credentials/status", {cache:"no-store", credentials:"same-origin"});
        var data = await response.json();
        if (!response.ok) throw new Error("HTTP " + response.status);
        renderStatus(output, data.providers);
    } catch (error) { output.textContent = "Ошибка проверки: " + error.message; }
}

function build() {
    if (document.getElementById("provider-credentials-modal")) return;
    var modal = document.createElement("div");
    modal.id = "provider-credentials-modal";
    modal.style.cssText = "display:none;position:fixed;inset:0;z-index:100002;background:rgba(0,0,0,.72);align-items:center;justify-content:center;";
    var box = document.createElement("div");
    box.style.cssText = "background:var(--bg,#1e1e1e);color:var(--text,#fff);padding:20px;border-radius:12px;width:min(560px,92vw);max-height:90vh;overflow:auto;position:relative;box-shadow:0 10px 30px rgba(0,0,0,.35);";
    var close = document.createElement("button");
    close.textContent = "×"; close.setAttribute("aria-label","Закрыть");
    close.style.cssText = "position:absolute;right:12px;top:8px;font-size:24px;background:none;border:0;color:inherit;cursor:pointer;";
    close.onclick = function(){ modal.style.display="none"; };
    var title=document.createElement("h3"); title.textContent="Провайдеры"; title.style.marginTop="0";
    box.appendChild(close); box.appendChild(title);

    box.appendChild(makeField("provider-yandex-key","Yandex Cloud API key","","Yandex API key","password"));
    box.appendChild(makeField("provider-yandex-project","Yandex Cloud Project ID","Обязателен вместе с Yandex API key.","например: b1g1fekh2198nuan1tnh","text"));
    box.appendChild(makeField("provider-cloudru-key","Cloud.ru API key","","Cloud.ru API key","password"));

    var actions=document.createElement("div"); actions.style.cssText="display:flex;gap:8px;margin-top:14px;";
    var save=document.createElement("button"); save.textContent="Подключить"; save.className="btn-primary";
    save.onclick=async function(){
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
            var response=await fetch("/api/provider-credentials",{method:"PUT",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
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
    };
    actions.appendChild(save); box.appendChild(actions);

    var output=document.createElement("div"); output.id="provider-credentials-status"; output.style.marginTop="14px"; box.appendChild(output);
    modal.appendChild(box); document.body.appendChild(modal);
}

window.openProviderCredentialsModal=async function(){
    build();
    var modal=document.getElementById("provider-credentials-modal"); modal.style.display="flex";
    var output=document.getElementById("provider-credentials-status");
    await fetchStatus(output);
};
document.addEventListener("DOMContentLoaded",build);
})();