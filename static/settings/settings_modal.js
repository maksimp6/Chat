(function() {
    "use strict";

    function safeAddListener(id, evt, fn) {
        var el = document.getElementById(id);
        if (el) el.addEventListener(evt, fn);
        else console.warn("[settings_modal] Element not found: #" + id);
    }

    window.openSettingsModal = function() {
        var UI = window.SettingsUI;
        var ex = document.getElementById("settings-modal");
        if (ex) ex.remove();

        UI.injectModalStyles();

        var s = window.getSettings();
        var cfg = s.tools_config || {};
        var dirty = false;

        // Рабочая копия MCP-настроек — будет обновляться интерактивно
        var mcpEnabled = (s.enabled_mcp_servers || []).slice();
        var mcpApproval = {};
        if (s.mcp_approval) {
            for (var k in s.mcp_approval) { mcpApproval[k] = s.mcp_approval[k]; }
        }

        var ov = document.createElement("div");
        ov.id = "settings-modal";
        ov.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:var(--m-overlay,rgba(0,0,0,0.6));z-index:9999;display:flex;align-items:center;justify-content:center;";

        var md = document.createElement("div");
        md.style.cssText = "background:var(--m-bg,#fff);border-radius:12px;padding:24px;max-width:700px;width:90%;max-height:90vh;overflow-y:auto;box-shadow:0 8px 32px var(--m-shadow,rgba(0,0,0,0.3));color:var(--m-text,#222);";

        // === Заголовок ===
        var h = '<div style="display:flex;justify-content:space-between;margin-bottom:16px;">';
        h += '<h2 style="margin:0;color:var(--m-text,#222);">Настройки Агента</h2>';
        h += '<button id="btn-close-modal" style="border:none;background:none;font-size:24px;cursor:pointer;color:var(--m-muted,#666);">&times;</button></div>';

        // === Имя диалога ===
        if (typeof currentConvId !== 'undefined' && currentConvId) {
            var ct = "";
            if (typeof conversations !== 'undefined') {
                var cv = conversations.find(function(c) { return c.id === currentConvId; });
                if (cv) ct = cv.title || "";
            }
            h += '<div style="margin-bottom:20px;padding-bottom:15px;border-bottom:1px solid var(--m-section-border,#eee);">';
            h += UI.lbl('Имя диалога') + UI.inp('set-conv-title', 'text', ct, ' class="track-change"');
            h += '</div>';
        }

        // === Базовые параметры ===
        h += UI.section('Базовые параметры');
        h += UI.gap2(
            '<div>' + UI.lbl('Temperature', '0 — точно, 2 — хаотично') + UI.inp('set-temp', 'number', s.temperature, ' step="0.1" min="0" max="2" class="track-change"') + '</div>',
            '<div>' + UI.lbl('Top P', 'Ядерная выборка') + UI.inp('set-top-p', 'number', s.top_p, ' step="0.05" min="0" max="1" class="track-change"') + '</div>'
        );
        h += UI.gap3(
            '<div>' + UI.lbl('Max Tokens', 'Лимит токенов ответа') + UI.inp('set-tokens', 'number', s.max_output_tokens, ' class="track-change"') + '</div>',
            '<div>' + UI.lbl('Max Tool Calls', '10 = по умолчанию') + UI.inp('set-max-tool-calls', 'number', s.max_tool_calls, ' class="track-change"') + '</div>',
            '<div>' + UI.lbl('Service Tier') + UI.sel('set-service-tier',
                '<option value="auto"' + (s.service_tier == 'auto' ? ' selected' : '') + '>Auto</option>' +
                '<option value="priority"' + (s.service_tier == 'priority' ? ' selected' : '') + '>Priority</option>' +
                '<option value="default"' + (s.service_tier == 'default' ? ' selected' : '') + '>Default</option>' +
                '<option value="flex"' + (s.service_tier == 'flex' ? ' selected' : '') + '>Flex</option>' +
                '<option value="scale"' + (s.service_tier == 'scale' ? ' selected' : '') + '>Scale</option>',
                ' class="track-change"') + '</div>'
        );
        h += '<div style="margin-bottom:10px;">' + UI.lbl('System Prompt', 'Роль и поведение модели') + UI.ta('set-instr', s.instructions, 'min-height:60px;') + '</div>';

        // === Prompt Templates ===
        h += UI.section('Prompt Templates');
        h += UI.gap2(
            '<div>' + UI.lbl('Prompt ID', 'ID из Agent Atelier') + UI.inp('set-prompt-id', 'text', s.prompt_id, ' class="track-change" placeholder="(опционально)"') + '</div>',
            '<div>' + UI.lbl('Version', 'Пусто = latest') + UI.inp('set-prompt-ver', 'text', s.prompt_version, ' class="track-change" placeholder="(пусто = latest)"') + '</div>'
        );
        h += '<div>' + UI.lbl('Variables (JSON)') + UI.ta('set-prompt-vars', s.prompt_variables, 'min-height:50px;font-family:monospace;font-size:12px;') + '</div>';

        // === Формат и логика ===
        h += UI.section('Формат и логика');
        h += UI.gap2(
            '<div>' + UI.lbl('Format') + UI.sel('set-text-format',
                '<option value="text"' + (s.text_format == 'text' ? ' selected' : '') + '>Text</option>' +
                '<option value="json"' + (s.text_format == 'json' ? ' selected' : '') + '>JSON Schema</option>',
                ' class="track-change"') + '</div>',
            '<div>' + UI.lbl('Verbosity') + UI.sel('set-verbosity',
                '<option value="low"' + (s.text_verbosity == 'low' ? ' selected' : '') + '>Low</option>' +
                '<option value="medium"' + (s.text_verbosity == 'medium' ? ' selected' : '') + '>Medium</option>' +
                '<option value="high"' + (s.text_verbosity == 'high' ? ' selected' : '') + '>High</option>',
                ' class="track-change"') + '</div>'
        );
        h += UI.gap2(
            '<div>' + UI.lbl('Reasoning', 'Глубина рассуждений') + UI.sel('set-reasoning',
                '<option value="none"' + (s.reasoning_effort == 'none' ? ' selected' : '') + '>None</option>' +
                '<option value="minimal"' + (s.reasoning_effort == 'minimal' ? ' selected' : '') + '>Minimal</option>' +
                '<option value="low"' + (s.reasoning_effort == 'low' ? ' selected' : '') + '>Low</option>' +
                '<option value="medium"' + (s.reasoning_effort == 'medium' ? ' selected' : '') + '>Medium</option>' +
                '<option value="high"' + (s.reasoning_effort == 'high' ? ' selected' : '') + '>High</option>',
                ' class="track-change"') + '</div>',
            '<div>' + UI.lbl('Tool Choice') + UI.sel('set-tool-choice',
                '<option value="auto"' + (s.tool_choice == 'auto' ? ' selected' : '') + '>Auto</option>' +
                '<option value="required"' + (s.tool_choice == 'required' ? ' selected' : '') + '>Required</option>' +
                '<option value="none"' + (s.tool_choice == 'none' ? ' selected' : '') + '>None</option>',
                ' class="track-change"') + '</div>'
        );
        h += '<div id="json-schema-ui" style="' + (s.text_format == 'json' ? '' : 'display:none;') + 'margin-bottom:10px;">';
        h += UI.lbl('Schema Name') + UI.inp('set-schema-name', 'text', s.json_schema_name, ' class="track-change"');
        h += '<div style="margin-top:6px;">' + UI.lbl('JSON Schema') + UI.ta('set-json-schema', s.json_schema, 'min-height:100px;font-family:monospace;font-size:12px;') + '</div></div>';

        // === Флаги ===
        h += UI.section('Флаги выполнения');
        h += '<div style="display:flex;flex-wrap:wrap;gap:15px;margin-top:10px;">';
        h += UI.chk('set-stream', s.stream, 'Stream', ' class="track-change"', 'SSE поток');
        h += UI.chk('set-parallel', s.parallel_tool_calls, 'Parallel Tools', ' class="track-change"', 'Параллельные вызовы');
        h += UI.chk('set-store', s.store, 'Store', ' class="track-change"', 'Сохранять ответ');
        h += '</div>';

        // === Инструменты ===
        h += UI.section('Инструменты (Tools)');

        // Web Search
        h += UI.toolSection('Web Search', 'menu-websearch',
            UI.chk('chk-websearch', cfg.web_search && cfg.web_search.enabled, 'Включить поиск', ' class="track-change"') +
            '<div style="margin-top:8px;">' + UI.lbl('Context Size') + UI.sel('sel-ws-context',
                '<option value="low"' + ((cfg.web_search && cfg.web_search.context_size == 'low') ? ' selected' : '') + '>Low</option>' +
                '<option value="medium"' + ((cfg.web_search && cfg.web_search.context_size == 'medium') ? ' selected' : '') + '>Medium</option>' +
                '<option value="high"' + ((cfg.web_search && cfg.web_search.context_size == 'high') ? ' selected' : '') + '>High</option>',
                ' class="track-change"') + '</div>' +
            '<div style="margin-top:8px;">' + UI.lbl('Allowed Domains', 'через запятую') + UI.inp('inp-ws-allowed', 'text', cfg.web_search ? cfg.web_search.allowed_domains : '', ' class="track-change"') + '</div>' +
            '<div style="margin-top:8px;">' + UI.lbl('Blocked Domains', 'через запятую') + UI.inp('inp-ws-blocked', 'text', cfg.web_search ? cfg.web_search.blocked_domains : '', ' class="track-change"') + '</div>'
        );

        // Code Interpreter
        h += UI.toolSection('Code Interpreter', 'menu-code',
            UI.chk('chk-code', cfg.code_interpreter && cfg.code_interpreter.enabled, 'Включить выполнение кода', ' class="track-change"')
        );

        // File Search
        h += UI.toolSection('File Search (RAG)', 'menu-filesearch',
            UI.chk('chk-filesearch', cfg.file_search && cfg.file_search.enabled, 'Включить поиск по файлам', ' class="track-change"') +
            '<div style="margin-top:8px;">' + UI.lbl('Vector Store IDs') +
            '<button id="btn-load-vs" type="button" style="padding:5px 12px;background:var(--m-accent,#4a90d9);color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px;margin-bottom:4px;">Загрузить хранилища</button>' +
            '<div id="vs-list-container" style="margin-top:4px;max-height:150px;overflow-y:auto;border:1px solid var(--m-border,#ddd);border-radius:4px;padding:4px;background:var(--m-input-bg,#f5f5f5);"></div>' +
            '<input type="hidden" id="inp-fs-ids" value="' + UI.escapeHtml(cfg.file_search ? cfg.file_search.vector_store_ids : '') + '" class="track-change">' +
            '</div>' +
            '<div style="margin-top:8px;">' + UI.lbl('Max Results') + UI.inp('inp-fs-max', 'number', cfg.file_search ? cfg.file_search.max_results : 20, ' class="track-change"') + '</div>'
        );

        // MCP — с интерактивными чекбоксами и approval
        h += UI.toolSection('MCP Серверы', 'menu-mcp',
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">' +
            '<span style="font-size:13px;color:var(--m-muted,#666);">Включите нужные серверы и настройте approval</span>' +
            '<button id="btn-open-mcp-mgr" type="button" style="padding:5px 12px;background:var(--m-accent,#4a90d9);color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px;">Менеджер серверов</button>' +
            '</div>' +
            '<div id="mcp-enabled-list" style="margin-top:6px;"></div>'
        );

        // === Дополнительно ===
        h += UI.section('Дополнительно');
        h += UI.gap2(
            '<div>' + UI.lbl('Prompt Cache Key') + UI.inp('set-cache-key', 'text', s.prompt_cache_key, ' class="track-change"') + '</div>',
            '<div>' + UI.lbl('Previous Response ID') + UI.inp('set-prev-id', 'text', s.previous_response_id, ' class="track-change"') + '</div>'
        );
        h += UI.gap2(
            '<div>' + UI.lbl('Top Logprobs', '0 = выкл') + UI.inp('set-logprobs', 'number', s.top_logprobs, ' class="track-change"') + '</div>',
            '<div>' + UI.lbl('Truncation') + UI.sel('set-truncation',
                '<option value="disabled"' + (s.truncation == 'disabled' ? ' selected' : '') + '>Disabled</option>' +
                '<option value="auto"' + (s.truncation == 'auto' ? ' selected' : '') + '>Auto</option>',
                ' class="track-change"') + '</div>'
        );
        h += '<div style="margin-bottom:10px;">' + UI.lbl('Conversation Metadata (JSON)') + UI.ta('set-conv-meta', s.conv_metadata, 'min-height:50px;font-family:monospace;font-size:12px;') + '</div>';

        // === Голос ===
        h += UI.section('Голос (Realtime API)');
        h += UI.gap2(
            '<div>' + UI.lbl('Voice') + UI.sel('set-voice',
                '<option value="kirill"' + (s.voice == 'kirill' ? ' selected' : '') + '>Кирилл</option>' +
                '<option value="alloy"' + (s.voice == 'alloy' ? ' selected' : '') + '>Alloy</option>' +
                '<option value="echo"' + (s.voice == 'echo' ? ' selected' : '') + '>Echo</option>' +
                '<option value="shimmer"' + (s.voice == 'shimmer' ? ' selected' : '') + '>Shimmer</option>',
                ' class="track-change"') + '</div>',
            '<div>' + UI.lbl('Response Mode') + UI.sel('set-resp-mode',
                '<option value="audio"' + (s.response_mode == 'audio' ? ' selected' : '') + '>Audio</option>' +
                '<option value="text"' + (s.response_mode == 'text' ? ' selected' : '') + '>Text</option>',
                ' class="track-change"') + '</div>'
        );
        h += '<div style="margin-bottom:10px;">' + UI.lbl('Voice Instructions') + UI.ta('set-voice-instr', s.voice_instructions, 'min-height:50px;') + '</div>';
        h += UI.gap3(
            '<div>' + UI.lbl('VAD Threshold') + UI.inp('set-vad-thr', 'number', s.vad_threshold, ' step="0.1" min="0" max="1" class="track-change"') + '</div>',
            '<div>' + UI.lbl('VAD Silence (ms)') + UI.inp('set-vad-silence', 'number', s.vad_silence_duration_ms, ' class="track-change"') + '</div>',
            '<div>' + UI.lbl('VAD Padding (ms)') + UI.inp('set-vad-padding', 'number', s.vad_prefix_padding_ms, ' class="track-change"') + '</div>'
        );

        // === Кнопки ===
        h += '<div style="display:flex;gap:10px;margin-top:20px;padding-top:15px;border-top:1px solid var(--m-section-border,#eee);">';
        h += UI.btn('btn-save-settings', 'Сохранить', 'accent', ' style="flex:1;"');
        h += UI.btn('btn-reset-settings', 'Сбросить', 'danger');
        h += '</div>';

        md.innerHTML = h;
        ov.appendChild(md);
        document.body.appendChild(ov);

        // === Закрытие ===
        function closeModal() { ov.remove(); }
        safeAddListener('btn-close-modal', 'click', closeModal);
        ov.addEventListener('click', function(e) { if (e.target === ov) closeModal(); });

        // === JSON Schema show/hide ===
        safeAddListener('set-text-format', 'change', function() {
            var ju = document.getElementById('json-schema-ui');
            if (ju) ju.style.display = this.value === 'json' ? '' : 'none';
        });

        // === Vector Stores ===
        safeAddListener('btn-load-vs', 'click', function() {
            var cont = document.getElementById('vs-list-container');
            if (cont) cont.innerHTML = '<div style="padding:10px;color:var(--m-muted,#999);">Загрузка...</div>';
            window.fetchVectorStores().then(function(list) { window.renderVsList(list); });
        });

        // === MCP менеджер ===
        safeAddListener('btn-open-mcp-mgr', 'click', function() {
            window.openMcpManagerModal(function() { renderMcpEnabledList(); });
        });

        // === Интерактивный рендер MCP-серверов с чекбоксами и approval ===
        function renderMcpEnabledList() {
            var cont = document.getElementById('mcp-enabled-list');
            if (!cont) return;
            var cache = window.getMcpServersCache();
            if (cache.length === 0) {
                cont.innerHTML = '<div style="font-size:12px;color:var(--m-muted,#999);padding:8px;">Нет доступных MCP серверов. Создайте их в менеджере.</div>';
                return;
            }

            cont.innerHTML = cache.map(function(srv) {
                var isEnabled = mcpEnabled.indexOf(srv.id) !== -1;
                var approvalVal = (srv.id in mcpApproval) ? mcpApproval[srv.id] : null;
                var connInfo = srv.connector_id ? srv.connector_id : (srv.server_url || '(нет URL)');

                var row = '<div style="display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--m-section-border,#eee);font-size:12px;">';

                // Чекбокс включения
                row += '<input type="checkbox" class="mcp-enable-chk" data-id="' + UI.escapeHtml(srv.id) + '"' + (isEnabled ? ' checked' : '') + ' style="cursor:pointer;">';

                // Название и URL
                row += '<div style="flex:1;min-width:0;">';
                row += '<div style="font-weight:bold;color:var(--m-text,#222);">' + UI.escapeHtml(srv.name) + '</div>';
                row += '<div style="font-size:11px;color:var(--m-muted,#666);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + UI.escapeHtml(connInfo) + '</div>';
                row += '</div>';

                // Approval dropdown — показываем только если включён
                row += '<select class="mcp-approval-sel" data-id="' + UI.escapeHtml(srv.id) + '"' + (isEnabled ? '' : ' disabled') + ' style="font-size:11px;padding:3px 6px;border:1px solid var(--m-border,#ddd);border-radius:4px;background:var(--m-input-bg,#f5f5f5);color:var(--m-text,#222);' + (isEnabled ? '' : 'opacity:0.5;') + '">';
                row += '<option value=""' + (approvalVal === null ? ' selected' : '') + '>По умолчанию</option>';
                row += '<option value="true"' + (approvalVal === true ? ' selected' : '') + '>✅ Approval</option>';
                row += '<option value="false"' + (approvalVal === false ? ' selected' : '') + '>⛔ No approval</option>';
                row += '</select>';

                row += '</div>';
                return row;
            }).join('');

            // Обработчики чекбоксов включения
            cont.querySelectorAll('.mcp-enable-chk').forEach(function(chk) {
                chk.addEventListener('change', function() {
                    var id = this.getAttribute('data-id');
                    if (this.checked) {
                        if (mcpEnabled.indexOf(id) === -1) mcpEnabled.push(id);
                        // Включаем select
                        var sel = cont.querySelector('.mcp-approval-sel[data-id="' + id + '"]');
                        if (sel) { sel.disabled = false; sel.style.opacity = '1'; }
                    } else {
                        mcpEnabled = mcpEnabled.filter(function(x) { return x !== id; });
                        delete mcpApproval[id];
                        var sel = cont.querySelector('.mcp-approval-sel[data-id="' + id + '"]');
                        if (sel) { sel.disabled = true; sel.style.opacity = '0.5'; sel.value = ''; }
                    }
                });
            });

            // Обработчики approval
            cont.querySelectorAll('.mcp-approval-sel').forEach(function(sel) {
                sel.addEventListener('change', function() {
                    var id = this.getAttribute('data-id');
                    var v = this.value;
                    if (v === '') { delete mcpApproval[id]; }
                    else { mcpApproval[id] = (v === 'true'); }
                });
            });
        }

        // Загружаем MCP и рендерим
        window.fetchMcpServers().then(function() { renderMcpEnabledList(); });

        // === Сборка настроек ===
        function collectSettings() {
            function val(id, fallback) {
                var el = document.getElementById(id);
                return el ? el.value : fallback;
            }
            function chk(id, fallback) {
                var el = document.getElementById(id);
                return el ? el.checked : fallback;
            }
            function num(id, fallback) {
                var el = document.getElementById(id);
                return el ? parseFloat(el.value) : fallback;
            }
            function int(id, fallback) {
                var el = document.getElementById(id);
                return el ? parseInt(el.value, 10) : fallback;
            }

            var ns = {
                temperature: num('set-temp', 0.7),
                top_p: num('set-top-p', 1.0),
                max_output_tokens: int('set-tokens', 2000),
                max_tool_calls: int('set-max-tool-calls', 10),
                service_tier: val('set-service-tier', 'auto'),
                instructions: val('set-instr', ''),
                prompt_id: val('set-prompt-id', ''),
                prompt_version: val('set-prompt-ver', ''),
                prompt_variables: val('set-prompt-vars', ''),
                text_format: val('set-text-format', 'text'),
                text_verbosity: val('set-verbosity', 'medium'),
                reasoning_effort: val('set-reasoning', 'none'),
                tool_choice: val('set-tool-choice', 'auto'),
                json_schema_name: val('set-schema-name', 'response'),
                json_schema: val('set-json-schema', ''),
                stream: chk('set-stream', false),
                parallel_tool_calls: chk('set-parallel', true),
                store: chk('set-store', true),
                prompt_cache_key: val('set-cache-key', ''),
                previous_response_id: val('set-prev-id', ''),
                top_logprobs: int('set-logprobs', 0),
                truncation: val('set-truncation', 'disabled'),
                conv_metadata: val('set-conv-meta', ''),
                voice: val('set-voice', 'kirill'),
                response_mode: val('set-resp-mode', 'audio'),
                voice_instructions: val('set-voice-instr', ''),
                vad_threshold: num('set-vad-thr', 0.3),
                vad_silence_duration_ms: int('set-vad-silence', 500),
                vad_prefix_padding_ms: int('set-vad-padding', 300),
                background: true,
                selected_skills: s.selected_skills || [],
                enabled_mcp_servers: mcpEnabled,
                mcp_approval: mcpApproval,
                tools_config: {
                    web_search: {
                        enabled: chk('chk-websearch', false),
                        context_size: val('sel-ws-context', 'medium'),
                        allowed_domains: val('inp-ws-allowed', ''),
                        blocked_domains: val('inp-ws-blocked', '')
                    },
                    code_interpreter: { enabled: chk('chk-code', false) },
                    file_search: {
                        enabled: chk('chk-filesearch', false),
                        vector_store_ids: val('inp-fs-ids', ''),
                        max_results: int('inp-fs-max', 20)
                    }
                }
            };

            // Имя диалога
            var titleEl = document.getElementById('set-conv-title');
            if (titleEl && typeof currentConvId !== 'undefined' && currentConvId) {
                if (typeof conversations !== 'undefined') {
                    var cv = conversations.find(function(c) { return c.id === currentConvId; });
                    if (cv) cv.title = titleEl.value;
                    if (typeof renderConvList === 'function') renderConvList();
                }
            }

            return ns;
        }

        safeAddListener('btn-save-settings', 'click', function() {
            var ns = collectSettings();
            window.saveSettings(ns);
            ov.remove();
            if (typeof showNotification === 'function') showNotification("Настройки сохранены");
        });

        safeAddListener('btn-reset-settings', 'click', function() {
            if (!confirm("Сбросить настройки к значениям по умолчанию?")) return;
            window.resetSettings();
            ov.remove();
            setTimeout(function() { window.openSettingsModal(); }, 50);
        });
    };
})();
