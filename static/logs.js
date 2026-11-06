(function () {
  "use strict";

  // The logs window: the server's system log (GET /api/logs, observability/system_log.py), newest first. New records
  // arrive by themselves while the window is open; older ones come a page at a time. Dozzle, which this icon opened
  // before, does not exist on the phone (owner, 06.11.2026: "there is not more logs").

  var MODAL_ID = "logs-modal";
  var REFRESH_MS = 3000;
  var SEARCH_DELAY_MS = 300;
  var FILTERS = [
    ["", "Все"],
    ["WARNING", "Предупреждения"],
    ["ERROR", "Ошибки"],
  ];

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function when(timestamp) {
    var date = new Date(timestamp);
    if (isNaN(date.getTime())) return "";
    return date.toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function row(entry) {
    var node = el("div", "logs-row");
    node.dataset.level = entry.level || "";
    var head = el("div", "logs-row-head");
    head.appendChild(el("span", "", when(entry.timestamp)));
    head.appendChild(el("span", "logs-level", entry.level || ""));
    head.appendChild(el("span", "", String(entry.logger || "").replace(/^logos\./, "")));
    node.appendChild(head);
    node.appendChild(el("div", "logs-text", entry.message || ""));
    if (entry.exception) node.appendChild(el("div", "logs-text", entry.exception));
    return node;
  }

  function open() {
    var existing = document.getElementById(MODAL_ID);
    if (existing) existing.remove();

    var UI = window.LogosCoreAPI.ui;
    var body = el("div", "logs-body");
    var tools = el("div", "logs-tools");
    var search = el("input", "logs-search");
    search.type = "search";
    search.placeholder = "Поиск в логах";
    search.setAttribute("aria-label", "Поиск в логах");
    search.autocomplete = "off";
    search.spellcheck = false;
    tools.appendChild(search);
    var filters = FILTERS.map(function (filter) {
      var button = el("button", "logos-btn logs-filter", filter[1]);
      button.type = "button";
      button.setAttribute("aria-pressed", filter[0] ? "false" : "true");
      button.addEventListener("click", function () {
        level = filter[0];
        filters.forEach(function (other) {
          other.setAttribute("aria-pressed", other === button ? "true" : "false");
        });
        reload();
      });
      tools.appendChild(button);
      return button;
    });
    var status = el("div", "logs-status", "Загрузка…");
    status.setAttribute("role", "status");
    var list = el("div", "logs-list");
    var more = el("button", "logos-btn logs-more", "Показать ещё");
    more.type = "button";
    more.hidden = true;
    body.appendChild(tools);
    body.appendChild(status);
    body.appendChild(list);
    body.appendChild(more);

    var modal = UI.modal.create({ id: MODAL_ID, title: "Логи", body: body });
    UI.modal.open(modal);

    var level = "";
    var text = "";
    // Which log the two positions below belong to; a rotation can renumber them (read_entries).
    var history = null;
    var older = null;
    var newer = null;
    // A filter changed or the window closed: answers to the earlier requests are dropped.
    var generation = 0;
    var loading = false;
    var refreshing = false;
    var searchTimer = null;

    function query(position) {
      var params = new URLSearchParams(position);
      if (level) params.set("level", level);
      if (text) params.set("q", text);
      return window.LogosDispatcher.request("/api/logs?" + params.toString(), {
        headers: { Accept: "application/json" },
      }).then(function (response) {
        if (!response.ok) return window.LogosHttpError(response).then(function (e) { throw e; });
        return response.json();
      });
    }

    function showCount() {
      var count = list.childElementCount;
      status.classList.remove("logos-error-note");
      status.textContent = count ? "Записей показано: " + count : "Записей нет";
      more.hidden = older === null;
    }

    function showFailure(error) {
      status.classList.add("logos-error-note");
      status.textContent = "Не удалось загрузить логи: " + ((error && error.message) || error);
    }

    function load() {
      var mine = generation;
      loading = true;
      query({})
        .then(function (data) {
          if (mine !== generation) return;
          history = data.log;
          older = data.before;
          newer = data.after;
          list.append.apply(list, data.entries.map(row));
          showCount();
        })
        .catch(function (error) {
          if (mine === generation) showFailure(error);
        })
        .then(function () {
          if (mine === generation) loading = false;
        });
    }

    function reload() {
      generation += 1;
      older = null;
      newer = null;
      list.replaceChildren();
      more.hidden = true;
      status.classList.remove("logos-error-note");
      status.textContent = "Загрузка…";
      load();
    }

    function refresh() {
      if (refreshing || loading || document.hidden) return;
      // The first read failed, say during a restart: it is tried again, and its failure stays up until it works.
      if (newer === null) {
        load();
        return;
      }
      var mine = generation;
      refreshing = true;
      query({ after: newer, log: history })
        .then(function (data) {
          if (mine !== generation) return;
          if (data.reset) {
            reload();
            return;
          }
          newer = data.after;
          list.prepend.apply(list, data.entries.map(row));
          showCount();
        })
        .catch(function (error) {
          if (mine === generation) showFailure(error);
        })
        .then(function () {
          refreshing = false;
        });
    }

    more.addEventListener("click", function () {
      var mine = generation;
      more.disabled = true;
      query({ before: older, log: history })
        .then(function (data) {
          if (mine !== generation) return;
          if (data.reset) {
            reload();
            return;
          }
          older = data.before;
          list.append.apply(list, data.entries.map(row));
          showCount();
        })
        .catch(function (error) {
          if (mine === generation) showFailure(error);
        })
        .then(function () {
          more.disabled = false;
        });
    });

    search.addEventListener("input", function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function () {
        text = search.value.trim();
        reload();
      }, SEARCH_DELAY_MS);
    });

    var timer = setInterval(refresh, REFRESH_MS);
    modal.addEventListener("close", function () {
      clearInterval(timer);
      clearTimeout(searchTimer);
      generation += 1;
    });

    reload();
  }

  window.openLogsModal = open;
})();
