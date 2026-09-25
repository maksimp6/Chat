(function(){
  "use strict";
  var modal=null;
  function el(tag,attrs,text){var n=document.createElement(tag);Object.keys(attrs||{}).forEach(function(k){n.setAttribute(k,attrs[k]);});if(text!==undefined)n.textContent=text;return n;}
  function close(){if(modal){modal.remove();modal=null;}}
  function open(){
    close();
    modal=el("div",{class:"alice-dept-overlay"});
    var card=el("div",{class:"alice-dept-card"});
    var head=el("div",{class:"alice-dept-head"}); head.appendChild(el("h3",{},"Departments"));
    var x=el("button",{"aria-label":"Закрыть"},"×"); x.onclick=close; head.appendChild(x); card.appendChild(head);
    var list=el("div",{class:"alice-dept-list"}); list.textContent="Загрузка…"; card.appendChild(list); modal.appendChild(card); document.body.appendChild(modal);
    fetch("/api/departments",{headers:{"Accept":"application/json"}}).then(function(r){if(!r.ok)throw new Error("HTTP "+r.status);return r.json();}).then(function(data){
      list.textContent="";
      (data.departments||[]).forEach(function(d){
        var row=el("button",{class:"alice-dept-row"});
        row.appendChild(el("strong",{},d.name)); row.appendChild(el("span",{},(d.description||d.type||"").slice(0,120)));
        row.onclick=function(){
        if (d.id === "partner-relations" && typeof window.openPartnerRelationsModal === "function") {
          close();
          window.openPartnerRelationsModal();
          return;
        }
        fetch("/api/departments/"+encodeURIComponent(d.id)+"/sessions",{method:"POST",headers:{"Accept":"application/json"}}).then(function(r){return r.json().then(function(x){return {ok:r.ok,data:x};});}).then(function(x){if(!x.ok){alert(x.data.error||"Не удалось начать сессию");return;} row.querySelector("span").textContent="Сессия: "+x.data.session.id;});
      };
        list.appendChild(row);
      });
      if(!list.children.length) list.appendChild(el("div",{},"Пока нет активных departments."));
    }).catch(function(e){list.textContent="Ошибка: "+e.message;});
  }
  window.openDepartmentsModal=open;
  window.closeDepartmentsModal=close;
})();
