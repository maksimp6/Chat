(function () {
  "use strict";

  // === 1. Модальное окно локальных инструментов (Tools) с немедленным сохранением в БД ===
  window.openToolsModal = function () {
    var UI = window.SettingsUI;
    var CoreUI = window.LogosCoreAPI.ui;
    var Storage = window.SettingsStorage;
    var settings = Storage.load(currentConvId);
    var cfg = settings.tools_config || {};
    var ws = cfg.web_search || {};
    var fs = cfg.file_search || {};
    var ci = cfg.code_interpreter || {};

    var existing = document.getElementById("tools-modal-custom");
    if (existing) existing.remove();

    var md = document.createElement("div");
    md.className = "settings-tools-body";

    md.innerHTML = [
      '<div style="font-size:12px;color:var(--m-muted,#666);margin-bottom:12px;">Управление локальными и встроенными инструментами. Отключенные инструменты не передаются модели.</div>',
      '<h3 style="font-size:14px;margin:12px 0 8px;">Локальные инструменты</h3>',
      '<div id="tools-category-list" style="display:flex;flex-direction:column;gap:8px;">Загрузка инструментов...</div>',
      '<hr style="margin:18px 0;border:none;border-top:1px solid var(--m-border,#ddd);">',
      '<h3 style="font-size:14px;margin:12px 0 8px;">Встроенные инструменты Yandex AI Studio</h3>',
      UI.chk(
        "tools-ws-en",
        ws.enabled || false,
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
            (!ws.context_size || ws.context_size === "medium" ? " selected" : "") +
            ">Medium</option>" +
            '<option value="high"' +
            (ws.context_size === "high" ? " selected" : "") +
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
      UI.chk("tools-ci-en", ci.enabled || false, "<strong>🧮 Code Interpreter</strong> (Python)"),
      '<hr style="margin:14px 0;border:none;border-top:1px solid var(--m-border,#ddd);">',
      UI.chk(
        "tools-fs-en",
        fs.enabled || false,
        "<strong>📚 File Search</strong> (поиск по Vector Store)",
      ),
      '<div style="margin-top:8px;">' +
        UI.lbl("Vector Store IDs") +
        UI.inp(
          "tools-fs-vids",
          "text",
          fs.vector_store_ids || "",
          {"placeholder": "vs_xxx, vs_yyy"},
        ) +
        "</div>",
      '<div style="margin-top:8px;">' +
        UI.lbl("Максимум результатов") +
        UI.inp("tools-fs-max", "number", fs.max_results || 20, {"min": "1"}) +
        "</div>",
      '<div style="margin-top:10px;color:var(--m-muted,#666);font-size:12px;line-height:1.5;">Эти инструменты выполняются на стороне Yandex AI Studio. Они независимы от MCP и локального Tool Registry.</div>',
      '<div id="tools-status" role="status" style="color:var(--m-danger,#c33);font-size:12px;min-height:16px;"></div>',
      '<div class="settings-modal-footer" style="display:flex;justify-content:flex-end;gap:10px;margin-top:16px;border-top:1px solid var(--m-border,#ddd);padding-top:12px;">',
      ' <button class="logos-btn settings-contract-btn" id="tools-save-btn">Сохранить инструменты</button>',
      "</div>",
    ].join("");
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
    function readOk(r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }
    function loadCategories() {
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
              '<input type="checkbox" class="tool-cat-chk" value="' +
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
      .catch(function () {
        var listEl = document.getElementById("tools-category-list");
        if (!listEl) return;
        listEl.replaceChildren();
        var message = document.createElement("p");
        message.textContent = "Не удалось загрузить список инструментов. Проверьте соединение.";
        var retry = document.createElement("button");
        retry.type = "button";
        retry.className = "logos-btn";
        retry.textContent = "Повторить";
        retry.addEventListener("click", loadCategories);
        listEl.append(message, retry);
      });
    }
    loadCategories();

    document.getElementById("tools-save-btn").addEventListener("click", function () {
      var selected = [];
      document.querySelectorAll(".tool-cat-chk:checked").forEach(function (chk) {
        selected.push(chk.value);
      });
      settings.tools_config = {
        web_search: {
          enabled: document.getElementById("tools-ws-en").checked,
          context_size: document.getElementById("tools-ws-context").value,
          allowed_domains: document.getElementById("tools-ws-allow").value.trim(),
          blocked_domains: document.getElementById("tools-ws-block").value.trim(),
        },
        code_interpreter: { enabled: document.getElementById("tools-ci-en").checked },
        file_search: {
          enabled: document.getElementById("tools-fs-en").checked,
          vector_store_ids: document.getElementById("tools-fs-vids").value.trim(),
          max_results: Math.max(
            1,
            parseInt(document.getElementById("tools-fs-max").value, 10) || 20,
          ),
        },
      };
      var status = document.getElementById("tools-status");
      status.textContent = "";
      var localSave = currentConvId
        ? window.LogosDispatcher.request("/api/conversations/" + currentConvId + "/tools", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ active_tool_categories: selected }),
          }).then(function (r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
          })
        : Promise.resolve();
      Promise.all([localSave, Storage.save(settings, currentConvId)]).then(closeModal, function () {
        status.textContent =
          "Не удалось сохранить инструменты на сервер. Настройки сохранены только в этом браузере.";
      });
    });
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
      displayFallback: "medium", ultraFixed: true,
      choices: [["disabled", "Disabled"], ["low", "Low"], ["medium", "Medium"], ["high", "High"]],
    },
    store: { id: "set-store", label: "Сохранять ответы", type: "checkbox", enabledByDefault: true },
    background: { id: "set-bg", label: "Фоновый опрос", type: "checkbox", enabledByDefault: true },
    stream: { id: "set-stream", label: "Потоковый режим", type: "checkbox" },
    instructions: {
      id: "set-instructions", label: "Системный промпт (Instructions)",
      type: "textarea", extra: "height:70px;",
    },
    text_format: {
      id: "set-text-fmt", label: "Формат", type: "select",
      choices: [["text", "Обычный текст (Text)"], ["json", "Строгий JSON (json_schema)"]],
    },
    text_verbosity: {
      id: "set-verbosity", label: "Детализация (Verbosity)", type: "select",
      choices: [["medium", "Medium"], ["concise", "Concise"], ["verbose", "Verbose"]],
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

  function readSettingsField(field, element) {
    if (field.type === "checkbox") return element.checked;
    if (field.read === "float") {
      var number = parseFloat(element.value);
      return Number.isNaN(number) ? field.fallback : number;
    }
    if (field.read === "integer") return parseInt(element.value, 10) || field.fallback;
    return field.read === "trim" ? element.value.trim() : element.value;
  }

  window.openSettingsModal = function () {
    var UI = window.SettingsUI;
    var CoreUI = window.LogosCoreAPI.ui;
    var Storage = window.SettingsStorage;
    var settings = Storage.load(currentConvId);
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
      '<div id="set-status" role="status" style="color:var(--m-danger,#c33);font-size:12px;min-height:16px;margin-top:auto;"></div>',
      '<div class="settings-modal-footer" style="display:flex;justify-content:flex-end;gap:10px;margin-top:20px;border-top:1px solid var(--m-border,#ddd);padding-top:14px;">',
      '    <button class="logos-btn settings-contract-btn" id="set-reset-btn">Сброс</button>',
      '    <button class="logos-btn settings-btn-accent" id="set-save-btn">Сохранить параметры</button>',
      "</div>",
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
    function showSaveError() {
      md.querySelector("#set-status").textContent =
        "Не удалось сохранить на сервер. Настройки сохранены только в этом браузере.";
    }
    ov.addEventListener("click", function (e) {
      if (e.target === ov) closeModal();
    });

    // Truncation and reasoning are fixed by Ultra; the output cap stays editable.
    Object.keys(SETTINGS_FIELDS).forEach(function (key) {
      if (ultra && SETTINGS_FIELDS[key].ultraFixed) fieldElement(key).disabled = true;
    });

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

    md.querySelector("#set-reset-btn").addEventListener("click", function () {
      if (!confirm("Сбросить параметры генерации к значениям по умолчанию?")) return;
      var defs = window.resetSettings(currentConvId);
      Storage.save(defs, currentConvId).then(closeModal, showSaveError);
    });

    md.querySelector("#set-save-btn").addEventListener("click", function () {
      Object.keys(SETTINGS_FIELDS).forEach(function (key) {
        settings[key] = readSettingsField(SETTINGS_FIELDS[key], fieldElement(key));
      });
      if (ultra) applyUltraProfile(settings, ultra);
      Storage.save(settings, currentConvId).then(closeModal, showSaveError);
    });
  };
})();

