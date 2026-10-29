(function () {
  "use strict";

  // Settings are read-only: the windows show them, nothing is saved (the server has no save route).
  var READ_ONLY_NOTE =
    "Настройки только для чтения: всё стоит на максимуме, изменить их здесь нельзя.";

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
    var convId = currentConvId;
    var settings = Storage.load(convId);
    var unknownAtOpen = Boolean(convId) && Storage.isUnknown(convId);
    var cfg = settings.tools_config || {};
    var ws = cfg.web_search || {};
    var fs = cfg.file_search || {};
    var ci = cfg.code_interpreter || {};
    var dl = cfg.defer_loading || {};
    var eager = Array.isArray(dl.eager_categories) ? dl.eager_categories.join(", ") : dl.eager_categories || "";

    var existing = document.getElementById("tools-modal-custom");
    if (existing) existing.remove();

    var md = document.createElement("div");
    md.className = "settings-tools-body";

    md.innerHTML = [
      '<div style="font-size:12px;color:var(--m-muted,#666);margin-bottom:12px;">Управление локальными и встроенными инструментами. Отключенные инструменты не передаются модели.</div>',
      '<h3 style="font-size:14px;margin:12px 0 8px;">Локальные инструменты</h3>',
      '<div id="tools-category-list" style="display:flex;flex-direction:column;gap:8px;">Загрузка инструментов...</div>',
      '<div style="margin-top:12px;font-size:13px;"><strong>⏳ Defer Loading</strong> — всегда включено: модель видит инструмент только после поиска инструментов провайдера. Отключить нельзя.</div>',
      '<div style="margin-top:8px;">' +
        UI.lbl("Категории без отложенной загрузки (всегда загружены)") +
        UI.inp("tools-defer-eager", "text", eager, {"placeholder": "rootagent, filesystem"}) +
        "</div>",
      '<hr style="margin:18px 0;border:none;border-top:1px solid var(--m-border,#ddd);">',
      '<h3 style="font-size:14px;margin:12px 0 8px;">Встроенные инструменты Yandex AI Studio</h3>',
      UI.chk(
        "tools-ws-en",
        // An absent setting means on, as the server applies it.
        ws.enabled !== false,
        "<strong>🌐 Web Search</strong> (поиск в интернете)",
      ),
      '<div style="margin-top:8px;">' +
        UI.lbl("Search Context Size") +
        UI.sel(
          "tools-ws-context",
          '<option value="low"' +
            (ws.context_size === "low" ? " selected" : "") +
            ">Low</option>" +
            '<option value="medium"' +
            (ws.context_size === "medium" ? " selected" : "") +
            ">Medium</option>" +
            '<option value="high"' +
            (!ws.context_size || ws.context_size === "high" ? " selected" : "") +
            ">High</option>",
        ) +
        "</div>",
      '<div style="margin-top:8px;">' +
        UI.lbl("Разрешённые домены") +
        UI.inp(
          "tools-ws-allow",
          "text",
          ws.allowed_domains || "",
          {"placeholder": "example.com, wikipedia.org"},
        ) +
        "</div>",
      '<div style="margin-top:8px;">' +
        UI.lbl("Заблокированные домены") +
        UI.inp("tools-ws-block", "text", ws.blocked_domains || "", {"placeholder": "example.org"}) +
        "</div>",
      '<hr style="margin:14px 0;border:none;border-top:1px solid var(--m-border,#ddd);">',
      UI.chk("tools-ci-en", ci.enabled !== false, "<strong>🧮 Code Interpreter</strong> (Python)"),
      '<hr style="margin:14px 0;border:none;border-top:1px solid var(--m-border,#ddd);">',
      UI.chk(
        "tools-fs-en",
        fs.enabled !== false,
        "<strong>📚 File Search</strong> (поиск по Vector Store)",
      ),
      '<div style="margin-top:8px;">' +
        UI.lbl("Vector Store IDs (основное хранилище знаний подключено всегда)") +
        UI.inp(
          "tools-fs-vids",
          "text",
          fs.vector_store_ids || "",
          {"placeholder": "vs_xxx, vs_yyy"},
        ) +
        "</div>",
      '<div style="margin-top:8px;">' +
        UI.lbl("Максимум результатов") +
        UI.inp("tools-fs-max", "number", fs.max_results || 50, {"min": "1", "max": "50"}) +
        "</div>",
      '<div style="margin-top:10px;color:var(--m-muted,#666);font-size:12px;line-height:1.5;">Эти инструменты выполняются на стороне Yandex AI Studio. Они независимы от MCP и локального Tool Registry.</div>',
      '<div id="tools-status" role="status" style="color:var(--m-muted,#666);font-size:12px;min-height:16px;margin-top:12px;"></div>',
    ].join("");
    disableControls(md);
    var ov = CoreUI.modal.create({
      id: "tools-modal-custom",
      title: "🧰 Инструменты",
      className: "tools-modal-custom",
      contentClassName: "settings-tools-modal-content",
      body: md,
    });
    var modalContent = ov.querySelector(".modal-content");
    modalContent.style.cssText =
      "background:var(--m-bg,#fff);border-radius:12px;padding:20px;max-width:720px;width:94%;max-height:90vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.3);color:var(--m-text,#222);";
    (document.querySelector(".logos-pro-app") || document.body).appendChild(ov);
    CoreUI.modal.open(ov);

    function closeModal() {
      CoreUI.modal.close(ov);
    }
    ov.addEventListener("click", function (e) {
      if (e.target === ov) closeModal();
    });

    var categoryLabels = {
      termux: "📱 Termux Hardware (батарея, буфер, сенсоры, TTS, тосты)",
      git: "🌿 Local Git (статус, ветки, diff, коммиты)",
      filesystem: "⚙️ Файловая система (чтение, запись, apply_patch)",
      system: "🔧 Системные драйверы Termux",
      wikipedia: "📚 Wikipedia Engine (поиск и сводка статей)",
      profiler: "⏱️ Профайлер и инспекция состояния",
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
      if (status === 404 && error.code === "conversation_not_found") {
        return "Диалог не найден на сервере: его удалили. Закройте окно и выберите другой диалог.";
      }
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
    Promise.all([
      window.LogosDispatcher.request("/api/tools/categories").then(readOk),
      currentConvId
        ? window.LogosDispatcher.request("/api/conversations/" + currentConvId + "/tools").then(
            readOk,
          )
        : Promise.resolve({ active_tool_categories: null }),
    ])
      .then(function (results) {
        var catData = results[0];
        var activeData = results[1];
        var listEl = document.getElementById("tools-category-list");
        var categories = catData.categories || {};
        var active =
          activeData && activeData.active_tool_categories !== null
            ? activeData.active_tool_categories
            : Object.keys(categories);
        listEl.innerHTML = Object.keys(categories)
          .map(function (cat) {
            var isChecked = active.indexOf(cat) !== -1;
            var label = categoryLabels[cat] || "Модуль: " + cat;
            var count = (categories[cat] || []).length;
            return (
              '<label style="display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--m-border,#ddd);border-radius:6px;background:var(--m-card,#fff);cursor:pointer;">' +
              '<input type="checkbox" class="tool-cat-chk" disabled value="' +
              UI.escapeHtml(cat) +
              '" ' +
              (isChecked ? "checked" : "") +
              ">" +
              '<div style="flex:1;"><div style="font-weight:600;font-size:13px;color:var(--m-text,#222);">' +
              UI.escapeHtml(label) +
              "</div>" +
              '<div style="font-size:11px;color:var(--m-muted,#666);">' +
              count +
              " функций доступно</div></div></label>"
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
      ? "Настройки инструментов этого диалога не загружены с сервера: показаны значения по умолчанию. " + READ_ONLY_NOTE
      : READ_ONLY_NOTE;
    loadCategories();
  };

  // === 2. Полное окно конфигурации параметров LLM ===
  function ultraProfileFor(modelKey) {
    var storage = window.SettingsStorage;
    var text = typeof modelsData !== "undefined" && modelsData.text ? modelsData.text : {};
    var profile = typeof storage.ultraProfileFor === "function"
      ? storage.ultraProfileFor(modelKey)
      : (text[modelKey] || {}).ultra_profile;
    return profile && profile.default_profile === "ultra" && profile.settings_defaults
      ? profile : null;
  }

  function applyUltraProfile(settings, profile) {
    var userCap = parseInt(settings.max_output_tokens, 10) || 0;
    Object.keys(profile.settings_defaults).forEach(function (key) {
      settings[key] = profile.settings_defaults[key];
    });
    // An output cap the user set is kept; 0 means no cap.
    if (userCap > 0) settings.max_output_tokens = userCap;
    settings.model_profile = "ultra";
    settings.model_profile_id = profile.profile_id;
    return settings;
  }

  function ultraProfileNote(profile) {
    if (!profile) return "";
    var defaults = profile.settings_defaults;
    var depth = defaults.reasoning_effort === "disabled"
      ? "параметр не передаётся для этой модели"
      : defaults.reasoning_effort;
    return (
      '<div style="margin-bottom:12px;font-size:13px;color:var(--m-text,#222);">' +
      "<strong>Профиль: Ultra — по умолчанию</strong><br>" +
      "Ответ — максимум провайдера, если поле токенов пустое или 0; контекст не обрезается; " +
      "глубина размышлений — " + window.SettingsUI.escapeHtml(depth) + "." +
      "</div>"
    );
  }

  // IDs, display values and readers live together so a field has one binding.
  var SETTINGS_FIELDS = {
    temperature: {
      id: "set-temp", label: "Temperature", type: "number", read: "float", fallback: 0.7,
      extra: {"step": "0.1", "min": "0", "max": "2"},
    },
    top_p: {
      id: "set-topp", label: "Top P", type: "number", read: "float", fallback: 1.0,
      extra: {"step": "0.05", "min": "0", "max": "1"},
    },
    max_output_tokens: {
      id: "set-tokens", label: "Токены ответа (0 — максимум провайдера)",
      type: "number", read: "integer", fallback: 0, extra: {"step": "100", "min": "0"},
    },
    truncation: {
      id: "set-trunc", label: "Truncation", type: "select", ultraFixed: true,
      choices: [["disabled", "Disabled"], ["auto", "Auto"]],
    },
    reasoning_effort: {
      id: "set-reasoning", label: "Глубина размышлений", type: "select",
      displayFallback: "high", ultraFixed: true,
      choices: [["disabled", "Disabled"], ["low", "Low"], ["medium", "Medium"], ["high", "High"]],
    },
    store: { id: "set-store", label: "Сохранять ответы", type: "checkbox", enabledByDefault: true },
    background: { id: "set-bg", label: "Фоновый опрос", type: "checkbox", enabledByDefault: true },
    stream: { id: "set-stream", label: "Потоковый режим", type: "checkbox", enabledByDefault: true },
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
      choices: [["low", "Low"], ["medium", "Medium"], ["high", "High"]],
    },
    json_schema_name: {
      id: "set-js-name", label: "Название схемы", type: "text", read: "trim",
      displayFallback: "response", extra: {"style": "margin-bottom:8px;"},
    },
    json_schema: {
      id: "set-js-schema", label: "JSON Schema definition", type: "textarea", read: "trim",
      displayFallback: "", extra: "height:100px;font-family:monospace;font-size:11px;",
    },
    tool_choice: {
      id: "set-tool-choice", label: "Tool Choice", type: "select",
      choices: [["auto", "Auto"], ["required", "Required"], ["none", "None"]],
    },
    max_tool_calls: {
      id: "set-max-tools", label: "Max Tool Calls", type: "number", read: "integer",
      fallback: null, displayFallback: "", extra: {"min": "1"},
    },
    parallel_tool_calls: {
      id: "set-parallel-tools", label: "Параллельные вызовы (Parallel Tool Calls)",
      type: "checkbox", enabledByDefault: true,
    },
    top_logprobs: {
      id: "set-logprobs", label: "Top Logprobs", type: "number", read: "integer",
      fallback: 0, displayFallback: 0, extra: {"min": "0", "max": "5"},
    },
    service_tier: {
      id: "set-tier", label: "Service Tier", type: "select",
      choices: [["auto", "Auto (Default)"], ["priority", "Priority"]],
    },
    previous_response_id: {
      id: "set-prev-id", label: "Previous Response ID", type: "text", read: "trim",
      displayFallback: "", extra: {"placeholder": "UUID"},
    },
    prompt_cache_key: {
      id: "set-cache-key", label: "Prompt Cache Key", type: "text", read: "trim",
      displayFallback: "", extra: {"placeholder": "cache-id"},
    },
    prompt_id: {
      id: "set-prompt-id", label: "Prompt ID (Шаблон)", type: "text", read: "trim",
      displayFallback: "", extra: {"placeholder": "Шаблон Yandex Studio"},
    },
    prompt_version: {
      id: "set-prompt-ver", label: "Prompt Version", type: "text", read: "trim",
      displayFallback: "", extra: {"placeholder": "latest"},
    },
    prompt_variables: {
      id: "set-prompt-vars", label: "Prompt Variables (JSON)", type: "text", read: "trim",
      displayFallback: "", extra: {"placeholder": "{\"key\":\"val\"}"},
    },
    conv_metadata: {
      id: "set-conv-meta", label: "Диалоговые метаданные (Metadata JSON)", type: "textarea",
      read: "trim", displayFallback: "", extra: "height:60px;font-family:monospace;font-size:11px;",
    },
  };

  window.openSettingsModal = function () {
    var UI = window.SettingsUI;
    var CoreUI = window.LogosCoreAPI.ui;
    var Storage = window.SettingsStorage;
    var convId = currentConvId;
    var settings = Storage.load(convId);
    var unknownAtOpen = Boolean(convId) && Storage.isUnknown(convId);
    var ultra = ultraProfileFor(typeof currentModel !== "undefined" ? currentModel : "");
    if (ultra) settings = applyUltraProfile(settings, ultra);

    var existing = document.getElementById("settings-modal-custom");
    if (existing) {
      CoreUI.modal.close(existing);
      existing.remove();
    }

    var ov = CoreUI.modal.create({
      id: "settings-modal-custom",
      title: "⚙️ Конфигурация LLM & Responses API",
      contentClassName: "settings-modal-panel",
    });
    ov.style.zIndex = "10002";
    var md = ov.querySelector(".modal-content");
    md.style.cssText =
      "background:var(--m-bg,#fff);border-radius:12px;padding:20px;max-width:720px;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.3);color:var(--m-text,#222);display:flex;flex-direction:column;";
    var title = md.querySelector(".modal-title");
    title.style.cssText = "margin:0;font-size:18px;color:var(--m-text,#222);";
    var closeButton = md.querySelector(".modal-close");
    closeButton.id = "set-close-btn";
    closeButton.classList.add("settings-contract-btn");

    function fieldElement(key) {
      return md.querySelector("#" + SETTINGS_FIELDS[key].id);
    }

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
      '<div class="settings-modal-heading" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;"></div>',
      '<div class="llm-tabs" style="display:flex;gap:6px;border-bottom:1px solid var(--m-border,#ddd);flex-wrap:wrap;">',
      '    <button class="logos-btn llm-tab-btn active settings-contract-btn" data-tab="tab-gen">Сэмплинг</button>',
      '    <button class="logos-btn llm-tab-btn settings-contract-btn" data-tab="tab-output">Формат вывода</button>',
      '    <button class="logos-btn llm-tab-btn settings-contract-btn" data-tab="tab-routing">Маршрутизация вызовов</button>',
      '    <button class="logos-btn llm-tab-btn settings-contract-btn" data-tab="tab-adv">Промпты & Кэш</button>',
      '    <button class="logos-btn llm-tab-btn settings-contract-btn" data-tab="tab-theme">Оформление</button>',
      "</div>",
      "</div>",
    ].join("");

    var tabGen = [
      '<div id="tab-gen" class="llm-tab-content">',
      UI.section("Базовый сэмплинг"),
      ultraProfileNote(ultra),
      row(["temperature", "top_p"]),
      row(["max_output_tokens", "truncation"]),
      row(["reasoning_effort", null]),
      row(["store", "background", "stream"]),
      '<div style="margin-top:10px;">' + fieldHtml("instructions") + "</div>",
      "</div>",
    ].join("");

    var tabOutput = [
      '<div id="tab-output" class="llm-tab-content" style="display:none;">',
      UI.section("Структура ответа (Text / JSON Schema)"),
      row(["text_format", "text_verbosity"]),
      '<div id="json-schema-wrap" style="' +
        (settings.text_format === "json" ? "" : "display:none;") +
        'margin-top:10px;">',
      fieldHtml("json_schema_name"),
      fieldHtml("json_schema"),
      "</div>",
      "</div>",
    ].join("");

    var tabRouting = [
      '<div id="tab-routing" class="llm-tab-content" style="display:none;">',
      UI.section("Политики Tool Calling"),
      row(["tool_choice", "max_tool_calls"]),
      row(["parallel_tool_calls", "top_logprobs"]),
      UI.section("Приоритеты облака"),
      row(["service_tier", "previous_response_id"]),
      "</div>",
    ].join("");

    var tabAdv = [
      '<div id="tab-adv" class="llm-tab-content" style="display:none;">',
      UI.section("Промпт-шаблоны и кэширование"),
      row(["prompt_cache_key", "prompt_id"]),
      row(["prompt_version", "prompt_variables"]),
      '<div style="margin-top:10px;">' + fieldHtml("conv_metadata") + "</div>",
      "</div>",
    ].join("");

    var activeTheme = window.LogosTheme ? window.LogosTheme.getStored() : "light";
    var themes = (window.LogosTheme && window.LogosTheme.themes) || {
      light: "Светлая",
      dark: "Тёмная",
      dim: "Приглушённая",
      "high-contrast": "Высокий контраст",
    };
    var themeOptions = Object.keys(themes).map(function (key) {
      var label = ((window.LogosTheme && window.LogosTheme.themes) || {})[key] || key;
      return '<option value="' + UI.escapeHtml(key) + '"' +
        (activeTheme === key ? " selected" : "") + ">" +
        UI.escapeHtml(label) + "</option>";
    }).join("");

    var tabTheme = [
      '<div id="tab-theme" class="llm-tab-content" style="display:none;">',
      UI.section("Цветовая схема"),
      UI.lbl("Схема интерфейса"),
      UI.sel("set-theme", themeOptions),
      '<div style="margin-top:10px;color:var(--m-muted,#666);font-size:12px;line-height:1.5;">',
      "Выбор сохраняется локально на устройстве и не передаётся модели. Переключатель в шапке циклически меняет схемы.",
      "</div>",
      "</div>",
    ].join("");

    var footer = [
      '<div id="set-status" role="status" style="color:var(--m-muted,#666);font-size:12px;min-height:16px;margin-top:auto;"></div>',
    ].join("");

    md.innerHTML = tabsHeader + tabGen + tabOutput + tabRouting + tabAdv + tabTheme + footer;
    var heading = md.querySelector(".settings-modal-heading");
    heading.appendChild(title);
    heading.appendChild(closeButton);
    // Keep the shared modal's accessible title and close action in the sticky header.
    (document.querySelector(".logos-pro-app") || document.body).appendChild(ov);
    CoreUI.modal.open(ov);

    function closeModal() {
      CoreUI.modal.close(ov);
    }
    md.querySelector("#set-status").textContent = unknownAtOpen
      ? "Сохранённые параметры этого диалога не загружены: показаны локальные значения или значения по умолчанию. " + READ_ONLY_NOTE
      : READ_ONLY_NOTE;
    ov.addEventListener("click", function (e) {
      if (e.target === ov) closeModal();
    });

    // Every parameter is read-only; only the colour scheme (kept on this device) can be chosen.
    disableControls(md);
    md.querySelector("#set-theme").disabled = false;

    md.querySelectorAll(".llm-tab-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        md.querySelectorAll(".llm-tab-btn").forEach(function (b) {
          b.classList.remove("active");
        });
        md.querySelectorAll(".llm-tab-content").forEach(function (c) {
          c.style.display = "none";
        });
        btn.classList.add("active");
        var target = md.querySelector("#" + btn.getAttribute("data-tab"));
        if (target) target.style.display = "block";
      });
    });

    var themeSelect = md.querySelector("#set-theme");
    themeSelect.addEventListener("change", function () {
      if (window.LogosTheme) window.LogosTheme.apply(this.value, true);
    });

    fieldElement("text_format").addEventListener("change", function () {
      md.querySelector("#json-schema-wrap").style.display =
        this.value === "json" ? "block" : "none";
    });

  };
})();

