(function () {
    "use strict";

    function h(tag, attrs, text) {
        var node = document.createElement(tag);
        Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function closeModal() {
        var existing = document.getElementById("partner-relations-modal");
        if (existing) existing.remove();
    }

    function openPartnerRelationsModal() {
        closeModal();

        var overlay = h("div", { id: "partner-relations-modal", class: "alice-dept-overlay" });
        var card = h("div", { class: "alice-dept-card" });
        var head = h("div", { class: "alice-dept-head" });
        head.appendChild(h("h3", {}, "Partner Relations"));
        var close = h("button", { type: "button", "aria-label": "Закрыть" }, "×");
        close.onclick = closeModal;
        head.appendChild(close);
        card.appendChild(head);

        var notice = h("div", { style: "font-size:12px;opacity:.72;margin:8px 0 12px;" },
            "Партнёры и история принадлежат текущему пользователю. Отправка внешних сообщений выполняется через AI tool с подтверждением.");
        card.appendChild(notice);

        var form = h("form", { style: "display:grid;gap:8px;margin-bottom:14px;" });
        var name = h("input", { required: "required", placeholder: "Имя / компания", "aria-label": "Имя или компания" });
        var organization = h("input", { placeholder: "Организация", "aria-label": "Организация" });
        var email = h("input", { type: "email", placeholder: "Email", "aria-label": "Email" });
        var phone = h("input", { placeholder: "Телефон", "aria-label": "Телефон" });
        var create = h("button", { type: "submit" }, "Добавить партнёра");
        [name, organization, email, phone, create].forEach(function (n) { form.appendChild(n); });
        card.appendChild(form);

        var list = h("div", { class: "alice-dept-list" });
        list.textContent = "Загрузка…";
        card.appendChild(list);
        overlay.appendChild(card);
        document.body.appendChild(overlay);

        function load() {
            return fetch("/api/partners", { headers: { "Accept": "application/json" } })
                .then(function (r) {
                    return r.json().then(function (data) {
                        if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
                        return data;
                    });
                })
                .then(function (data) {
                    list.textContent = "";
                    var partners = Array.isArray(data.partners) ? data.partners : [];
                    if (!partners.length) {
                        list.appendChild(h("div", {}, "Пока нет партнёров."));
                        return;
                    }
                    partners.forEach(function (partner) {
                        var row = h("button", { type: "button", class: "alice-dept-row" });
                        row.appendChild(h("strong", {}, partner.name));
                        row.appendChild(h("span", {}, [partner.organization, partner.status, partner.email].filter(Boolean).join(" · ")));
                        row.onclick = function () {
                            fetch("/api/partners/" + encodeURIComponent(partner.id), { headers: { "Accept": "application/json" } })
                                .then(function (r) { return r.json().then(function (data) {
                                    if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
                                    return data.partner;
                                }); })
                                .then(showDetails)
                                .catch(function (err) { alert("Ошибка: " + err.message); });
                        };
                        list.appendChild(row);
                    });
                });
        }

        function showDetails(partner) {
            var detail = document.getElementById("partner-relations-detail");
            if (detail) detail.remove();
            detail = h("div", { id: "partner-relations-detail", style: "margin-top:14px;padding-top:14px;border-top:1px solid rgba(127,127,127,.25);" });
            detail.appendChild(h("h4", { style: "margin:0 0 8px;" }, partner.name + " · " + partner.status));

            var status = h("select", { "aria-label": "Статус партнёра" });
            ["lead","active","negotiating","paused","completed","archived"].forEach(function (value) {
                var option = h("option", { value: value }, value);
                if (value === partner.status) option.selected = true;
                status.appendChild(option);
            });
            var statusButton = h("button", { type: "button" }, "Сохранить статус");
            statusButton.onclick = function () {
                fetch("/api/partners/" + encodeURIComponent(partner.id) + "/status", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ status: status.value })
                }).then(function (r) { return r.json().then(function (data) {
                    if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
                    load();
                    showDetails(data.partner);
                }); }).catch(function (err) { alert("Ошибка: " + err.message); });
            };
            detail.appendChild(status);
            detail.appendChild(statusButton);

            detail.appendChild(h("h5", { style: "margin:14px 0 6px;" }, "Контакты (" + (partner.contacts || []).length + ")"));
            (partner.contacts || []).forEach(function (contact) {
                detail.appendChild(h("div", { style: "font-size:12px;padding:6px 0;" },
                    [contact.name, contact.role, contact.email || contact.phone].filter(Boolean).join(" · ")));
            });

            detail.appendChild(h("h5", { style: "margin:14px 0 6px;" }, "Переписка (" + (partner.messages || []).length + ")"));
            (partner.messages || []).forEach(function (message) {
                detail.appendChild(h("div", { style: "font-size:12px;padding:6px 0;" },
                    "[" + message.direction + "] " + message.subject + (message.body ? ": " + message.body.slice(0, 160) : "")));
            });

            detail.appendChild(h("h5", { style: "margin:14px 0 6px;" }, "Follow-up (" + (partner.followups || []).length + ")"));
            (partner.followups || []).forEach(function (followup) {
                var line = h("div", { style: "display:flex;gap:8px;align-items:center;font-size:12px;padding:6px 0;" });
                line.appendChild(h("span", {}, followup.title + " · " + followup.status));
                if (followup.status === "open") {
                    var done = h("button", { type: "button" }, "Готово");
                    done.onclick = function () {
                        fetch("/api/partners/followups/" + encodeURIComponent(followup.id) + "/complete", {
                            method: "POST", headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ status: "completed" })
                        }).then(function (r) { return r.json().then(function (data) {
                            if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
                            load().then(function () { showDetails(data.followup ? partner : partner); });
                        }); }).catch(function (err) { alert("Ошибка: " + err.message); });
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
            fetch("/api/partners", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    name: name.value,
                    organization: organization.value,
                    email: email.value,
                    phone: phone.value
                })
            }).then(function (r) { return r.json().then(function (data) {
                if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
                return data;
            }); }).then(function () {
                form.reset();
                return load();
            }).catch(function (err) {
                alert("Ошибка: " + err.message);
            }).finally(function () {
                create.disabled = false;
            });
        });

        overlay.addEventListener("click", function (event) {
            if (event.target === overlay) closeModal();
        });

        load().catch(function (err) { list.textContent = "Ошибка: " + err.message; });
    }

    window.openPartnerRelationsModal = openPartnerRelationsModal;
    window.closePartnerRelationsModal = closeModal;
})();
