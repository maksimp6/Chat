(function () {
  "use strict";

  // The vector database window: the collections of vector_db with their documents, chunks and the chunks the
  // current document model embedded (only those are found by search), and a search by meaning over every
  // collection (POST /api/vectors/<action>, vector_db/routes.py). The window only reads: search stores nothing.
  // The server returns every match ranked; the window shows them a page at a time.

  var MODAL_ID = "vectors-modal";
  var SEARCH_DELAY_MS = 300;
  // Result rows added per page of the list already received; every result stays reachable.
  var PAGE = 50;

  var el = window.LogosCoreAPI.ui.element;

  function post(action, body) {
    return window.LogosDispatcher.request("/api/vectors/" + action, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body || {}),
    }).then(function (response) {
      if (!response.ok) return window.LogosHttpError(response);
      return response.json();
    });
  }

  function collectionRow(c) {
    var node = el("div", "logs-row");
    var head = el("div", "logs-row-head");
    head.appendChild(el("span", "", String(c.collection)));
    head.appendChild(el("span", "", "документов: " + c.documents));
    head.appendChild(el("span", "", "частей: " + c.chunks));
    head.appendChild(el("span", "", "найдёт поиск: " + c.searchable));
    node.appendChild(head);
    if (c.searchable < c.chunks) {
      node.appendChild(el("div", "logs-text", "часть эмбеддингов сделана прежней моделью и поиском не находится"));
    }
    return node;
  }

  function hitRow(hit) {
    var node = el("div", "logs-row");
    var head = el("div", "logs-row-head");
    head.appendChild(el("span", "", String(hit.collection)));
    head.appendChild(el("span", "", String(hit.id)));
    head.appendChild(el("span", "", "часть " + hit.chunk));
    head.appendChild(el("span", "", "сходство " + Number(hit.score).toFixed(3)));
    node.appendChild(head);
    node.appendChild(el("div", "logs-text", hit.text || ""));
    return node;
  }

  function reason(error) {
    return (error && error.message) || String(error);
  }

  function open() {
    var existing = document.getElementById(MODAL_ID);
    if (existing) existing.remove();

    var UI = window.LogosCoreAPI.ui;
    var body = el("div");
    var status = el("div", "logs-status", "Загрузка…");
    status.setAttribute("role", "status");
    var collections = el("div");
    var tools = el("div", "logs-tools");
    var search = el("input", "logs-search");
    search.type = "search";
    search.placeholder = "Поиск по смыслу";
    search.setAttribute("aria-label", "Поиск по смыслу");
    search.autocomplete = "off";
    search.spellcheck = false;
    tools.appendChild(search);
    var results = el("div");
    var more = el("button", "logos-btn logs-more", "Показать ещё");
    more.type = "button";
    more.hidden = true;
    body.appendChild(status);
    body.appendChild(collections);
    body.appendChild(tools);
    body.appendChild(results);
    body.appendChild(more);

    var modal = UI.modal.create({ id: MODAL_ID, title: "Векторная база", body: body });
    UI.modal.open(modal);

    // A new search or the window closed: answers to the earlier requests are dropped.
    var generation = 0;
    var searchTimer = null;
    // The results of the latest search and how many of them are shown.
    var hits = [];
    var shown = 0;
    // What the status says about the collections, shown again when the search field is emptied.
    var listed = { text: "Загрузка…", failed: false };

    function showStatus(text, failed) {
      status.classList.toggle("logos-error-note", failed);
      status.textContent = text;
    }

    function showPage() {
      var page = hits.slice(shown, shown + PAGE);
      results.append.apply(results, page.map(hitRow));
      shown += page.length;
      more.hidden = shown >= hits.length;
    }

    function clearResults() {
      hits = [];
      shown = 0;
      results.replaceChildren();
      more.hidden = true;
    }

    // The search field holds text: the status belongs to the search, not to the collections.
    var searching = false;

    function showListed() {
      if (!searching) showStatus(listed.text, listed.failed);
    }

    post("collections")
      .then(function (data) {
        var list = (data && data.collections) || [];
        listed = { text: list.length ? "" : "Коллекций нет", failed: false };
        collections.append.apply(collections, list.map(collectionRow));
        showListed();
      })
      .catch(function (error) {
        listed = { text: "Не удалось загрузить векторную базу: " + reason(error), failed: true };
        showListed();
      });

    function find(text) {
      generation += 1;
      var mine = generation;
      clearResults();
      searching = Boolean(text);
      if (!searching) {
        showListed();
        return;
      }
      showStatus("Поиск…", false);
      post("search", { query: text })
        .then(function (data) {
          if (mine !== generation) return;
          hits = (data && data.results) || [];
          showStatus("Найдено: " + hits.length + " из " + ((data && data.searched) || 0) + " частей", false);
          showPage();
        })
        .catch(function (error) {
          if (mine !== generation) return;
          var message = reason(error);
          var embedding = /embed|501/i.test(message);
          showStatus(
            "Поиск не выполнен: " + message + (embedding ? " — сервер эмбеддингов на ПК не ответил." : ""),
            true,
          );
        });
    }

    more.addEventListener("click", showPage);

    search.addEventListener("input", function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function () {
        find(search.value.trim());
      }, SEARCH_DELAY_MS);
    });

    modal.addEventListener("close", function () {
      clearTimeout(searchTimer);
      generation += 1;
    });
  }

  window.openVectorsModal = open;
})();
