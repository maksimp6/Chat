(function () {
  "use strict";

  // Settings are read-only: the windows show them, nothing is saved (the server has no save route).
  var READ_ONLY_NOTE =
    "Настройки общие для всех диалогов и только для чтения: всё стоит на максимуме, изменить их здесь нельзя.";

  // Every tool has its icon in static/icons/tools/<name>.svg (scripts/make_tool_icons.py).
  var TOOL_ICON_BASE = (function () {
    var src = document.currentScript && document.currentScript.src;
    return src ? new URL("../icons/tools/", src).href : "/static/icons/tools/";
  })();

  // The icon is drawn as a mask in the text colour, so it reads in both colour schemes.
  function toolChip(name) {
    var url = "url('" + TOOL_ICON_BASE + encodeURIComponent(name) + ".svg') center / contain no-repeat";
    return (
      '<span class="tool-chip">' +
      '<span class="tool-chip-icon" aria-hidden="true" style="--tool-icon:' + url + ';"></span>' +
      window.SettingsUI.escapeHtml(name) +
      "</span>"
    );
  }

  function disableControls(root) {
    root.querySelectorAll("input, select, textarea").forEach(function (control) {
      control.disabled = true;
    });
  }

  // === 1. Модальное окно локальных инструментов (Tools), только для чтения ===
  window.openToolsModal = function () {
    var UI = window.SettingsUI;
    var CoreUI = window.LogosCoreAPI.ui;
    var Storage = window.SettingsStorage;
    var settings = Storage.load();
    var unknownAtOpen = Storage.isUnknown();
    var cfg = settings.tools_config || {};
    var ws = cfg.web_search || {};
    var fs = cfg.file_search || {};
    var ci = cfg.code_interpreter || {};
    var dl = cfg.defer_loading || {};
    var eager = Array.isArray(dl.eager_categories) ? dl.eager_categories.join(", ") : dl.eager_categories || "";

    var existing = document.getElementById("tools-modal-custom");
    if (existing) existing.remove();

    var md = document.createElement("div");

    md.innerHTML = [
      '<div class="settings-lead">Локальные и встроенные инструменты. Настройки только для чтения.</div>',
      '<h3 class="settings-subtitle">Локальные инструменты</h3>',
      '<div id="tools-category-list" class="tool-category-list">Загрузка инструментов...</div>',
      '<div class="settings-defer-note"><strong>⏳ Отложенная загрузка</strong> — всегда включена: модель видит инструмент только после поиска инструментов провайдера. Отключить нельзя.</div>',
      '<div class="settings-field">' +
        UI.lbl("Категории без отложенной загрузки (всегда загружены)") +
        UI.inp("tools-defer-eager", "text", eager, {"placeholder": "rootagent, filesystem"}) +
        "</div>",
      '<hr class="settings-rule settings-rule-wide">',
      '<h3 class="settings-subtitle">Встроенные инструменты Yandex AI Studio</h3>',
      UI.chk(
        "tools-ws-en",
        // An absent setting means on, as the server applies it.
        ws.enabled !== false,
        "<strong>🌐 Поиск в интернете</strong>",
      ),
      '<div class="settings-field">' +
        UI.lbl("Размер контекста поиска") +
        UI.sel(
          "tools-ws-context",
          '<option value="low"' +
            (ws.context_size === "low" ? " selected" : "") +
            ">Низкий</option>" +
            '<option value="medium"' +
            (ws.context_size === "medium" ? " selected" : "") +
            ">Средний</option>" +
            '<option value="high"' +
            (!ws.context_size || ws.context_size === "high" ? " selected" : "") +
            ">Высокий</option>",
        ) +
        "</div>",
      '<div class="settings-field">' +
        UI.lbl("Разрешённые домены") +
        UI.inp(
          "tools-ws-allow",
          "text",
          ws.allowed_domains || "",
          {"placeholder": "example.com, wikipedia.org"},
        ) +
        "</div>",
      '<div class="settings-field">' +
        UI.lbl("Заблокированные домены") +
        UI.inp("tools-ws-block", "text", ws.blocked_domains || "", {"placeholder": "example.org"}) +
        "</div>",
      '<hr class="settings-rule">',
      UI.chk("tools-ci-en", ci.enabled !== false, "<strong>🧮 Выполнение кода</strong> (Python)"),
      '<hr class="settings-rule">',
      UI.chk(
        "tools-fs-en",
        fs.enabled !== false,
        "<strong>📚 Поиск по файлам</strong> (векторные хранилища)",
      ),
      '<div class="settings-field">' +
        UI.lbl("ID векторных хранилищ (основное хранилище знаний подключено всегда)") +
        UI.inp(
          "tools-fs-vids",
          "text",
          fs.vector_store_ids || "",
          {"placeholder": "vs_xxx, vs_yyy"},
        ) +
        "</div>",
      '<div class="settings-field">' +
        UI.lbl("Максимум результатов") +
        UI.inp("tools-fs-max", "number", fs.max_results || 50, {"min": "1", "max": "50"}) +
        "</div>",
      '<div class="settings-hint">Эти инструменты выполняются на стороне Yandex AI Studio. Они независимы от MCP и локального реестра инструментов.</div>',
      '<div id="tools-status" class="settings-status" role="status"></div>',
    ].join("");
    disableControls(md);
    var ov = CoreUI.modal.create({
      id: "tools-modal-custom",
      title: "Инструменты",
      body: md,
    });
    // Shown at once with its loading line; the categories fill it when they arrive. A click on the backdrop and Escape
    // close it through LogosCoreAPI.ui.modal.
    CoreUI.modal.open(ov);

    var categoryLabels = {
      termux: "📱 Устройство Termux (батарея, буфер, сенсоры, TTS, тосты)",
      git: "🌿 Локальный Git (статус, ветки, diff, коммиты)",
      filesystem: "⚙️ Файловая система (чтение, запись, apply_patch)",
      system: "🔧 Системные драйверы Termux",
      wikipedia: "📚 Википедия (поиск и сводка статей)",
      profiler: "⏱️ Профайлер и инспекция состояния",
      court: "⚖️ Суд Логоса (суды над диалогом и зал заседаний)",
    };

    // fetch resolves for HTTP errors too, so a non-OK status must be turned into a failure here.
    // The failure keeps the HTTP status and the server's error code, so the message names the real cause.
    function readOk(r) {
      if (!r.ok) {
        return r
          .json()
          .catch(function () {
            return {};
          })
          .then(function (body) {
            var error = new Error("HTTP " + r.status);
            error.status = r.status;
            error.code = body && typeof body.error === "string" ? body.error : "";
            throw error;
          });
      }
      return r.json();
    }
    // Why the list did not load: the server's answer (with its HTTP status) or no answer at all.
    function categoriesErrorText(error) {
      var status = error && error.status;
      if (status >= 500) {
        return "Ошибка на сервере (код " + status + "): список инструментов не получен. Нажмите «Повторить» позже.";
      }
      if (status) {
        return "Сервер отклонил запрос (код " + status + "): список инструментов не получен. Нажмите «Повторить».";
      }
      if (error && error.name === "TimeoutError") {
        return "Сервер не ответил вовремя: список инструментов не получен. Нажмите «Повторить».";
      }
      if (error && error.name === "SyntaxError") {
        return "Ответ сервера не удалось прочитать: список инструментов не получен. Нажмите «Повторить».";
      }
      return "Нет связи с сервером: список инструментов не получен. Проверьте соединение и нажмите «Повторить».";
    }
    function loadCategories() {
    var loadingEl = document.getElementById("tools-category-list");
    // «Повторить» replaces the error with the loading line, so a repeated tap cannot start a second load.
    if (loadingEl) loadingEl.textContent = "Загрузка инструментов...";
    window.LogosDispatcher.request("/api/tools/categories")
      .then(readOk)
      .then(function (catData) {
        var listEl = document.getElementById("tools-category-list");
        var categories = catData.categories || {};
        // One tool, one function: the function picks the action; these are its actions.
        var actionsOf = catData.actions || {};
        function countOf(n, one, few, many) {
          var mod10 = n % 10;
          var mod100 = n % 100;
          var word = mod10 === 1 && mod100 !== 11 ? one : mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20) ? few : many;
          return n + " " + word;
        }
        listEl.innerHTML = Object.keys(categories)
          .map(function (cat) {
            var label = categoryLabels[cat] || "Модуль: " + cat;
            var functions = categories[cat] || [];
            var actions = actionsOf[cat] || [];
            var summary = countOf(functions.length, "функция", "функции", "функций");
            if (actions.length) summary += " · " + countOf(actions.length, "действие", "действия", "действий");
            return (
              '<label class="tool-category">' +
              '<input type="checkbox" class="tool-cat-chk" disabled checked value="' +
              UI.escapeHtml(cat) +
              '">' +
              '<div class="tool-category-main"><div class="tool-category-name">' +
              UI.escapeHtml(label) +
              "</div>" +
              '<div class="tool-category-count">' +
              UI.escapeHtml(summary) +
              "</div>" +
              '<div class="tool-chips">' +
              functions.map(toolChip).join("") +
              "</div>" +
              (actions.length
                ? '<details class="tool-category-actions"><summary>Действия функции</summary>' +
                  UI.escapeHtml(actions.join(", ")) +
                  "</details>"
                : "") +
              "</div></label>"
            );
          })
          .join("");
      })
      .catch(function (error) {
        console.error("[SETTINGS] Tool categories load failed:", error);
        var listEl = document.getElementById("tools-category-list");
        if (!listEl) return;
        listEl.replaceChildren();
        var message = document.createElement("p");
        // role="alert" makes TalkBack read out why the list did not load.
        message.setAttribute("role", "alert");
        message.textContent = categoriesErrorText(error);
        var retry = document.createElement("button");
        retry.type = "button";
        retry.className = "logos-btn";
        retry.textContent = "Повторить";
        retry.addEventListener("click", function () {
          // The tapped button is removed by the reload, so focus stays inside the window on the list.
          listEl.setAttribute("tabindex", "-1");
          listEl.focus();
          loadCategories();
        });
        listEl.append(message, retry);
      });
    }
    document.getElementById("tools-status").textContent = unknownAtOpen
      ? "Общие настройки не загружены с сервера: показаны значения, встроенные в интерфейс. " + READ_ONLY_NOTE
      : READ_ONLY_NOTE;
    loadCategories();
  };

  // === 2. Полное окно конфигурации параметров LLM ===
  function ultraProfileFor(modelKey) {
    var profile = window.SettingsStorage.ultraProfileFor(modelKey);
    return profile && profile.default_profile === "ultra" && profile.settings_defaults
      ? profile : null;
  }

  function ultraProfileNote(profile) {
    if (!profile) return "";
    var defaults = profile.settings_defaults;
    var depth = defaults.reasoning_effort === "disabled"
      ? "параметр не передаётся для этой модели"
      : defaults.reasoning_effort;
    return (
      '<div class="settings-ultra-note">' +
      "<strong>Профиль: Ultra</strong><br>" +
      "Ответ — максимум провайдера, если поле токенов пустое или 0; контекст не обрезается; " +
      "глубина размышлений — " + window.SettingsUI.escapeHtml(depth) + "." +
      "</div>"
    );
  }

  // IDs and display values live together so a field has one binding.
  var SETTINGS_FIELDS = {
    temperature: {
      id: "set-temp", label: "Температура", type: "number",
      extra: {"step": "0.1", "min": "0", "max": "2"},
    },
    top_p: {
      id: "set-topp", label: "Top P", type: "number",
      extra: {"step": "0.05", "min": "0", "max": "1"},
    },
    max_output_tokens: {
      id: "set-tokens", label: "Токены ответа (0 — максимум провайдера)",
      type: "number", extra: {"step": "100", "min": "0"},
    },
    truncation: {
      id: "set-trunc", label: "Обрезка контекста", type: "select",
      choices: [["disabled", "Выключена"], ["auto", "Автоматически"]],
    },
    reasoning_effort: {
      id: "set-reasoning", label: "Глубина размышлений", type: "select",
      displayFallback: "high",
      choices: [["disabled", "Выключена"], ["low", "Низкий"], ["medium", "Средний"], ["high", "Высокий"]],
    },
    store: { id: "set-store", label: "Сохранять ответы", type: "checkbox", enabledByDefault: true },
    background: { id: "set-bg", label: "Фоновый опрос", type: "checkbox", enabledByDefault: false },
    stream: { id: "set-stream", label: "Потоковый режим", type: "checkbox", enabledByDefault: false },
    instructions: {
      id: "set-instructions", label: "Системный промпт (Instructions)",
      type: "textarea", extra: "height:70px;",
    },
    text_format: {
      id: "set-text-fmt", label: "Формат", type: "select",
      choices: [["text", "Обычный текст (Text)"], ["json", "Строгий JSON (json_schema)"]],
    },
    text_verbosity: {
      id: "set-verbosity", label: "Детализация (Verbosity)", type: "select", displayFallback: "high",
      // The provider accepts low, medium and high (request_builder validation).
      choices: [["low", "Низкий"], ["medium", "Средний"], ["high", "Высокий"]],
    },
    json_schema_name: {
      id: "set-js-name", label: "Название схемы", type: "text",
      displayFallback: "response",
    },
    json_schema: {
      id: "set-js-schema", label: "Определение JSON Schema", type: "textarea",
      displayFallback: "", extra: "height:100px;font-family:monospace;font-size:11px;",
    },
    tool_choice: {
      id: "set-tool-choice", label: "Выбор инструмента", type: "select",
      choices: [["auto", "Автоматически"], ["required", "Обязательно"], ["none", "Нет"]],
    },
    max_tool_calls: {
      id: "set-max-tools", label: "Макс. вызовов инструментов", type: "number",
      displayFallback: "", extra: {"min": "1"},
    },
    parallel_tool_calls: {
      id: "set-parallel-tools", label: "Параллельные вызовы",
      type: "checkbox", enabledByDefault: true,
    },
    top_logprobs: {
      id: "set-logprobs", label: "Вероятности токенов", type: "number",
      displayFallback: 0, extra: {"min": "0", "max": "5"},
    },
    service_tier: {
      id: "set-tier", label: "Уровень обслуживания", type: "select",
      choices: [["auto", "Автоматически"], ["priority", "Приоритетный"]],
    },
    previous_response_id: {
      id: "set-prev-id", label: "ID предыдущего ответа", type: "text",
      displayFallback: "", extra: {"placeholder": "UUID"},
    },
    prompt_cache_key: {
      id: "set-cache-key", label: "Ключ кэша промпта", type: "text",
      displayFallback: "", extra: {"placeholder": "cache-id"},
    },
    prompt_id: {
      id: "set-prompt-id", label: "ID шаблона промпта", type: "text",
      displayFallback: "", extra: {"placeholder": "Шаблон Yandex Studio"},
    },
    prompt_version: {
      id: "set-prompt-ver", label: "Версия промпта", type: "text",
      displayFallback: "", extra: {"placeholder": "latest"},
    },
    prompt_variables: {
      id: "set-prompt-vars", label: "Переменные промпта (JSON)", type: "text",
      displayFallback: "", extra: {"placeholder": "{\"key\":\"val\"}"},
    },
    conv_metadata: {
      id: "set-conv-meta", label: "Метаданные диалога (JSON)", type: "textarea",
      displayFallback: "", extra: "height:60px;font-family:monospace;font-size:11px;",
    },
  };

  function gib(bytes) {
    return typeof bytes === "number" ? (bytes / 1073741824).toFixed(1) + " ГиБ" : "—";
  }

  function ollamaRows(rows, UI) {
    return (
      '<table class="ollama-table">' +
      rows
        .map(function (row) {
          return (
            "<tr><td>" +
            UI.escapeHtml(row[0]) +
            "</td><td>" +
            UI.escapeHtml(row[1] === undefined || row[1] === null ? "—" : String(row[1])) +
            "</td></tr>"
          );
        })
        .join("") +
      "</table>"
    );
  }

  // GET /api/ollama/settings: the settings and the server's live state, all read-only.
  function loadOllamaSettings(box, UI) {
    if (!box) return;
    window.LogosDispatcher.request("/api/ollama/settings", null, { timeoutMs: 30000 })
      .then(function (r) {
        if (!r.ok) return window.LogosHttpError(r).then(function (e) { throw e; });
        return r.json();
      })
      .then(function (data) {
        var s = data.settings || {};
        var options = s.options || {};
        var models = s.models || {};
        var html = ollamaRows(
          [
            ["Сервер", s.url],
            ["Версия Ollama", data.version],
            [
              "Модели",
              Object.keys(models)
                .map(function (key) { return key + " → " + models[key]; })
                .join(", "),
            ],
            ["Контекст (num_ctx)", options.num_ctx],
            ["Длина ответа (num_predict)", options.num_predict === -1 ? "без ограничения (-1)" : options.num_predict],
            ["Промпт за шаг (num_batch)", options.num_batch],
            [
              "Мышление (think)",
              Object.keys(s.think || {})
                .map(function (key) {
                  return key + ": " + (s.think[key] ? "включено" : "выключено");
                })
                .join(", "),
            ],
            ["Потоковый вывод", s.stream ? "включён" : "выключен"],
            ["Инструменты", (s.tools || []).join(", ") || "нет"],
          ],
          UI,
        );
        var running = data.running || [];
        html += UI.section("Загружено в память сейчас");
        html += running.length
          ? running
              .map(function (m) {
                return ollamaRows(
                  [
                    ["Модель", m.name],
                    ["Память", gib(m.size) + " (из них в видеопамяти " + gib(m.size_vram) + ")"],
                    ["Контекст", m.context_length],
                    ["Выгрузится", m.expires_at ? new Date(m.expires_at).toLocaleString() : "—"],
                  ],
                  UI,
                );
              })
              .join("")
          : '<div class="ollama-empty">Ни одна модель не загружена.</div>';
        Object.keys(data.models || {}).forEach(function (key) {
          var model = data.models[key];
          var details = model.details || {};
          html += UI.section("Модель " + model.upstream);
          html += ollamaRows(
            [
              ["Размер", details.parameter_size],
              ["Квантование", details.quantization_level],
              ["Семейство", details.family],
            ],
            UI,
          );
          if (model.parameters) {
            html +=
              '<pre class="ollama-pre">' +
              UI.escapeHtml(model.parameters) +
              "</pre>";
          }
        });
        html += UI.section("Инструкции модели");
        html +=
          '<pre class="ollama-pre">' +
          UI.escapeHtml(s.instructions || "") +
          "</pre>";
        var errors = data.errors || {};
        var errorKeys = Object.keys(errors);
        if (errorKeys.length) {
          html +=
            '<div class="ollama-error">Ollama не ответила: ' +
            UI.escapeHtml(
              errorKeys.map(function (k) { return k + ": " + errors[k]; }).join("; "),
            ) +
            "</div>";
        }
        box.innerHTML = html;
      })
      .catch(function (error) {
        box.textContent =
          "Настройки Ollama не загружены: " + (error && error.message ? error.message : error);
      });
  }

  window.openSettingsModal = function () {
    var UI = window.SettingsUI;
    var CoreUI = window.LogosCoreAPI.ui;
    var Storage = window.SettingsStorage;
    var settings = Storage.load();
    var unknownAtOpen = Storage.isUnknown();
    var ultra = ultraProfileFor(typeof currentModel !== "undefined" ? currentModel : "");

    var existing = document.getElementById("settings-modal-custom");
    if (existing) {
      CoreUI.modal.close(existing);
      existing.remove();
    }

    var ov = CoreUI.modal.create({
      id: "settings-modal-custom",
      title: "Настройки",
      contentClassName: "settings-modal-panel",
    });
    var md = ov.querySelector(".modal-content");
    var title = md.querySelector(".modal-title");
    var closeButton = md.querySelector(".modal-close");
    closeButton.id = "set-close-btn";
    closeButton.classList.add("settings-contract-btn");

    function fieldHtml(key) {
      var field = SETTINGS_FIELDS[key];
      var value = settings[key];
      if (!value && Object.prototype.hasOwnProperty.call(field, "displayFallback")) {
        value = field.displayFallback;
      }
      if (field.type === "checkbox") {
        var checked = field.enabledByDefault ? value !== false : value === true;
        return UI.chk(field.id, checked, field.label);
      }
      var control;
      if (field.type === "select") {
        var options = field.choices.map(function (choice) {
          return '<option value="' + UI.escapeHtml(choice[0]) + '"' +
            (value === choice[0] ? " selected" : "") + ">" +
            UI.escapeHtml(choice[1]) + "</option>";
        }).join("");
        control = UI.sel(field.id, options);
      } else if (field.type === "textarea") {
        control = UI.ta(field.id, value, field.extra);
      } else {
        control = UI.inp(field.id, field.type, value, field.extra);
      }
      return UI.lbl(field.label) + control;
    }

    function row(keys) {
      var cells = keys.map(function (key) {
        return "<div>" + (key ? fieldHtml(key) : "") + "</div>";
      });
      return keys.length === 3
        ? UI.gap3(cells[0], cells[1], cells[2])
        : UI.gap2(cells[0], cells[1]);
    }

    var tabsHeader = [
      '<div class="settings-modal-top">',
      '<div class="settings-modal-heading"></div>',
      '<div class="llm-tabs">',
      '    <button class="logos-btn llm-tab-btn active settings-contract-btn" data-tab="tab-ollama">Ollama</button>',
      '    <button class="logos-btn llm-tab-btn settings-contract-btn" data-tab="tab-gen">Сэмплинг</button>',
      '    <button class="logos-btn llm-tab-btn settings-contract-btn" data-tab="tab-output">Формат вывода</button>',
      '    <button class="logos-btn llm-tab-btn settings-contract-btn" data-tab="tab-routing">Маршрутизация вызовов</button>',
      '    <button class="logos-btn llm-tab-btn settings-contract-btn" data-tab="tab-adv">Промпты и кэш</button>',
      '    <button class="logos-btn llm-tab-btn settings-contract-btn" data-tab="tab-theme">Оформление</button>',
      "</div>",
      "</div>",
    ].join("");

    // Ollama's own settings (ollama_client.settings) and what Ollama reports now: the
    // chat model runs there, and nothing of the Yandex tabs below reaches it.
    var tabOllama = [
      '<div id="tab-ollama" class="llm-tab-content">',
      UI.section("Ollama на ПК владельца"),
      '<div id="ollama-settings-body">Загружаю…</div>',
      "</div>",
    ].join("");

    var tabGen = [
      '<div id="tab-gen" class="llm-tab-content" hidden>',
      UI.section("Базовый сэмплинг"),
      ultraProfileNote(ultra),
      row(["temperature", "top_p"]),
      row(["max_output_tokens", "truncation"]),
      row(["reasoning_effort", null]),
      row(["store", "background", "stream"]),
      '<div class="settings-field-wide">' + fieldHtml("instructions") + "</div>",
      "</div>",
    ].join("");

    var tabOutput = [
      '<div id="tab-output" class="llm-tab-content" hidden>',
      UI.section("Структура ответа (текст или JSON Schema)"),
      row(["text_format", "text_verbosity"]),
      '<div id="json-schema-wrap" class="settings-field-wide"' +
        (settings.text_format === "json" ? "" : " hidden") +
        ">",
      fieldHtml("json_schema_name"),
      fieldHtml("json_schema"),
      "</div>",
      "</div>",
    ].join("");

    var tabRouting = [
      '<div id="tab-routing" class="llm-tab-content" hidden>',
      UI.section("Вызов инструментов"),
      row(["tool_choice", "max_tool_calls"]),
      row(["parallel_tool_calls", "top_logprobs"]),
      UI.section("Приоритеты облака"),
      row(["service_tier", "previous_response_id"]),
      "</div>",
    ].join("");

    var tabAdv = [
      '<div id="tab-adv" class="llm-tab-content" hidden>',
      UI.section("Промпт-шаблоны и кэширование"),
      row(["prompt_cache_key", "prompt_id"]),
      row(["prompt_version", "prompt_variables"]),
      '<div class="settings-field-wide">' + fieldHtml("conv_metadata") + "</div>",
      "</div>",
    ].join("");

    var activeTheme = window.LogosTheme.getStored();
    var themes = window.LogosTheme.themes;
    var themeOptions = Object.keys(themes).map(function (key) {
      return '<option value="' + UI.escapeHtml(key) + '"' +
        (activeTheme === key ? " selected" : "") + ">" +
        UI.escapeHtml(themes[key]) + "</option>";
    }).join("");

    var tabTheme = [
      '<div id="tab-theme" class="llm-tab-content" hidden>',
      UI.section("Цветовая схема"),
      UI.lbl("Схема интерфейса"),
      UI.sel("set-theme", themeOptions),
      '<div class="settings-hint">',
      "Выбор сохраняется локально на устройстве и не передаётся модели. Переключатель в шапке циклически меняет схемы.",
      "</div>",
      "</div>",
    ].join("");

    var footer = '<div id="set-status" class="settings-status" role="status"></div>';

    md.innerHTML =
      tabsHeader + tabOllama + tabGen + tabOutput + tabRouting + tabAdv + tabTheme + footer;
    loadOllamaSettings(md.querySelector("#ollama-settings-body"), UI);
    // Keep the shared modal's accessible title and close action in the sticky header.
    var heading = md.querySelector(".settings-modal-heading");
    heading.appendChild(title);
    heading.appendChild(closeButton);
    CoreUI.modal.open(ov);

    md.querySelector("#set-status").textContent = unknownAtOpen
      ? "Общие настройки не загружены с сервера: показаны значения, встроенные в интерфейс. " + READ_ONLY_NOTE
      : READ_ONLY_NOTE;

    // Every parameter is read-only; only the colour scheme (kept on this device) can be chosen.
    disableControls(md);
    md.querySelector("#set-theme").disabled = false;

    md.querySelectorAll(".llm-tab-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        md.querySelectorAll(".llm-tab-btn").forEach(function (b) {
          b.classList.remove("active");
        });
        md.querySelectorAll(".llm-tab-content").forEach(function (c) {
          c.hidden = true;
        });
        btn.classList.add("active");
        var target = md.querySelector("#" + btn.getAttribute("data-tab"));
        if (target) target.hidden = false;
      });
    });

    var themeSelect = md.querySelector("#set-theme");
    themeSelect.addEventListener("change", function () {
      window.LogosTheme.apply(this.value, true);
    });
  };
})();

