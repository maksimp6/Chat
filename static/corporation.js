(function () {
  "use strict";

  // The board window of the corporation (docs/agents/logos-corporation.md): the cycle, the cash,
  // the directives, the projects with their tasks, products and offers, the buyers, the ledger's
  // tail. It only shows (the windows rule); the owner steers in the chat with "корпорация: …".
  // POST /api/corporation {action: status|projects|ledger} (corporation/routes.py).

  var MODAL_ID = "corporation-modal";
  var REFRESH_MS = 15000;
  var el = window.LogosCoreAPI.ui.element;

  function money(value) {
    var number = Number(value || 0);
    return number.toLocaleString("ru-RU", { maximumFractionDigits: 0 }) + " ₽";
  }

  function when(stamp) {
    if (!stamp) return "—";
    var date = new Date(stamp);
    return isNaN(date.getTime()) ? String(stamp) : date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  function post(action, extra) {
    var body = Object.assign({ action: action }, extra || {});
    return window.LogosDispatcher.request("/api/corporation", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    }).then(function (response) {
      if (!response.ok) return window.LogosHttpError(response);
      return response.json();
    });
  }

  function section(title) {
    var node = el("div", "corp-section");
    node.appendChild(el("div", "corp-section-title", title));
    return node;
  }

  function line(parent, text, className) {
    parent.appendChild(el("div", className || "corp-line", text));
  }

  var STATUS_WORDS = { queued: "в очереди", working: "в работе", done: "сделано", failed: "провалено",
    draft: "черновик", rework: "на доработке", ready: "готов", published: "опубликован", open: "открыт", closed: "закрыт" };

  function word(status) {
    return STATUS_WORDS[status] || status || "";
  }

  function render(body, status, data) {
    body.textContent = "";
    var summary = status.summary || {};
    var cycle = summary.cycle || {};
    var cash = status.cash || {};

    var head = section("Цикл");
    line(head, (cycle.running ? "Работает" : "Остановлен") + "; шагов " + (cycle.steps || 0) + "; последний ход " + when(cycle.last_tick) + (cycle.last_role ? " (" + cycle.last_role + ")" : "") + "; поток " + (status.thread_alive ? "жив" : "нет") + ".");
    line(head, "Модель раннера: " + (status.runner_model || "недоступен") + (status.current && status.current.role ? "; сейчас: " + status.current.role + (status.current.task ? " " + status.current.task : "") : "") + ".");
    var channels = summary.channels || {};
    var names = Object.keys(channels);
    line(head, "Каналы: " + (names.length ? names.map(function (name) { return name + " " + (channels[name].enabled ? "вкл" : "выкл"); }).join(", ") : "нет") + "; ключи: " + Object.keys(status.channels_configured || {}).filter(function (k) { return status.channels_configured[k]; }).join(", ") + ".");
    body.appendChild(head);

    var till = section("Касса");
    line(till, "Подтверждено " + money(cash.confirmed) + ", предложено " + money(cash.proposed) + ", расходы " + money(cash.expenses) + " (электричество " + money(cash.power_rub) + "), итог " + money(cash.net) + "; продаж " + (cash.sales || 0) + ".");
    body.appendChild(till);

    var directives = section("Директивы правления");
    (data.directives || []).slice(-10).reverse().forEach(function (d) {
      line(directives, "[" + d.seq + "] " + when(d.at) + " " + d.text + (d.project ? " → " + d.project : " (ждёт директора)"));
    });
    if (!(data.directives || []).length) line(directives, "Нет. Дайте первую: «корпорация: директива …»", "corp-muted");
    body.appendChild(directives);

    var projects = section("Проекты");
    (data.projects || []).slice(-8).reverse().forEach(function (p) {
      var card = el("div", "corp-card");
      card.appendChild(el("div", "corp-card-title", p.id + " · " + p.title + " · " + word(p.status)));
      if (p.goal) line(card, p.goal, "corp-muted");
      (p.tasks || []).slice(-12).forEach(function (t) {
        line(card, t.id + " [" + t.role + "] " + word(t.status) + (t.turns ? ", ходов " + t.turns : "") + ": " + String(t.text || "").slice(0, 160));
      });
      (p.products || []).forEach(function (pr) {
        line(card, "Продукт " + pr.id + " «" + pr.title + "» — " + word(pr.status) + " · " + pr.path);
      });
      (p.offers || []).forEach(function (o) {
        line(card, "Оффер " + o.id + " — " + money(o.price) + ", " + o.channel + ", " + word(o.status));
      });
      projects.appendChild(card);
    });
    if (!(data.projects || []).length) line(projects, "Проектов пока нет.", "corp-muted");
    body.appendChild(projects);

    var clients = section("Покупатели");
    (data.clients || []).slice(-10).forEach(function (c) {
      var last = (c.messages || [])[c.messages.length - 1];
      line(clients, c.id + (c.name ? " " + c.name : "") + " (" + (c.channel || "") + "): " + (last ? last.from + ": " + String(last.text).slice(0, 120) : "") + (c.paid && c.paid.length ? " · оплатил " + c.paid.join(", ") : ""));
    });
    if (!(data.clients || []).length) line(clients, "Никто ещё не писал.", "corp-muted");
    body.appendChild(clients);

    var ledger = section("Журнал (последние записи)");
    (data.ledger || []).slice().reverse().forEach(function (e) {
      var d = e.data || {};
      var what = d.text || d.reason || d.result || d.reply || d.title || d.error || "";
      line(ledger, e.seq + " " + when(e.at) + " " + e.kind + " [" + (e.actor || "") + "] " + String(what).slice(0, 140), "corp-ledger-line");
    });
    var verify = data.verify || {};
    line(ledger, "Записей " + (verify.records || 0) + "; цепочка " + (verify.ok ? "цела" : "ПОВРЕЖДЕНА на " + verify.broken_at) + "; снимок " + when(summary.last_backup && summary.last_backup.at) + ".", "corp-muted");
    body.appendChild(ledger);
  }

  function open() {
    var existing = document.getElementById(MODAL_ID);
    if (existing) existing.remove();
    var UI = window.LogosCoreAPI.ui;
    var body = el("div", "corp-body");
    var status = el("div", "corp-status", "Загрузка…");
    status.setAttribute("role", "status");
    var content = el("div");
    body.appendChild(status);
    body.appendChild(content);
    var modal = UI.modal.create({ id: MODAL_ID, title: "Корпорация", body: body });
    UI.modal.open(modal);
    var timer = null;

    function load() {
      if (!document.getElementById(MODAL_ID)) { if (timer) clearInterval(timer); return; }
      Promise.all([post("status"), post("projects"), post("ledger", { count: 40 })]).then(function (parts) {
        status.textContent = "Обновлено " + new Date().toLocaleTimeString("ru-RU") + ". Управление — в чате: «корпорация: статус», «корпорация: директива …».";
        render(content, parts[0], Object.assign({}, parts[1], { ledger: parts[2].entries, verify: parts[2].verify }));
      }).catch(function (error) {
        status.textContent = "Не удалось загрузить: " + ((error && error.message) || error);
      });
    }

    load();
    timer = setInterval(load, REFRESH_MS);
  }

  window.openCorporationModal = open;
})();
