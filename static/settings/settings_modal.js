(function() {
    "use strict";

    // === 1. Автономное модальное окно локальных инструментов (Tools) ===
    window.openToolsModal = function() {
        var UI = window.SettingsUI;
        var currentConvId = typeof window.currentConvId !== "undefined" ? window.currentConvId : null;

        var existing = document.getElementById("tools-modal-custom");
        if (existing) existing.remove();

        var ov = document.createElement("div");
        ov.id = "tools-modal-custom";
        ov.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:10001;display:flex;align-items:center;justify-content:center;";

        var md = document.createElement("div");
        md.style.cssText = "background:var(--m-bg,#fff);border-radius:12px;padding:20px;max-width:550px;width:92%;max-height:85vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.3);color:var(--m-text,#222);";

        var html = [
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">',
            '    <h2 style="margin:0;font-size:18px;color:var(--m-text,#222);">🧰 Локальные инструменты (Tools)</h2>',
            '    <button id="tools-close-btn" style="border:none;background:none;font-size:24px;cursor:pointer;color:var(--m-muted,#666);">&times;</button>',
            '</div>',
            '<div style="font-size:12px;color:var(--m-muted,#666);margin-bottom:12px;">Включайте или отключайте модули функций для текущего диалога:</div>',
            '<div id="tools-category-list" style="display:flex;flex-direction:column;gap:8px;">',
            '    <div style="padding:10px;text-align:center;color:var(--m-muted,#888);">Загрузка инструментов...</div>',
            '</div>',
            '<div style="display:flex;justify-content:flex-end;gap:10px;margin-top:16px;border-top:1px solid var(--m-border,#ddd);padding-top:12px;">',
            '    <button id="tools-save-btn" style="padding:8px 16px;background:var(--m-success,#28a745);color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600;">Сохранить активность</button>',
            '</div>'
        ].join('');

        md.innerHTML = html;
        ov.appendChild(md);
        document.body.appendChild(ov);

        function closeModal() { ov.remove(); }
        document.getElementById('tools-close-btn').addEventListener('click', closeModal);
        ov.addEventListener('click', function(e) { if (e.target === ov) closeModal(); });

        var categoryLabels = {
            "termux": "📱 Termux Hardware (батарея, буфер, датчики, тосты)",
            "git": "🌿 Local Git (статус, ветки, diff, коммиты)",
            "filesystem": "⚙️ Файловая система (чтение, запись, apply_patch)",
            "system": "🔧 Системные драйверы Termux",
            "wikipedia": "📚 Wikipedia Engine (поиск и сводка статей)",
            "profiler": "⏱️ Профайлер и инспекция состояния"
        };

        Promise.all([
            fetch('/api/tools/categories').then(r => r.json()),
            currentConvId ? fetch('/api/conversations/' + currentConvId + '/tools').then(r => r.json()) : Promise.resolve({active_tool_categories: []})
        ]).then(([catData, activeData]) => {
            var listEl = document.getElementById('tools-category-list');
            var categories = catData.categories || {};
            var active = activeData.active_tool_categories || Object.keys(categories);

            listEl.innerHTML = Object.keys(categories).map(function(cat) {
                var isChecked = active.indexOf(cat) !== -1;
                var label = categoryLabels[cat] || `Модуль: ${cat}`;
                var count = (categories[cat] || []).length;
                return '<label style="display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--m-border,#ddd);border-radius:6px;background:var(--m-card,#fff);cursor:pointer;">' +
                    '<input type="checkbox" class="tool-cat-chk" value="' + UI.escapeHtml(cat) + '" ' + (isChecked ? 'checked' : '') + '>' +
                    '<div style="flex:1;">' +
                    '   <div style="font-weight:600;font-size:13px;color:var(--m-text,#222);">' + UI.escapeHtml(label) + '</div>' +
                    '   <div style="font-size:11px;color:var(--m-muted,#666);">' + count + ' функций доступно</div>' +
                    '</div>' +
                    '</label>';
            }).join('');
        }).catch(err => {
            document.getElementById('tools-category-list').innerHTML = '<div style="color:red;padding:10px;">Ошибка загрузки категорий</div>';
        });

        document.getElementById('tools-save-btn').addEventListener('click', function() {
            var selected = [];
            document.querySelectorAll('.tool-cat-chk:checked').forEach(function(chk) {
                selected.push(chk.value);
            });
            if (currentConvId) {
                fetch('/api/conversations/' + currentConvId + '/tools', {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({active_tool_categories: selected})
                }).then(() => closeModal());
            } else {
                closeModal();
            }
        });
    };

    // === 2. Полное модальное окно параметров LLM (Со всеми полями Yandex API) ===
    window.openSettingsModal = function() {
        var UI = window.SettingsUI;
        var Storage = window.SettingsStorage;
        var currentConvId = typeof window.currentConvId !== "undefined" ? window.currentConvId : null;
        var settings = Storage.load(currentConvId);

        var existing = document.getElementById("settings-modal-custom");
        if (existing) existing.remove();

        var ov = document.createElement("div");
        ov.id = "settings-modal-custom";
        ov.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:10001;display:flex;align-items:center;justify-content:center;";

        var md = document.createElement("div");
        md.style.cssText = "background:var(--m-bg,#fff);border-radius:12px;padding:20px;max-width:720px;width:94%;max-height:90vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.3);color:var(--m-text,#222);display:flex;flex-direction:column;";

        var tabsHeader = [
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">',
            '    <h2 style="margin:0;font-size:18px;color:var(--m-text,#222);">⚙️ Конфигурация LLM & Responses API</h2>',
            '    <button id="set-close-btn" style="border:none;background:none;font-size:24px;cursor:pointer;color:var(--m-muted,#666);">&times;</button>',
            '</div>',
            '<div style="display:flex;gap:6px;border-bottom:1px solid var(--m-border,#ddd);margin-bottom:14px;overflow-x:auto;">',
            '    <button class="llm-tab-btn active" data-tab="tab-gen" style="padding:8px 12px;border:none;background:none;border-bottom:2px solid var(--m-accent,#4a90d9);color:var(--m-text,#222);font-weight:600;cursor:pointer;white-space:nowrap;">Сэмплинг</button>',
            '    <button class="llm-tab-btn" data-tab="tab-output" style="padding:8px 12px;border:none;background:none;color:var(--m-muted,#666);cursor:pointer;white-space:nowrap;">Формат вывода</button>',
            '    <button class="llm-tab-btn" data-tab="tab-routing" style="padding:8px 12px;border:none;background:none;color:var(--m-muted,#666);cursor:pointer;white-space:nowrap;">Маршрутизация вызовов</button>',
            '    <button class="llm-tab-btn" data-tab="tab-adv" style="padding:8px 12px;border:none;background:none;color:var(--m-muted,#666);cursor:pointer;white-space:nowrap;">Промпты & Кэш</button>',
            '</div>'
        ].join('');

        // 1. Сэмплинг и генерация
        var tabGen = [
            '<div id="tab-gen" class="llm-tab-content">',
            UI.section("Базовый сэмплинг"),
            UI.gap2(
                '<div>' + UI.lbl('Temperature') + UI.inp('set-temp', 'number', settings.temperature, ' step="0.1" min="0" max="2"') + '</div>',
                '<div>' + UI.lbl('Top P') + UI.inp('set-topp', 'number', settings.top_p, ' step="0.05" min="0" max="1"') + '</div>'
            ),
            UI.gap2(
                '<div>' + UI.lbl('Max Output Tokens') + UI.inp('set-tokens', 'number', settings.max_output_tokens, ' step="100" min="1"') + '</div>',
                '<div>' + UI.lbl('Truncation') + UI.sel('set-trunc', '<option value="disabled"' + (settings.truncation === 'disabled' ? ' selected' : '') + '>Disabled</option><option value="auto"' + (settings.truncation === 'auto' ? ' selected' : '') + '>Auto</option>') + '</div>'
            ),
            UI.gap3(
                '<div>' + UI.chk('set-store', settings.store !== false, 'Store Responses') + '</div>',
                '<div>' + UI.chk('set-bg', settings.background !== false, 'Background Polling') + '</div>',
                '<div>' + UI.chk('set-stream', settings.stream === true, 'Streaming Mode') + '</div>'
            ),
            '<div style="margin-top:10px;">' + UI.lbl('Системный промпт (Instructions)') + UI.ta('set-instructions', settings.instructions, 'height:70px;') + '</div>',
            '</div>'
        ].join('');

        // 2. Формат и структура вывода
        var tabOutput = [
            '<div id="tab-output" class="llm-tab-content" style="display:none;">',
            UI.section("Структура ответа (Text / JSON Schema)"),
            UI.gap2(
                '<div>' + UI.lbl('Формат') + UI.sel('set-text-fmt', '<option value="text"' + (settings.text_format === 'text' ? ' selected' : '') + '>Обычный текст (Text)</option><option value="json"' + (settings.text_format === 'json' ? ' selected' : '') + '>Строгий JSON (json_schema)</option>') + '</div>',
                '<div>' + UI.lbl('Детализация (Verbosity)') + UI.sel('set-verbosity', '<option value="medium"' + (settings.text_verbosity === 'medium' ? ' selected' : '') + '>Medium</option><option value="concise"' + (settings.text_verbosity === 'concise' ? ' selected' : '') + '>Concise</option><option value="verbose"' + (settings.text_verbosity === 'verbose' ? ' selected' : '') + '>Verbose</option>') + '</div>'
            ),
            '<div id="json-schema-wrap" style="' + (settings.text_format === 'json' ? '' : 'display:none;') + 'margin-top:10px;">',
                UI.lbl('Название схемы') + UI.inp('set-js-name', 'text', settings.json_schema_name || 'response', ' style="margin-bottom:8px;"'),
                UI.lbl('JSON Schema definition') + UI.ta('set-js-schema', settings.json_schema || '', 'height:100px;font-family:monospace;font-size:11px;'),
            '</div>',
            '</div>'
        ].join('');

        // 3. Вызовы тулов и сервисный уровень
        var tabRouting = [
            '<div id="tab-routing" class="llm-tab-content" style="display:none;">',
            UI.section("Политики Tool Calling"),
            UI.gap2(
                '<div>' + UI.lbl('Tool Choice') + UI.sel('set-tool-choice', '<option value="auto"' + (settings.tool_choice === 'auto' ? ' selected' : '') + '>Auto</option><option value="required"' + (settings.tool_choice === 'required' ? ' selected' : '') + '>Required</option><option value="none"' + (settings.tool_choice === 'none' ? ' selected' : '') + '>None</option>') + '</div>',
                '<div>' + UI.lbl('Max Tool Calls') + UI.inp('set-max-tools', 'number', settings.max_tool_calls || 10, ' min="1" max="50"') + '</div>'
            ),
            UI.gap2(
                '<div>' + UI.chk('set-parallel-tools', settings.parallel_tool_calls !== false, 'Параллельные вызовы (Parallel Tool Calls)') + '</div>',
                '<div>' + UI.lbl('Top Logprobs') + UI.inp('set-logprobs', 'number', settings.top_logprobs || 0, ' min="0" max="5"') + '</div>'
            ),
            UI.section("Приоритеты облака"),
            UI.gap2(
                '<div>' + UI.lbl('Service Tier') + UI.sel('set-tier', '<option value="auto"' + (settings.service_tier === 'auto' ? ' selected' : '') + '>Auto (Default)</option><option value="priority"' + (settings.service_tier === 'priority' ? ' selected' : '') + '>Priority</option>') + '</div>',
                '<div>' + UI.lbl('Previous Response ID') + UI.inp('set-prev-id', 'text', settings.previous_response_id || '', ' placeholder="UUID"') + '</div>'
            ),
            '</div>'
        ].join('');

        // 4. Шаблоны и метаданные
        var tabAdv = [
            '<div id="tab-adv" class="llm-tab-content" style="display:none;">',
            UI.section("Промпт-шаблоны и кэширование"),
            UI.gap2(
                '<div>' + UI.lbl('Prompt Cache Key') + UI.inp('set-cache-key', 'text', settings.prompt_cache_key || '', ' placeholder="cache-id"') + '</div>',
                '<div>' + UI.lbl('Prompt ID (Шаблон)') + UI.inp('set-prompt-id', 'text', settings.prompt_id || '', ' placeholder="Шаблон Yandex Studio"') + '</div>'
            ),
            UI.gap2(
                '<div>' + UI.lbl('Prompt Version') + UI.inp('set-prompt-ver', 'text', settings.prompt_version || '', ' placeholder="latest"') + '</div>',
                '<div>' + UI.lbl('Prompt Variables (JSON)') + UI.inp('set-prompt-vars', 'text', settings.prompt_variables || '', ' placeholder=\'{"key":"val"}\'') + '</div>'
            ),
            '<div style="margin-top:10px;">' + UI.lbl('Диалоговые метаданные (Metadata JSON)') + UI.ta('set-conv-meta', settings.conv_metadata || '', 'height:60px;font-family:monospace;font-size:11px;') + '</div>',
            '</div>'
        ].join('');

        var footer = [
            '<div style="display:flex;justify-content:flex-end;gap:10px;margin-top:20px;border-top:1px solid var(--m-border,#ddd);padding-top:14px;">',
            '    <button id="set-reset-btn" style="padding:8px 16px;background:var(--m-danger,#c33);color:#fff;border:none;border-radius:6px;cursor:pointer;">Сброс</button>',
            '    <button id="set-save-btn" style="padding:8px 16px;background:var(--m-success,#28a745);color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600;">Сохранить параметры</button>',
            '</div>'
        ].join('');

        md.innerHTML = tabsHeader + tabGen + tabOutput + tabRouting + tabAdv + footer;
        ov.appendChild(md);
        document.body.appendChild(ov);

        function closeModal() { ov.remove(); }
        document.getElementById('set-close-btn').addEventListener('click', closeModal);
        ov.addEventListener('click', function(e) { if (e.target === ov) closeModal(); });

        // Переключение табов
        md.querySelectorAll('.llm-tab-btn').forEach(function(btn) {
            btn.addEventListener('click', function() {
                md.querySelectorAll('.llm-tab-btn').forEach(function(b) {
                    b.classList.remove('active');
                    b.style.fontWeight = 'normal';
                    b.style.borderBottom = 'none';
                    b.style.color = 'var(--m-muted,#666)';
                });
                md.querySelectorAll('.llm-tab-content').forEach(function(c) { c.style.display = 'none'; });

                btn.classList.add('active');
                btn.style.fontWeight = '600';
                btn.style.borderBottom = '2px solid var(--m-accent,#4a90d9)';
                btn.style.color = 'var(--m-text,#222)';

                var target = document.getElementById(btn.getAttribute('data-tab'));
                if (target) target.style.display = 'block';
            });
        });

        // Показ поля JSON Schema
        document.getElementById('set-text-fmt').addEventListener('change', function() {
            document.getElementById('json-schema-wrap').style.display = this.value === 'json' ? 'block' : 'none';
        });

        document.getElementById('set-reset-btn').addEventListener('click', function() {
            if (!confirm('Сбросить параметры генерации к значениям по умолчанию?')) return;
            var defs = window.resetSettings(currentConvId);
            Storage.save(defs, currentConvId);
            closeModal();
        });

        document.getElementById('set-save-btn').addEventListener('click', function() {
            settings.temperature = parseFloat(document.getElementById('set-temp').value) || 0.7;
            settings.top_p = parseFloat(document.getElementById('set-topp').value) || 1.0;
            settings.max_output_tokens = parseInt(document.getElementById('set-tokens').value, 10) || 2000;
            settings.truncation = document.getElementById('set-trunc').value;
            settings.store = document.getElementById('set-store').checked;
            settings.background = document.getElementById('set-bg').checked;
            settings.stream = document.getElementById('set-stream').checked;
            settings.instructions = document.getElementById('set-instructions').value;

            settings.text_format = document.getElementById('set-text-fmt').value;
            settings.text_verbosity = document.getElementById('set-verbosity').value;
            settings.json_schema_name = document.getElementById('set-js-name').value.trim();
            settings.json_schema = document.getElementById('set-js-schema').value.trim();

            settings.tool_choice = document.getElementById('set-tool-choice').value;
            settings.max_tool_calls = parseInt(document.getElementById('set-max-tools').value, 10) || 10;
            settings.parallel_tool_calls = document.getElementById('set-parallel-tools').checked;
            settings.top_logprobs = parseInt(document.getElementById('set-logprobs').value, 10) || 0;

            settings.service_tier = document.getElementById('set-tier').value;
            settings.previous_response_id = document.getElementById('set-prev-id').value.trim();

            settings.prompt_cache_key = document.getElementById('set-cache-key').value.trim();
            settings.prompt_id = document.getElementById('set-prompt-id').value.trim();
            settings.prompt_version = document.getElementById('set-prompt-ver').value.trim();
            settings.prompt_variables = document.getElementById('set-prompt-vars').value.trim();
            settings.conv_metadata = document.getElementById('set-conv-meta').value.trim();

            Storage.save(settings, currentConvId);
            closeModal();
        });
    };
})();
