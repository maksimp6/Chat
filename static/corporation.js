(function () {
  "use strict";

  // The company window of the corporation (docs/agents/logos-corporation.md), laid out as the
  // owner's pictures of 08.11.2026 ("Logos Corp"): the working day as a line of events on the
  // left, the departments as rows in the middle (a face and a name, what the department is for,
  // three check marks of what it did today, a badge), the company's numbers and the latest events
  // on the right, the day's results below; under them the factory floor as a cartoon draws it
  // (the belt of stations, bins, the chute, the warehouse) and the books. Every number and every
  // check mark comes from the ledger (POST /api/corporation {action: conveyor|projects|ledger});
  // nothing is invented, and an empty day says so. The window only shows (the windows rule);
  // the owner steers in the chat with "корпорация: …".

  var MODAL_ID = "corporation-modal";
  var REFRESH_MS = 10000;
  var el = window.LogosCoreAPI.ui.element;

  function money(value) {
    var number = Number(value || 0);
    return "₽ " + number.toLocaleString("ru-RU", { maximumFractionDigits: 0 });
  }

  function clock(stamp) {
    if (!stamp) return "—";
    var date = new Date(stamp);
    return isNaN(date.getTime()) ? String(stamp) : date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
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

  function dot(on) {
    return el("span", "corp-dot " + (on ? "corp-dot-on" : "corp-dot-off"));
  }

  var STATUS_WORDS = { queued: "В очереди", working: "В работе", done: "Завершено", failed: "Провалено",
    draft: "черновик", rework: "на доработке", ready: "готов", published: "опубликован", open: "открыт", closed: "закрыт" };

  function word(status) {
    return STATUS_WORDS[status] || status || "";
  }

  // ----- the header -----------------------------------------------------------------------------

  function header(f) {
    var c = f.company || {};
    var node = el("div", "corp-header");
    var brand = el("div", "corp-brand");
    brand.appendChild(el("div", "corp-brand-title", "Logos Corp"));
    brand.appendChild(el("div", "corp-muted", "Компания на одном интеллекте. Без людей внутри: владелец снаружи."));
    node.appendChild(brand);
    var pill = el("div", "corp-pill " + (c.running ? "corp-pill-on" : "corp-pill-off"));
    pill.appendChild(dot(c.running));
    pill.appendChild(document.createTextNode(c.running ? " Компания работает" : " Компания остановлена"));
    pill.appendChild(el("div", "corp-muted", c.running ? "Автоматический режим с " + when(c.since) : "«корпорация: старт» включит смену"));
    node.appendChild(pill);
    var now = el("div", "corp-now");
    now.appendChild(el("div", "corp-muted", "Сегодня, " + new Date().toLocaleDateString("ru-RU")));
    now.appendChild(el("div", "corp-now-time", new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })));
    node.appendChild(now);
    return node;
  }

  // ----- the working day: the line on the left ---------------------------------------------------

  function timeline(f) {
    var node = el("div", "corp-timeline");
    node.appendChild(el("div", "corp-panel-title", "📅 Рабочий день"));
    var rows = (f.timeline || []).slice(-18);
    if (!rows.length) line(node, "Сегодня записей ещё нет.", "corp-muted");
    rows.forEach(function (r) {
      var row = el("div", "corp-tl-row");
      row.appendChild(el("div", "corp-tl-dot"));
      var body = el("div", "corp-tl-body");
      body.appendChild(el("div", "corp-tl-time", clock(r.at)));
      body.appendChild(el("div", "corp-tl-label", r.label + " · " + r.who));
      if (r.what) body.appendChild(el("div", "corp-muted corp-tl-what", r.what));
      row.appendChild(body);
      node.appendChild(row);
    });
    return node;
  }

  // ----- the departments: rows in the middle -----------------------------------------------------

  function departmentRows(f) {
    var list = el("div", "corp-rows");
    var o = f.orchestrator || {};
    (f.departments || []).forEach(function (d) {
      var row = el("div", "corp-row" + (d.active ? " corp-card-active" : ""));
      var face = el("div", "corp-face");
      var people = d.people || [];
      face.appendChild(el("div", "corp-face-sign", people.length ? people.map(function (p) { return p.avatar; }).join("") : d.sign));
      face.appendChild(el("div", "corp-face-name", people.length ? people.map(function (p) { return p.name; }).join(" и ") : "код"));
      row.appendChild(face);
      var mid = el("div", "corp-row-mid");
      var head = el("div", "corp-row-head");
      head.appendChild(el("span", "corp-row-name", d.name));
      head.appendChild(el("span", "corp-tag", d.roles && d.roles.length ? d.roles.join(" · ") : "без модели"));
      mid.appendChild(head);
      mid.appendChild(el("div", "corp-muted", d.id === "orchestrator" && o.report ? "«" + o.report + "»" : (d.does || []).join(". ") + "."));
      row.appendChild(mid);
      var checks = el("ul", "corp-checks corp-row-checks");
      var facts = d.facts || [];
      if (facts.length) facts.forEach(function (t) { checks.appendChild(el("li", "", t)); });
      else checks.appendChild(el("li", "corp-check-none", f.whistle ? "сегодня ещё ничего" : "смена не идёт"));
      row.appendChild(checks);
      var badge = el("div", "corp-badge corp-badge-" + (d.badge === "Готово" ? "done" : d.badge === "В работе" ? "work" : d.badge === "В очереди" ? "queue" : "idle"), d.badge || "");
      row.appendChild(badge);
      list.appendChild(row);
    });
    return list;
  }

  // ----- the numbers and the latest events: the right column ------------------------------------

  function sidebar(f) {
    var node = el("div", "corp-side");
    var panel = el("div", "corp-panel");
    panel.appendChild(el("div", "corp-panel-title", "📈 Показатели компании"));
    var t = f.today || {}, m = f.metrics || {}, c = f.company || {}, cash = f.cash || {};
    function metric(key, value, note, good) {
      var block = el("div", "corp-metric");
      block.appendChild(el("div", "corp-muted", key));
      block.appendChild(el("div", "corp-metric-val" + (good ? " corp-good" : ""), value));
      if (note) block.appendChild(el("div", "corp-muted", note));
      panel.appendChild(block);
    }
    metric("Доход за сегодня", money(t.revenue), (t.sales || 0) + " продаж · всего " + money(cash.confirmed), t.revenue > 0);
    metric("Задачи выполнено", t.tasks_pct === null || t.tasks_pct === undefined ? "—" : t.tasks_pct + "%", (t.tasks_done || 0) + " сделано, " + (t.tasks_failed || 0) + " провалено, " + (t.tasks_opened || 0) + " поставлено");
    metric("Среднее время хода", m.avg_turn_seconds ? m.avg_turn_seconds + " с" : "—", "один ход одной роли");
    metric("Модель раннера", f.runner_model || "недоступен", "шагов " + (m.steps || 0) + " · записей " + (m.records || 0));
    metric("Рост за месяц", c.growth_month_pct === null || c.growth_month_pct === undefined ? "нет прошлого месяца" : (c.growth_month_pct >= 0 ? "+" : "") + c.growth_month_pct + "%", "по подтверждённой выручке", c.growth_month_pct > 0);
    node.appendChild(panel);

    var events = el("div", "corp-panel");
    events.appendChild(el("div", "corp-panel-title", "🔔 Последние события"));
    var rows = (f.timeline || []).slice(-6).reverse();
    if (!rows.length) line(events, "Пока тихо.", "corp-muted");
    rows.forEach(function (r) {
      var row = el("div", "corp-event");
      row.appendChild(el("span", "corp-event-time", clock(r.at)));
      row.appendChild(el("span", "", r.label + (r.what ? ": " + r.what : "")));
      events.appendChild(row);
    });
    node.appendChild(events);
    return node;
  }

  // ----- the day's results ------------------------------------------------------------------------

  function results(f) {
    var t = f.today || {};
    var node = el("div", "corp-bottom");
    var sum = el("div", "corp-panel");
    sum.appendChild(el("div", "corp-panel-title", "🏁 Итоги дня"));
    var list = el("ul", "corp-checks");
    var facts = [];
    if (t.tasks_done) facts.push("Сделано задач: " + t.tasks_done + (t.tasks_failed ? " (провалено " + t.tasks_failed + ")" : ""));
    if (t.products) facts.push("Заявлено продуктов: " + t.products);
    if (t.sales) facts.push("Продаж: " + t.sales + " на " + money(t.revenue));
    if (t.clients) facts.push("Писали покупатели: " + t.clients);
    if (!facts.length) facts.push(f.whistle ? "День только начался: конвейер работает, итогов ещё нет." : "Смена не идёт.");
    facts.forEach(function (x) { list.appendChild(el("li", "", x)); });
    sum.appendChild(list);
    node.appendChild(sum);

    var mood = el("div", "corp-panel corp-mood");
    var good = (t.sales || 0) > 0 || (t.tasks_done || 0) > 0;
    mood.appendChild(el("div", "corp-mood-title", good ? "🎉 Есть результат" : (f.whistle ? "🏗️ Работаем" : "💤 Тишина")));
    mood.appendChild(el("div", "corp-muted", good ? "Компания сегодня что-то сделала; что именно — слева, по строке." : (f.whistle ? "Конвейер идёт; как только появится сделанное, оно встанет в строку дня." : "Включите смену: «корпорация: старт». Основательница Вера напишет первую директиву сама.")));
    node.appendChild(mood);

    var principles = el("div", "corp-panel");
    principles.appendChild(el("div", "corp-panel-title", "⚖️ Принципы"));
    var pl = el("ul", "corp-checks");
    ["Журнал — единственная правда", "Один раннер, один шаг, одна смена", "Наружу — только через ворота Суда", "Владелец: цель, ключи, суд; остальное — компания"].forEach(function (p) { pl.appendChild(el("li", "", p)); });
    principles.appendChild(pl);
    node.appendChild(principles);
    return node;
  }

  // ----- the factory floor -----------------------------------------------------------------------

  function station(s, parts) {
    var node = el("div", "corp-station" + (s.active ? " corp-station-active" : ""));
    var head = el("div", "corp-station-head");
    head.appendChild(el("span", "corp-station-sign", s.sign));
    head.appendChild(el("span", "corp-station-name", s.name));
    node.appendChild(head);
    node.appendChild(el("div", "corp-station-does", s.does));
    var bin = el("div", "corp-bin");
    bin.appendChild(el("div", "corp-bin-label", "бункер: " + (s.bin_count || 0)));
    (s.bin || []).slice(0, 3).forEach(function (item) {
      var blank = el("div", "corp-blank", "▭ " + item.id);
      blank.title = item.text || "";
      bin.appendChild(blank);
    });
    node.appendChild(bin);
    (s.working || []).forEach(function (item) {
      var working = el("div", "corp-working", "⚡ " + item.id + " · ход " + ((item.turns || 0) + 1));
      working.title = item.text || "";
      node.appendChild(working);
    });
    var mine = (parts || []).filter(function (p) { return p.station === s.id; });
    if (mine.length) {
      var tray = el("div", "corp-tray");
      mine.slice(0, 4).forEach(function (p) {
        var box = el("div", "corp-part corp-part-" + p.status, "📦 " + p.id);
        box.title = p.title + " — " + p.note;
        tray.appendChild(box);
      });
      node.appendChild(tray);
    }
    var counters = el("div", "corp-counters");
    counters.appendChild(el("span", "", "✔ " + (s.done || 0)));
    if (s.failed) counters.appendChild(el("span", "corp-fail", "✘ " + s.failed));
    node.appendChild(counters);
    if (s.note) node.appendChild(el("div", "corp-muted", s.note));
    return node;
  }

  function floor(f) {
    var wrap = section("🏭 Цех: конвейер");
    var scroll = el("div", "corp-floor");
    var row = el("div", "corp-belt-row");
    (f.stations || []).forEach(function (s, index) {
      if (index) row.appendChild(el("div", "corp-arrow", "➜"));
      row.appendChild(station(s, f.belt));
    });
    scroll.appendChild(row);
    scroll.appendChild(el("div", "corp-belt" + (f.whistle ? " corp-belt-on" : "")));
    wrap.appendChild(scroll);

    var yard = el("div", "corp-yard");
    var warehouse = el("div", "corp-bay");
    warehouse.appendChild(el("div", "corp-bay-title", "🏬 Склад: " + (f.warehouse || []).length));
    (f.warehouse || []).slice(0, 5).forEach(function (p) { line(warehouse, "📦 " + p.id + " " + p.title, "corp-muted"); });
    yard.appendChild(warehouse);
    var shelf = el("div", "corp-bay");
    shelf.appendChild(el("div", "corp-bay-title", "🛒 Витрина: " + (f.shelf || []).length + " · ждут ответа: " + (f.clients_waiting || 0)));
    (f.shelf || []).slice(0, 5).forEach(function (p) { line(shelf, "📦 " + p.id + " — " + p.note, "corp-muted"); });
    yard.appendChild(shelf);
    var dock = el("div", "corp-bay");
    dock.appendChild(el("div", "corp-bay-title", "🚚 Отгружено: " + (f.shipped || 0)));
    line(dock, "записей в журнале: " + (f.records || 0), "corp-muted");
    yard.appendChild(dock);
    var chute = el("div", "corp-bay corp-chute");
    chute.appendChild(el("div", "corp-bay-title", "🗑️ Жёлоб брака: " + (f.chute || []).length + " · дел Суда: " + (f.court || []).length));
    (f.chute || []).slice(-3).reverse().forEach(function (x) { line(chute, "✘ " + x.id + " [" + x.role + "] " + (x.reason || ""), "corp-muted"); });
    (f.court || []).slice(-3).reverse().forEach(function (c) { line(chute, "⚖️ " + (c.task || c.channel || "") + ": " + (c.reason || c.action || ""), "corp-muted"); });
    yard.appendChild(chute);
    wrap.appendChild(yard);
    return wrap;
  }

  // ----- the books -----------------------------------------------------------------------------

  function books(data) {
    var node = el("div");
    var directives = section("Директивы");
    (data.directives || []).slice(-8).reverse().forEach(function (d) {
      line(directives, "[" + d.seq + "] " + when(d.at) + " (" + (d.by || "owner") + ") " + d.text + (d.project ? " → " + d.project : " · ждёт конторы"));
    });
    if (!(data.directives || []).length) line(directives, "Ещё ни одной: основательница напишет первую, когда смена пойдёт; или «корпорация: директива …».", "corp-muted");
    node.appendChild(directives);

    var projects = section("Проекты");
    (data.projects || []).slice(-6).reverse().forEach(function (p) {
      var card = el("div", "corp-card");
      card.appendChild(el("div", "corp-card-title", p.id + " · " + p.title + " · " + word(p.status) + (p.line ? " · линия " + p.line : "")));
      if (p.goal) line(card, p.goal, "corp-muted");
      (p.tasks || []).slice(-10).forEach(function (t) {
        line(card, t.id + " [" + t.role + "] " + word(t.status) + (t.turns ? ", ходов " + t.turns : "") + ": " + String(t.text || "").slice(0, 160));
      });
      (p.offers || []).forEach(function (o) { line(card, "Оффер " + o.id + " — " + money(o.price) + ", " + o.channel + ", " + word(o.status)); });
      projects.appendChild(card);
    });
    if (!(data.projects || []).length) line(projects, "Проектов пока нет.", "corp-muted");
    node.appendChild(projects);

    var clients = section("Покупатели");
    (data.clients || []).slice(-8).forEach(function (c) {
      var last = (c.messages || [])[c.messages.length - 1];
      line(clients, c.id + (c.name ? " " + c.name : "") + " (" + (c.channel || "") + "): " + (last ? last.from + ": " + String(last.text).slice(0, 120) : "") + (c.paid && c.paid.length ? " · оплатил " + c.paid.join(", ") : ""));
    });
    if (!(data.clients || []).length) line(clients, "Никто ещё не писал.", "corp-muted");
    node.appendChild(clients);

    var ledger = section("Журнал");
    (data.ledger || []).slice().reverse().forEach(function (e) {
      var d = e.data || {};
      var what = d.text || d.reason || d.result || d.reply || d.title || d.error || "";
      line(ledger, e.seq + " " + when(e.at) + " " + e.kind + " [" + (e.actor || "") + "] " + String(what).slice(0, 140), "corp-ledger-line");
    });
    var verify = data.verify || {};
    line(ledger, "Записей " + (verify.records || 0) + "; цепочка " + (verify.ok ? "цела" : "ПОВРЕЖДЕНА на " + verify.broken_at) + ".", "corp-muted");
    node.appendChild(ledger);
    return node;
  }

  function render(content, f, data) {
    content.textContent = "";
    content.appendChild(header(f));
    var day = el("div", "corp-day");
    day.appendChild(timeline(f));
    day.appendChild(departmentRows(f));
    day.appendChild(sidebar(f));
    content.appendChild(day);
    content.appendChild(results(f));
    content.appendChild(floor(f));
    content.appendChild(books(data));
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
    var modal = UI.modal.create({ id: MODAL_ID, title: "Корпорация", body: body, className: "corp-modal" });
    UI.modal.open(modal);
    var timer = null;

    function load() {
      if (!document.getElementById(MODAL_ID)) { if (timer) clearInterval(timer); return; }
      Promise.all([post("conveyor"), post("projects"), post("ledger", { count: 30 })]).then(function (parts) {
        status.textContent = "Обновлено " + new Date().toLocaleTimeString("ru-RU") + ". Руль — в чате: «корпорация: старт», «корпорация: цель …», «корпорация: статус».";
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
