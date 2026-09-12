(function() {
    "use strict";

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
        md.style.cssText = "background:var(--m-bg,#fff);border-radius:12px;padding:20px;max-width:750px;width:94%;max-height:88vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.3);color:var(--m-text,#222);display:flex;flex-direction:column;";

        var tabsHeader = [
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">',
            '    <h2 style="margin:0;font-size:18px;color:var(--m-text,#222);">Конфигурация окружения</h2>',
            '    <button id="set-close-btn" style="border:none;background:none;font-size:24px;cursor:pointer;color:var(--m-muted,#666);">&times;</button>',
            '</div>',
            '<div style="display:flex;gap:6px;border-bottom:1px solid var(--m-border,#ddd);margin-bottom:14px;overflow-x:auto;">',
            '    <button class="set-tab-btn active" data-tab="tab-params" style="padding:8px 12px;border:none;background:none;border-bottom:2px solid var(--m-accent,#4a90d9);color:var(--m-text,#222);font-weight:600;cursor:pointer;white-space:nowrap;">Параметры LLM</button>',
            '    <button class="set-tab-btn" data-tab="tab-tools" style="padding:8px 12px;border:none;background:none;color:var(--m-muted,#666);cursor:pointer;white-space:nowrap;">Встроенные тулы</button>',
            '    <button class="set-tab-btn" data-tab="tab-mcp" style="padding:8px 12px;border:none;background:none;color:var(--m-muted,#666);cursor:pointer;white-space:nowrap;">Внешние MCP</button>',
            '    <button class="set-tab-btn" data-tab="tab-fs" style="padding:8px 12px;border:none;background:none;color:var(--m-muted,#666);cursor:pointer;white-space:nowrap;">📂 Файловый менеджер</button>',
            '</div>'
        ].join('');

        var tabParams = [
            '<div id="tab-params" class="set-tab-content">',
            UI.section("Генерация"),
            UI.gap2(
                '<div>' + UI.lbl('Temperature') + UI.inp('set-temp', 'number', settings.temperature, ' step="0.1" min="0" max="2"') + '</div>',
                '<div>' + UI.lbl('Top P') + UI.inp('set-topp', 'number', settings.top_p, ' step="0.05" min="0" max="1"') + '</div>'
            ),
            UI.gap2(
                '<div>' + UI.lbl('Max Output Tokens') + UI.inp('set-tokens', 'number', settings.max_output_tokens, ' step="100" min="1"') + '</div>',
                '<div>' + UI.lbl('Truncation') + UI.sel('set-trunc', '<option value="disabled"' + (settings.truncation === 'disabled' ? ' selected' : '') + '>Disabled</option><option value="auto"' + (settings.truncation === 'auto' ? ' selected' : '') + '>Auto</option>') + '</div>'
            ),
            UI.gap2(
                '<div>' + UI.lbl('Format') + UI.sel('set-text-fmt', '<option value="text"' + (settings.text_format === 'text' ? ' selected' : '') + '>Text</option><option value="json"' + (settings.text_format === 'json' ? ' selected' : '') + '>JSON Schema</option>') + '</div>',
                '<div>' + UI.lbl('Tool Choice') + UI.sel('set-tool-choice', '<option value="auto"' + (settings.tool_choice === 'auto' ? ' selected' : '') + '>Auto</option><option value="required"' + (settings.tool_choice === 'required' ? ' selected' : '') + '>Required</option><option value="none"' + (settings.tool_choice === 'none' ? ' selected' : '') + '>None</option>') + '</div>'
            ),
            '<div style="margin-top:10px;">' + UI.lbl('Системный промпт (Instructions)') + UI.ta('set-instructions', settings.instructions, 'height:70px;') + '</div>',
            '</div>'
        ].join('');

        var cfg = settings.tools_config || {};
        var ws = cfg.web_search || {};
        var fs = cfg.file_search || {};
        var ci = cfg.code_interpreter || {};

        var tabTools = [
            '<div id="tab-tools" class="set-tab-content" style="display:none;">',
            UI.section("Облачные тулы Yandex Studio"),
            '<div style="margin-bottom:10px;padding:8px;border:1px solid var(--m-border,#ddd);border-radius:6px;background:var(--m-card,#fff);">',
            UI.chk("set-ws-en", ws.enabled || false, "<strong>Web Search</strong> (поиск в интернете)") +
            '<div style="margin-top:6px;display:flex;gap:8px;">' +
                '<div style="flex:1;">' + UI.lbl("Разрешённые домены (через запятую)") + UI.inp("set-ws-allow", "text", ws.allowed_domains || "", ' placeholder="example.com, wiki.org"') + '</div>' +
            '</div></div>',
            '<div style="margin-bottom:10px;padding:8px;border:1px solid var(--m-border,#ddd);border-radius:6px;background:var(--m-card,#fff);">',
            UI.chk("set-ci-en", ci.enabled || false, "<strong>Code Interpreter</strong> (песочница выполнения)") +
            '</div>',
            '<div style="margin-bottom:10px;padding:8px;border:1px solid var(--m-border,#ddd);border-radius:6px;background:var(--m-card,#fff);">',
            UI.chk("set-fs-en", fs.enabled || false, "<strong>File Search</strong> (семантический поиск в Vector Store)") +
            '<div style="margin-top:6px;">' + UI.lbl("Vector Store IDs") + UI.inp("set-fs-vids", "text", fs.vector_store_ids || "", ' placeholder="vs_xxxx, vs_yyyy"') + '</div>' +
            '</div>',
            UI.section("Локальные драйверы Termux"),
            '<div style="font-size:12px;color:var(--m-muted,#666);margin-bottom:8px;">Локальные функции выполняются непосредственно в Termux:</div>',
            '<div style="display:flex;flex-direction:column;gap:6px;font-size:13px;">',
            '    <label><input type="checkbox" checked disabled> 📱 <strong>Termux Hardware API</strong> (батарея, буфер обмена, тосты, TTS, уведомления)</label>',
            '    <label><input type="checkbox" checked disabled> 🌿 <strong>Local Git</strong> (ветки, статус, diff, коммиты)</label>',
            '    <label><input type="checkbox" checked disabled> ⚙️ <strong>Filesystem Agent Tools</strong> (AST outline, безопасное чтение, бэкапы, diff-патчи)</label>',
            '</div>',
            '</div>'
        ].join('');

        var tabMcp = [
            '<div id="tab-mcp" class="set-tab-content" style="display:none;">',
            UI.section("Внешние MCP Серверы"),
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">',
            '    <span style="font-size:12px;color:var(--m-muted,#666);">Подключение внешних контекстных шлюзов:</span>',
            '    <button id="set-open-mcp-mgr" style="padding:6px 12px;background:var(--m-accent,#4a90d9);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:12px;">+ Менеджер серверов</button>',
            '</div>',
            '<div id="set-mcp-checkboxes" style="max-height:220px;overflow-y:auto;border:1px solid var(--m-border,#ddd);padding:8px;border-radius:6px;background:var(--m-input-bg,#f5f5f5);"></div>',
            '</div>'
        ].join('');

        var tabFs = [
            '<div id="tab-fs" class="set-tab-content" style="display:none;">',
            UI.section("Локальный файловый менеджер проекта"),
            '<div style="display:flex;gap:8px;margin-bottom:10px;align-items:center;">',
            '    <input id="set-fs-path" type="text" value="." style="flex:1;padding:6px 10px;border:1px solid var(--m-border,#ddd);border-radius:6px;background:var(--m-input-bg,#f5f5f5);color:var(--m-text,#222);font-size:13px;" placeholder="Путь к папке...">',
            '    <button id="set-fs-refresh-btn" style="padding:6px 14px;background:var(--m-accent,#4a90d9);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:12px;">Перейти</button>',
            '</div>',
            '<div id="set-fs-dir-list" style="max-height:240px;overflow-y:auto;border:1px solid var(--m-border,#ddd);border-radius:6px;padding:6px;background:var(--m-card,#fff);font-size:13px;">',
            '    <div style="color:var(--m-muted,#888);text-align:center;padding:12px;">Нажмите Перейти для просмотра файлов</div>',
            '</div>',
            '<div id="set-fs-file-viewer" style="margin-top:10px;display:none;">',
            '    <div style="font-weight:600;font-size:12px;margin-bottom:4px;" id="set-fs-file-title">Просмотр файла</div>',
            '    <textarea id="set-fs-file-content" style="width:100%;height:140px;font-family:monospace;font-size:11px;padding:8px;border:1px solid var(--m-border,#ddd);border-radius:6px;background:var(--m-input-bg,#f5f5f5);color:var(--m-text,#222);" readonly></textarea>',
            '</div>',
            '</div>'
        ].join('');

        var footer = [
            '<div style="display:flex;justify-content:flex-end;gap:10px;margin-top:20px;border-top:1px solid var(--m-border,#ddd);padding-top:14px;">',
            '    <button id="set-reset-btn" style="padding:8px 16px;background:var(--m-danger,#c33);color:#fff;border:none;border-radius:6px;cursor:pointer;">Сбросить</button>',
            '    <button id="set-save-btn" style="padding:8px 16px;background:var(--m-success,#28a745);color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600;">Сохранить</button>',
            '</div>'
        ].join('');

        md.innerHTML = tabsHeader + tabParams + tabTools + tabMcp + tabFs + footer;
        ov.appendChild(md);
        document.body.appendChild(ov);

        function closeModal() { ov.remove(); }
        document.getElementById('set-close-btn').addEventListener('click', closeModal);
        ov.addEventListener('click', function(e) { if (e.target === ov) closeModal(); });

        md.querySelectorAll('.set-tab-btn').forEach(function(btn) {
            btn.addEventListener('click', function() {
                md.querySelectorAll('.set-tab-btn').forEach(function(b) {
                    b.classList.remove('active');
                    b.style.fontWeight = 'normal';
                    b.style.borderBottom = 'none';
                    b.style.color = 'var(--m-muted,#666)';
                });
                md.querySelectorAll('.set-tab-content').forEach(function(c) { c.style.display = 'none'; });

                btn.classList.add('active');
                btn.style.fontWeight = '600';
                btn.style.borderBottom = '2px solid var(--m-accent,#4a90d9)';
                btn.style.color = 'var(--m-text,#222)';

                var target = document.getElementById(btn.getAttribute('data-tab'));
                if (target) {
                    target.style.display = 'block';
                    if (btn.getAttribute('data-tab') === 'tab-fs') {
                        loadDirectory('.');
                    }
                }
            });
        });

        function renderMcpCheckboxes() {
            var container = document.getElementById('set-mcp-checkboxes');
            if (!container) return;
            window.fetchMcpServers().then(function(servers) {
                if (!servers || servers.length === 0) {
                    container.innerHTML = '<div style="font-size:12px;color:var(--m-muted,#999);text-align:center;padding:8px;">Нет подключенных внешних серверов.</div>';
                    return;
                }
                var enabled = settings.enabled_mcp_servers || [];
                container.innerHTML = servers.map(function(srv) {
                    var isChecked = enabled.indexOf(srv.id) !== -1;
                    return '<label style="display:flex;align-items:center;gap:8px;font-size:13px;margin-bottom:6px;cursor:pointer;background:var(--m-card,#fff);padding:6px;border-radius:4px;border:1px solid var(--m-border,#ddd);">' +
                        '<input type="checkbox" class="set-mcp-chk" value="' + UI.escapeHtml(srv.id) + '" ' + (isChecked ? 'checked' : '') + '>' +
                        '<div><strong>' + UI.escapeHtml(srv.name) + '</strong> <span style="font-size:11px;color:var(--m-muted,#666);">[' + UI.escapeHtml(srv.server_label || srv.connector_id || 'mcp') + ']</span></div>' +
                        '</label>';
                }).join('');
            });
        }
        renderMcpCheckboxes();

        function loadDirectory(path) {
            var listEl = document.getElementById('set-fs-dir-list');
            listEl.innerHTML = '<div style="padding:10px;text-align:center;color:var(--m-muted,#888);">Чтение файлов...</div>';
            fetch('/api/files')
                .then(function(r) { return r.json(); })
                .then(function(d) {
                    var files = (d && d.data) || [];
                    if (files.length === 0) {
                        listEl.innerHTML = '<div style="padding:10px;text-align:center;color:var(--m-muted,#888);">Файлы отсутствуют.</div>';
                        return;
                    }
                    listEl.innerHTML = files.map(function(f) {
                        return '<div style="display:flex;justify-content:space-between;align-items:center;padding:6px;border-bottom:1px solid var(--m-section-border,#eee);">' +
                            '<span>📄 ' + UI.escapeHtml(f.filename || f.id) + ' (' + (f.bytes || 0) + ' b)</span>' +
                            '<button class="set-fs-view-file-btn" data-id="' + UI.escapeHtml(f.id) + '" data-name="' + UI.escapeHtml(f.filename) + '" style="padding:3px 8px;border:1px solid var(--m-border,#ddd);background:var(--m-card,#fff);border-radius:4px;cursor:pointer;font-size:11px;">Просмотр</button>' +
                            '</div>';
                    }).join('');

                    listEl.querySelectorAll('.set-fs-view-file-btn').forEach(function(b) {
                        b.addEventListener('click', function() {
                            var fid = this.getAttribute('data-id');
                            var fname = this.getAttribute('data-name');
                            fetch('/api/files/' + fid + '/content')
                                .then(function(res) { return res.text(); })
                                .then(function(txt) {
                                    document.getElementById('set-fs-file-viewer').style.display = 'block';
                                    document.getElementById('set-fs-file-title').textContent = 'Просмотр: ' + fname;
                                    document.getElementById('set-fs-file-content').value = txt.substring(0, 5000);
                                });
                        });
                    });
                })
                .catch(function(err) {
                    listEl.innerHTML = '<div style="padding:10px;color:var(--m-danger,#c33);">Ошибка: ' + err.message + '</div>';
                });
        }

        document.getElementById('set-fs-refresh-btn').addEventListener('click', function() {
            var p = document.getElementById('set-fs-path').value.trim() || '.';
            loadDirectory(p);
        });

        document.getElementById('set-open-mcp-mgr').addEventListener('click', function() {
            if (typeof window.openMcpManagerModal === 'function') {
                window.openMcpManagerModal(renderMcpCheckboxes);
            }
        });

        document.getElementById('set-reset-btn').addEventListener('click', function() {
            if (!confirm('Сбросить все параметры к заводским значениям?')) return;
            var defs = window.resetSettings(currentConvId);
            Storage.save(defs, currentConvId);
            closeModal();
        });

        document.getElementById('set-save-btn').addEventListener('click', function() {
            settings.temperature = parseFloat(document.getElementById('set-temp').value) || 0.7;
            settings.top_p = parseFloat(document.getElementById('set-topp').value) || 1.0;
            settings.max_output_tokens = parseInt(document.getElementById('set-tokens').value, 10) || 2000;
            settings.truncation = document.getElementById('set-trunc').value;
            settings.text_format = document.getElementById('set-text-fmt').value;
            settings.tool_choice = document.getElementById('set-tool-choice').value;
            settings.instructions = document.getElementById('set-instructions').value;

            if (!settings.tools_config) settings.tools_config = {};
            settings.tools_config.web_search = {
                enabled: document.getElementById('set-ws-en').checked,
                allowed_domains: document.getElementById('set-ws-allow').value.trim()
            };
            settings.tools_config.code_interpreter = {
                enabled: document.getElementById('set-ci-en').checked
            };
            settings.tools_config.file_search = {
                enabled: document.getElementById('set-fs-en').checked,
                vector_store_ids: document.getElementById('set-fs-vids').value.trim(),
                max_results: 20
            };

            var enabledMcp = [];
            document.querySelectorAll('.set-mcp-chk:checked').forEach(function(chk) {
                enabledMcp.push(chk.value);
            });
            settings.enabled_mcp_servers = enabledMcp;

            Storage.save(settings, currentConvId);
            closeModal();
        });
    };
})();
