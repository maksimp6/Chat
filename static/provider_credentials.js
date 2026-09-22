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
    var small = document.createElement("div");
    small.textContent = help;
    small.style.cssText = "margin-top:5px;font-size:12px;opacity:.7;line-height:1.35;";
    wrap.appendChild(label); wrap.appendChild(input); wrap.appendChild(small);
    return wrap;
}

function statusText(item) {
    if (!item) return "Не проверено";
    if (item.status === "not_configured") return "Не настроен";
    if (item.status === "connected") return "Подключён • авторизация OK";
    if (item.status === "checking") return "Проверка ещё не выполнена";
    if (item.error === "unauthorized") return "Неверный ключ / нет доступа";
    return "Провайдер недоступен";
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
        var rotation = document.createElement("div");
        var r = item.rotation || {};
        rotation.textContent = r.supported ? (r.due ? "Автоперевыпуск: требуется" : "Автоперевыпуск: настроен") : "Автоперевыпуск: недоступен";
        rotation.style.cssText = "margin-top:4px;font-size:12px;opacity:.8;";
        var meta = document.createElement("div");
        meta.style.cssText = "margin-top:7px;font-size:11px;opacity:.65;";
        var fp = item.credential && item.credential.fingerprint;
        var exp = item.credential && item.credential.expires_at;
        meta.textContent = (fp ? "fp: " + fp.slice(0, 12) : "fingerprint: —") + (exp ? " • expires: " + exp : "");
        card.appendChild(title); card.appendChild(line); card.appendChild(rotation); card.appendChild(meta);
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

    box.appendChild(makeField("provider-yandex-key","Yandex Cloud API key","Ключ Yandex Cloud. Он проверяется перед сохранением.","Введите Yandex API key"));

    var cloudru=document.createElement("div");
    cloudru.style.cssText="margin:18px 0;padding:14px;border:1px solid #444;border-radius:9px;";
    var h=document.createElement("div"); h.textContent="Cloud.ru"; h.style.cssText="font-weight:700;margin-bottom:8px;";
    cloudru.appendChild(h);
    cloudru.appendChild(makeField("cloudru-iam-json","IAM credentials JSON","Загрузите JSON с Key ID и Key Secret. Alice Pro использует его только для IAM управления.","","file"));
    document.getElementById("cloudru-iam-json").accept=".json,application/json";
document.getElementById("cloudru-iam-json").addEventListener("change", async function(){
    var file=this.files[0]; if(!file) return;
    var form=new FormData(); form.append("iam_json",file);
    try{
        var sr=await fetch("/api/provider-credentials/cloudru/service-accounts",{method:"POST",credentials:"same-origin",body:form});
        var sd=await sr.json(); if(!sr.ok) throw new Error(sd.detail || sd.error || ("HTTP "+sr.status));
        var sel=document.getElementById("cloudru-service-account-id");
        sel.innerHTML='<option value="">Создать новый</option>';
        (sd.service_accounts||[]).forEach(function(sa){
            var opt=document.createElement("option"); opt.value=sa.id; opt.textContent=(sa.name||sa.id)+" • "+(sa.project_id||"");
            sel.appendChild(opt);
        });
        output.textContent="IAM JSON проверен. Доступно сервисных аккаунтов: "+(sd.service_accounts||[]).length;
    }catch(error){ output.textContent="Не удалось получить сервисные аккаунты: "+error.message; }
});
    cloudru.appendChild(makeField("cloudru-project-id","Project ID","Проект, в котором должен работать сервисный аккаунт.","Cloud.ru project ID","text"));
    var saWrap=document.createElement("div"); saWrap.style.cssText="margin:14px 0;";
var saLabel=document.createElement("label"); saLabel.textContent="Service account"; saLabel.style.cssText="display:block;font-weight:600;margin-bottom:6px;";
var saSelect=document.createElement("select"); saSelect.id="cloudru-service-account-id"; saSelect.style.cssText="width:100%;box-sizing:border-box;padding:10px;border:1px solid #555;border-radius:7px;background:var(--input-bg,#222);color:var(--text,#fff);";
saSelect.innerHTML='<option value="">Создать новый</option>';
var saHelp=document.createElement("div"); saHelp.textContent="После загрузки IAM JSON здесь появятся доступные сервисные аккаунты. Можно выбрать существующий или создать новый.";
saHelp.style.cssText="margin-top:5px;font-size:12px;opacity:.7;line-height:1.35;";
saWrap.appendChild(saLabel); saWrap.appendChild(saSelect); saWrap.appendChild(saHelp); cloudru.appendChild(saWrap);
cloudru.appendChild(makeField("cloudru-service-account-name","Имя нового service account","Используется только если выбран вариант «Создать новый».","Alice Pro","text"));
    box.appendChild(cloudru);

    var note=document.createElement("div");
    note.textContent="Cloud.ru API key для Foundation Models вводить не нужно. Alice Pro создаёт его для сервисного аккаунта, проверяет и затем ежедневно перевыпускает. IAM JSON не возвращается во frontend.";
    note.style.cssText="padding:10px;border-radius:7px;background:rgba(127,127,127,.12);font-size:12px;line-height:1.4;";
    box.appendChild(note);

    var actions=document.createElement("div"); actions.style.cssText="display:flex;gap:8px;margin-top:14px;";
    var save=document.createElement("button"); save.textContent="Подключить Cloud.ru"; save.className="btn-primary";
    save.onclick=async function(){
        
        var project=document.getElementById("cloudru-project-id").value.trim();
        var saId=document.getElementById("cloudru-service-account-id").value;
var name=document.getElementById("cloudru-service-account-name").value.trim() || "Alice Pro";
        var y=document.getElementById("provider-yandex-key").value.trim();
        var keyId=document.getElementById("cloudru-iam-key-id").value.trim();
var keySecret=document.getElementById("cloudru-iam-key-secret").value;
if(!keyId && !y){ output.textContent="Введите Cloud.ru IAM Key ID или Yandex API key."; return; }
if(keyId && !keySecret){ output.textContent="Введите Cloud.ru IAM Key Secret."; return; }
        try{
            if(y){
                var yr=await fetch("/api/provider-credentials",{method:"PUT",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify({yandex_api_key:y})});
                var yd=await yr.json(); if(!yr.ok) throw new Error(yd.error || ("HTTP "+yr.status));
            }
            if(keyId){
                output.textContent="Подключение Cloud.ru…";
                var form=new FormData(); form.append("iam_key_id",keyId); form.append("iam_key_secret",keySecret); form.append("project_id",project); form.append("service_account_name",name); if(saId) form.append("service_account_id",saId);
                var cr=await fetch("/api/provider-credentials/cloudru/bootstrap",{method:"POST",credentials:"same-origin",body:form});
                var cd=await cr.json(); if(!cr.ok) throw new Error(cd.detail || cd.error || ("HTTP "+cr.status));
                output.textContent="Cloud.ru подключён. Service account: "+cd.service_account_id+" • API key: "+cd.provider_key_id+" • ротация: ежедневно.";
            }
            document.getElementById("provider-yandex-key").value="";
            document.getElementById("cloudru-iam-key-id").value=""; document.getElementById("cloudru-iam-key-secret").value="";
            await fetchStatus(output);
        }catch(error){ output.textContent="Подключение не выполнено: "+error.message; }
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