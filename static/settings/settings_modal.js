(function () {
  "use strict";

  // === 1. Модальное окно локальных инструментов (Tools) с немедленным сохранением в БД ===
  window.openToolsModal = function () {
    var UI = window.SettingsUI;
    var CoreUI = window.AliceCoreAPI.ui;
    var Storage = window.SettingsStorage;
    var currentConvId = typeof window.currentConvId !== "undefined" ? window.currentConvId : null;
    var settings = Storage.load(currentConvId);
    var cfg = settings.tools_config || {};
    var ws = cfg.web_search || {};
    var fs = cfg.file_search || {};
    var ci = cfg.code_interpreter || {};

    var existing = document.getElementById("tools-modal-custom");
    if (existing) existing.remove();

    var ov = document.createElement("div");
    ov.id = "tools-modal-custom";
    ov.className = "modal tools-modal-custom";
    ov.hidden = true;
    ov.setAttribute("role", "dialog");
    ov.setAttribute("aria-modal", "true");
    ov.setAttribute("aria-hidden", "true");
    ov.setAttribute("aria-labelledby", "tools-modal-title");
    ov.style.cssText =
      "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:10001;";

    var md = document.createElement("div");
    md.className = "modal-content settings-tools-modal-content";
    md.style.cssText =
      "background:var(--m-bg,#fff);border-radius:12px;padding:20px;max-width:720px;width:94%;max-height:90vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.3);color:var(--m-text,#222);";

    md.innerHTML = [
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">',
      ' <h2 id="tools-modal-title" style="margin:0;font-size:18px;color:var(--m-text,#222);">🧰 Инструменты</h2>',
      ' <button class="alice-btn settings-contract-btn" id="tools-close-btn" data-action="modal.close" data-modal="tools-modal-custom">&times;</button>',
      "</div>",
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
          ' placeholder="example.com, wikipedia.org"',
        ) +
        "</div>",
      '<div style="margin-top:8px;">' +
        UI.lbl("Заблокированные домены") +
        UI.inp("tools-ws-block", "text", ws.blocked_domains || "", ' placeholder="example.org"') +
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
          ' placeholder="vs_xxx, vs_yyy"',
        ) +
        "</div>",
      '<div style="margin-top:8px;">' +
        UI.lbl("Максимум результатов") +
        UI.inp("tools-fs-max", "number", fs.max_results || 20, ' min="1"') +
        "</div>",
      '<div style="margin-top:10px;color:var(--m-muted,#666);font-size:12px;line-height:1.5;">Эти инструменты выполняются на стороне Yandex AI Studio. Они независимы от MCP и локального Tool Registry.</div>',
      '<div style="display:flex;justify-content:flex-end;gap:10px;margin-top:16px;border-top:1px solid var(--m-border,#ddd);padding-top:12px;">',
      ' <button class="alice-btn settings-contract-btn" id="tools-save-btn">Сохранить инструменты</button>',
      "</div>",
    ].join("");
    ov.appendChild(md);
    (document.querySelector(".alice-pro-app") || document.body).appendChild(ov);
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

    Promise.all([
      window.AliceDispatcher.request("/api/tools/categories").then(function (r) {
        return r.json();
      }),
      currentConvId
        ? window.AliceDispatcher.request("/api/conversations/" + currentConvId + "/tools").then(
            function (r) {
              return r.json();
            },
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
        if (listEl) listEl.textContent = "Ошибка загрузки категорий";
      });

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
      var localSave = currentConvId
        ? window.AliceDispatcher.request("/api/conversations/" + currentConvId + "/tools", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ active_tool_categories: selected }),
          })
        : Promise.resolve();
      Promise.all([localSave, Promise.resolve(Storage.save(settings, currentConvId))])
        .then(closeModal)
        .catch(closeModal);
    });
  };

  // === 2. Полное окно конфигурации параметров LLM ===
  window.openSettingsModal = function () {
    var UI = window.SettingsUI;
    var Storage = window.SettingsStorage;
    var currentConvId = typeof window.currentConvId !== "undefined" ? window.currentConvId : null;
    var settings = Storage.load(currentConvId);

    var existing = document.getElementById("settings-modal-custom");
    if (existing) existing.remove();

    var ov = document.createElement("div");
    ov.id = "settings-modal-custom";
    ov.style.cssText =
      "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:10001;display:flex;align-items:center;justify-content:center;";

    var md = document.createElement("div");
    md.style.cssText =
      "background:var(--m-bg,#fff);border-radius:12px;padding:20px;max-width:720px;width:94%;max-height:90vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.3);color:var(--m-text,#222);display:flex;flex-direction:column;";

    var tabsHeader = [
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">',
      '    <h2 style="margin:0;font-size:18px;color:var(--m-text,#222);">⚙️ Конфигурация LLM & Responses API</h2>',
      '    <button class="alice-btn settings-contract-btn" id="set-close-btn">&times;</button>',
      "</div>",
      '<div style="display:flex;gap:6px;border-bottom:1px solid var(--m-border,#ddd);margin-bottom:14px;overflow-x:auto;">',
      '    <button class="alice-btn llm-tab-btn active settings-contract-btn" data-tab="tab-gen">Сэмплинг</button>',
      '    <button class="alice-btn llm-tab-btn settings-contract-btn" data-tab="tab-output">Формат вывода</button>',
      '    <button class="alice-btn llm-tab-btn settings-contract-btn" data-tab="tab-routing">Маршрутизация вызовов</button>',
      '    <button class="alice-btn llm-tab-btn settings-contract-btn" data-tab="tab-adv">Промпты & Кэш</button>',
      '    <button class="alice-btn llm-tab-btn settings-contract-btn" data-tab="tab-theme">Оформление</button>',
      "</div>",
    ].join("");

    var tabGen = [
      '<div id="tab-gen" class="llm-tab-content">',
      UI.section("Базовый сэмплинг"),
      UI.gap2(
        "<div>" +
          UI.lbl("Temperature") +
          UI.inp("set-temp", "number", settings.temperature, ' step="0.1" min="0" max="2"') +
          "</div>",
        "<div>" +
          UI.lbl("Top P") +
          UI.inp("set-topp", "number", settings.top_p, ' step="0.05" min="0" max="1"') +
          "</div>",
      ),
      UI.gap2(
        "<div>" +
          UI.lbl("Max Output Tokens") +
          UI.inp("set-tokens", "number", settings.max_output_tokens, ' step="100" min="1"') +
          "</div>",
        "<div>" +
          UI.lbl("Truncation") +
          UI.sel(
            "set-trunc",
            '<option value="disabled"' +
              (settings.truncation === "disabled" ? " selected" : "") +
              '>Disabled</option><option value="auto"' +
              (settings.truncation === "auto" ? " selected" : "") +
              ">Auto</option>",
          ) +
          "</div>",
      ),
      UI.gap2(
        "<div>" +
          UI.lbl("Reasoning Effort (Мышление)") +
          UI.sel(
            "set-reasoning",
            '<option value="disabled"' +
              (settings.reasoning_effort === "disabled" ? " selected" : "") +
              '>Disabled</option><option value="low"' +
              (settings.reasoning_effort === "low" ? " selected" : "") +
              '>Low</option><option value="medium"' +
              (!settings.reasoning_effort || settings.reasoning_effort === "medium"
                ? " selected"
                : "") +
              '>Medium</option><option value="high"' +
              (settings.reasoning_effort === "high" ? " selected" : "") +
              ">High</option>",
          ) +
          "</div>",
        "<div></div>",
      ),
      UI.gap3(
        "<div>" + UI.chk("set-store", settings.store !== false, "Store Responses") + "</div>",
        "<div>" + UI.chk("set-bg", settings.background !== false, "Background Polling") + "</div>",
        "<div>" + UI.chk("set-stream", settings.stream === true, "Streaming Mode") + "</div>",
      ),
      '<div style="margin-top:10px;">' +
        UI.lbl("Системный промпт (Instructions)") +
        UI.ta("set-instructions", settings.instructions, "height:70px;") +
        "</div>",
      "</div>",
    ].join("");

    var tabOutput = [
      '<div id="tab-output" class="llm-tab-content" style="display:none;">',
      UI.section("Структура ответа (Text / JSON Schema)"),
      UI.gap2(
        "<div>" +
          UI.lbl("Формат") +
          UI.sel(
            "set-text-fmt",
            '<option value="text"' +
              (settings.text_format === "text" ? " selected" : "") +
              '>Обычный текст (Text)</option><option value="json"' +
              (settings.text_format === "json" ? " selected" : "") +
              ">Строгий JSON (json_schema)</option>",
          ) +
          "</div>",
        "<div>" +
          UI.lbl("Детализация (Verbosity)") +
          UI.sel(
            "set-verbosity",
            '<option value="medium"' +
              (settings.text_verbosity === "medium" ? " selected" : "") +
              '>Medium</option><option value="concise"' +
              (settings.text_verbosity === "concise" ? " selected" : "") +
              '>Concise</option><option value="verbose"' +
              (settings.text_verbosity === "verbose" ? " selected" : "") +
              ">Verbose</option>",
          ) +
          "</div>",
      ),
      '<div id="json-schema-wrap" style="' +
        (settings.text_format === "json" ? "" : "display:none;") +
        'margin-top:10px;">',
      UI.lbl("Название схемы") +
        UI.inp(
          "set-js-name",
          "text",
          settings.json_schema_name || "response",
          ' style="margin-bottom:8px;"',
        ),
      UI.lbl("JSON Schema definition") +
        UI.ta(
          "set-js-schema",
          settings.json_schema || "",
          "height:100px;font-family:monospace;font-size:11px;",
        ),
      "</div>",
      "</div>",
    ].join("");

    var tabRouting = [
      '<div id="tab-routing" class="llm-tab-content" style="display:none;">',
      UI.section("Политики Tool Calling"),
      UI.gap2(
        "<div>" +
          UI.lbl("Tool Choice") +
          UI.sel(
            "set-tool-choice",
            '<option value="auto"' +
              (settings.tool_choice === "auto" ? " selected" : "") +
              '>Auto</option><option value="required"' +
              (settings.tool_choice === "required" ? " selected" : "") +
              '>Required</option><option value="none"' +
              (settings.tool_choice === "none" ? " selected" : "") +
              ">None</option>",
          ) +
          "</div>",
        "<div>" +
          UI.lbl("Max Tool Calls") +
          UI.inp("set-max-tools", "number", settings.max_tool_calls || 10, ' min="1" max="50"') +
          "</div>",
      ),
      UI.gap2(
        "<div>" +
          UI.chk(
            "set-parallel-tools",
            settings.parallel_tool_calls !== false,
            "Параллельные вызовы (Parallel Tool Calls)",
          ) +
          "</div>",
        "<div>" +
          UI.lbl("Top Logprobs") +
          UI.inp("set-logprobs", "number", settings.top_logprobs || 0, ' min="0" max="5"') +
          "</div>",
      ),
      UI.section("Приоритеты облака"),
      UI.gap2(
        "<div>" +
          UI.lbl("Service Tier") +
          UI.sel(
            "set-tier",
            '<option value="auto"' +
              (settings.service_tier === "auto" ? " selected" : "") +
              '>Auto (Default)</option><option value="priority"' +
              (settings.service_tier === "priority" ? " selected" : "") +
              ">Priority</option>",
          ) +
          "</div>",
        "<div>" +
          UI.lbl("Previous Response ID") +
          UI.inp(
            "set-prev-id",
            "text",
            settings.previous_response_id || "",
            ' placeholder="UUID"',
          ) +
          "</div>",
      ),
      "</div>",
    ].join("");

    var tabAdv = [
      '<div id="tab-adv" class="llm-tab-content" style="display:none;">',
      UI.section("Промпт-шаблоны и кэширование"),
      UI.gap2(
        "<div>" +
          UI.lbl("Prompt Cache Key") +
          UI.inp(
            "set-cache-key",
            "text",
            settings.prompt_cache_key || "",
            ' placeholder="cache-id"',
          ) +
          "</div>",
        "<div>" +
          UI.lbl("Prompt ID (Шаблон)") +
          UI.inp(
            "set-prompt-id",
            "text",
            settings.prompt_id || "",
            ' placeholder="Шаблон Yandex Studio"',
          ) +
          "</div>",
      ),
      UI.gap2(
        "<div>" +
          UI.lbl("Prompt Version") +
          UI.inp("set-prompt-ver", "text", settings.prompt_version || "", ' placeholder="latest"') +
          "</div>",
        "<div>" +
          UI.lbl("Prompt Variables (JSON)") +
          UI.inp(
            "set-prompt-vars",
            "text",
            settings.prompt_variables || "",
            ' placeholder=\'{"key":"val"}\'',
          ) +
          "</div>",
      ),
      '<div style="margin-top:10px;">' +
        UI.lbl("Диалоговые метаданные (Metadata JSON)") +
        UI.ta(
          "set-conv-meta",
          settings.conv_metadata || "",
          "height:60px;font-family:monospace;font-size:11px;",
        ) +
        "</div>",
      "</div>",
    ].join("");

    var activeTheme = window.AliceTheme ? window.AliceTheme.getStored() : "light";
    var themeOptions = Object.keys(
      (window.AliceTheme && window.AliceTheme.themes) || {
        light: "Светлая",
        dark: "Тёмная",
        dim: "Приглушённая",
        "high-contrast": "Высокий контраст",
      },
    )
      .map(function (key) {
        var label = (window.AliceTheme.themes || {})[key] || key;
        return (
          '<option value="' +
          UI.escapeHtml(key) +
          '"' +
          (activeTheme === key ? " selected" : "") +
          ">" +
          UI.escapeHtml(label) +
          "</option>"
        );
      })
      .join("");

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
      '<div style="display:flex;justify-content:flex-end;gap:10px;margin-top:20px;border-top:1px solid var(--m-border,#ddd);padding-top:14px;">',
      '    <button class="alice-btn settings-contract-btn" id="set-reset-btn">Сброс</button>',
      '    <button class="alice-btn settings-contract-btn" id="set-save-btn">Сохранить параметры</button>',
      "</div>",
    ].join("");

    md.innerHTML = tabsHeader + tabGen + tabOutput + tabRouting + tabAdv + tabTheme + footer;
    ov.appendChild(md);
    document.body.appendChild(ov);

    function closeModal() {
      ov.remove();
    }
    document.getElementById("set-close-btn").addEventListener("click", closeModal);
    ov.addEventListener("click", function (e) {
      if (e.target === ov) closeModal();
    });

    md.querySelectorAll(".llm-tab-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        md.querySelectorAll(".llm-tab-btn").forEach(function (b) {
          b.classList.remove("active");
          b.style.fontWeight = "normal";
          b.style.borderBottom = "none";
          b.style.color = "var(--m-muted,#666)";
        });
        md.querySelectorAll(".llm-tab-content").forEach(function (c) {
          c.style.display = "none";
        });

        btn.classList.add("active");
        btn.style.fontWeight = "600";
        btn.style.borderBottom = "2px solid var(--m-accent,#4a90d9)";
        btn.style.color = "var(--m-text,#222)";

        var target = document.getElementById(btn.getAttribute("data-tab"));
        if (target) target.style.display = "block";
      });
    });

    var themeSelect = document.getElementById("set-theme");
    if (themeSelect) {
      themeSelect.addEventListener("change", function () {
        if (window.AliceTheme) window.AliceTheme.apply(this.value, true);
      });
    }

    document.getElementById("set-text-fmt").addEventListener("change", function () {
      document.getElementById("json-schema-wrap").style.display =
        this.value === "json" ? "block" : "none";
    });

    document.getElementById("set-reset-btn").addEventListener("click", function () {
      if (!confirm("Сбросить параметры генерации к значениям по умолчанию?")) return;
      var defs = window.resetSettings(currentConvId);
      Storage.save(defs, currentConvId);
      closeModal();
    });

    document.getElementById("set-save-btn").addEventListener("click", function () {
      settings.temperature = parseFloat(document.getElementById("set-temp").value) || 0.7;
      settings.top_p = parseFloat(document.getElementById("set-topp").value) || 1.0;
      settings.max_output_tokens =
        parseInt(document.getElementById("set-tokens").value, 10) || 2000;
      settings.truncation = document.getElementById("set-trunc").value;
      settings.reasoning_effort = document.getElementById("set-reasoning").value;
      settings.store = document.getElementById("set-store").checked;
      settings.background = document.getElementById("set-bg").checked;
      settings.stream = document.getElementById("set-stream").checked;
      settings.instructions = document.getElementById("set-instructions").value;

      settings.text_format = document.getElementById("set-text-fmt").value;
      settings.text_verbosity = document.getElementById("set-verbosity").value;
      settings.json_schema_name = document.getElementById("set-js-name").value.trim();
      settings.json_schema = document.getElementById("set-js-schema").value.trim();

      settings.tool_choice = document.getElementById("set-tool-choice").value;
      settings.max_tool_calls = parseInt(document.getElementById("set-max-tools").value, 10) || 10;
      settings.parallel_tool_calls = document.getElementById("set-parallel-tools").checked;
      settings.top_logprobs = parseInt(document.getElementById("set-logprobs").value, 10) || 0;

      settings.service_tier = document.getElementById("set-tier").value;
      settings.previous_response_id = document.getElementById("set-prev-id").value.trim();

      settings.prompt_cache_key = document.getElementById("set-cache-key").value.trim();
      settings.prompt_id = document.getElementById("set-prompt-id").value.trim();
      settings.prompt_version = document.getElementById("set-prompt-ver").value.trim();
      settings.prompt_variables = document.getElementById("set-prompt-vars").value.trim();
      settings.conv_metadata = document.getElementById("set-conv-meta").value.trim();
      Storage.save(settings, currentConvId);
      closeModal();
    });
  };
})();
