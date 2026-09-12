(function() {
    "use strict";

    var mcpServersCache = [];
    var mcpFetchPromise = null;

    window.fetchMcpServers = function() {
        if (mcpFetchPromise) return mcpFetchPromise;
        mcpFetchPromise = fetch('/api/mcp-servers')
            .then(function(r) {
                if (!r.ok) { console.warn("[MCP] Server returned " + r.status); return { data: [] }; }
                return r.json();
            })
            .then(function(data) {
                mcpServersCache = (data && data.data) || [];
                mcpFetchPromise = null;
                return mcpServersCache;
            })
            .catch(function(e) {
                console.warn("[MCP] Fetch failed:", e.message || e);
                mcpServersCache = [];
                mcpFetchPromise = null;
                return [];
            });
        return mcpFetchPromise;
    };

    window.getMcpServersCache = function() { return mcpServersCache; };

    function buildMcpTool(srv, approvalOverride) {
        var UI = window.SettingsUI;
        var mcp = { type: "mcp", server_label: srv.server_label || srv.name || "mcp_server" };

        if (srv.connector_id) { mcp.connector_id = srv.connector_id; }
        else if (srv.server_url) { mcp.server_url = srv.server_url; }

        if (srv.server_description) mcp.server_description = srv.server_description;
        if (srv.authorization) mcp.authorization = srv.authorization;

        if (srv.headers) { try { mcp.headers = JSON.parse(srv.headers); } catch(e) {} }
        if (srv.defer_loading) mcp.defer_loading = true;

        if (srv.allowed_tools) {
            mcp.allowed_tools = UI.strToArr(srv.allowed_tools);
        }

        if (approvalOverride !== undefined && approvalOverride !== null) {
            mcp.require_approval = approvalOverride ? "always" : "never";
        } else if (srv.require_approval === "filter") {
            var ra = {};
            var ftAlways = UI.strToArr(srv.require_approval_tools);
            if (ftAlways.length > 0) {
                var af = { tool_names: ftAlways };
                if (srv.require_approval_read_only) af.read_only = true;
                ra.always = af;
            }
            var ftNever = UI.strToArr(srv.require_approval_never_tools);
            if (ftNever.length > 0) {
                var nf = { tool_names: ftNever };
                if (srv.require_approval_never_read_only) nf.read_only = true;
                ra.never = nf;
            }
            mcp.require_approval = (Object.keys(ra).length > 0) ? ra : "always";
        } else {
            mcp.require_approval = srv.require_approval || "always";
        }
        return mcp;
    }

    window.openMcpManagerModal = function(onClose) {
        var UI = window.SettingsUI;
        UI.injectModalStyles();

        var existing = document.getElementById("mcp-manager-modal");
        if (existing) existing.remove();

        var ov = document.createElement("div");
        ov.id = "mcp-manager-modal";
        ov.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:var(--m-overlay,rgba(0,0,0,0.6));z-index:10001;display:flex;align-items:center;justify-content:center;";

        var md = document.createElement("div");
        md.style.cssText = "background:var(--m-bg,#fff);border-radius:12px;padding:24px;max-width:600px;width:90%;max-height:90vh;overflow-y:auto;box-shadow:0 8px 32px var(--m-shadow,rgba(0,0,0,0.3));color:var(--m-text,#222);";

        var html = '<div style="display:flex;justify-content:space-between;margin-bottom:16px;">' +
            '<h2 style="margin:0;color:var(--m-text,#222);">MCP Серверы</h2>' +
            '<button id="mcp-mgr-close" style="border:none;background:none;font-size:24px;cursor:pointer;color:var(--m-muted,#666);">&times;</button></div>';
        html += '<button id="mcp-mgr-add" style="padding:8px 16px;background:var(--m-accent,#4a90d9);color:#fff;border:none;border-radius:6px;cursor:pointer;margin-bottom:12px;">+ Добавить сервер</button>';
        html += '<div id="mcp-mgr-list"></div>';

        md.innerHTML = html;
        ov.appendChild(md);
        document.body.appendChild(ov);

        function closeModal() { ov.remove(); if (onClose) onClose(); }
        document.getElementById('mcp-mgr-close').addEventListener('click', closeModal);
        ov.addEventListener('click', function(e) { if (e.target === ov) closeModal(); });

        function renderList(servers) {
            var list = document.getElementById('mcp-mgr-list');
            if (!servers || servers.length === 0) {
                list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--m-muted,#999);">Нет MCP серверов. Добавьте первый.</div>';
                return;
            }
            list.innerHTML = servers.map(function(srv) {
                var connInfo = srv.connector_id ? srv.connector_id : (srv.server_url || '(нет URL)');
                return '<div style="display:flex;justify-content:space-between;align-items:center;padding:10px;border:1px solid var(--m-section-border,#eee);border-radius:6px;margin-bottom:8px;background:var(--m-card,#fff);">' +
                    '<div><div style="font-weight:bold;font-size:14px;color:var(--m-text,#222);">' + UI.escapeHtml(srv.name) + '</div>' +
                    '<div style="font-size:12px;color:var(--m-muted,#666);">' + UI.escapeHtml(connInfo) + '</div></div>' +
                    '<div style="display:flex;gap:6px;">' +
                    '<button class="mcp-edit-btn" data-id="' + UI.escapeHtml(srv.id) + '" style="padding:4px 10px;border:1px solid var(--m-border,#ddd);background:var(--m-card,#fff);border-radius:4px;cursor:pointer;font-size:12px;color:var(--m-text,#222);">Изменить</button>' +
                    '<button class="mcp-del-btn" data-id="' + UI.escapeHtml(srv.id) + '" style="padding:4px 10px;border:1px solid var(--m-border,#ddd);background:var(--m-card,#fff);border-radius:4px;cursor:pointer;font-size:12px;color:var(--m-danger,#c33);">Удалить</button>' +
                    '</div></div>';
            }).join('');

            list.querySelectorAll('.mcp-edit-btn').forEach(function(btn) {
                btn.addEventListener('click', function() {
                    var sid = this.getAttribute('data-id');
                    var srv = servers.find(function(s) { return s.id === sid; });
                    if (srv) openMcpEditor(srv);
                });
            });

            list.querySelectorAll('.mcp-del-btn').forEach(function(btn) {
                btn.addEventListener('click', function() {
                    var sid = this.getAttribute('data-id');
                    if (!confirm("Удалить MCP сервер?")) return;
                    fetch('/api/mcp-servers/' + sid, { method: 'DELETE' })
                        .then(function() { refreshList(); })
                        .catch(function(e) { alert("Ошибка: " + e); });
                });
            });
        }

        function refreshList() { window.fetchMcpServers().then(renderList); }

        var editorEl = null;

        function openMcpEditor(srv) {
            if (editorEl) editorEl.remove();
            var isNew = !srv;
            srv = srv || {};

            var CONNECTOR_OPTS = '<option value="">(нет — использовать server_url)</option>' +
                '<option value="termux_api">📱 Termux API (Android Hardware)</option>' +
                '<option value="local_git">📁 Local Git</option>' +
                '<option value="connector_dropbox">Dropbox</option>' +
                '<option value="connector_gmail">Gmail</option>' +
                '<option value="connector_googlecalendar">Google Calendar</option>' +
                '<option value="connector_googledrive">Google Drive</option>' +
                '<option value="connector_microsoftteams">Microsoft Teams</option>' +
                '<option value="connector_outlookcalendar">Outlook Calendar</option>' +
                '<option value="connector_outlookemail">Outlook Email</option>' +
                '<option value="connector_sharepoint">SharePoint</option>';

            function connSelected(val) {
                return CONNECTOR_OPTS.replace('value="' + val + '"', 'value="' + val + '" selected');
            }

            var e = document.createElement("div");
            e.style.cssText = "border:1px solid var(--m-accent,#4a90d9);border-radius:8px;padding:16px;margin-top:12px;background:var(--m-editor,#f8f9fa);";

            e.innerHTML = '<h3 style="margin:0 0 10px;font-size:15px;color:var(--m-text,#222);">' + (isNew ? 'Новый MCP сервер' : 'Редактирование') + '</h3>' +
                '<div style="margin-bottom:8px;">' + UI.lbl('Название') + UI.inp('mcp-ed-name', 'text', srv.name || '', ' style="width:100%;"') + '</div>' +
                '<div style="margin-bottom:8px;">' + UI.lbl('Connector ID') + UI.sel('mcp-ed-connector', connSelected(srv.connector_id || '')) + '</div>' +
                '<div style="margin-bottom:8px;">' + UI.lbl('Server URL') + UI.inp('mcp-ed-url', 'text', srv.server_url || '', ' style="width:100%;" placeholder="https://mcp.example.com/sse"') + '</div>' +
                UI.gap2(
                    '<div>' + UI.lbl('Server Label') + UI.inp('mcp-ed-label', 'text', srv.server_label || '', ' style="width:100%;"') + '</div>',
                    '<div>' + UI.lbl('Authorization') + UI.inp('mcp-ed-auth', 'password', srv.authorization || '', ' style="width:100%;"') + '</div>'
                ) +
                '<div style="margin-bottom:8px;">' + UI.lbl('Description') + UI.inp('mcp-ed-desc', 'text', srv.server_description || '', ' style="width:100%;"') + '</div>' +
                '<div style="margin-bottom:8px;">' + UI.lbl('Headers (JSON)') + '<textarea id="mcp-ed-headers" style="width:100%;min-height:40px;font-family:monospace;font-size:11px;padding:6px;border:1px solid var(--m-border,#ddd);border-radius:4px;background:var(--m-input-bg,#f5f5f5);color:var(--m-input-text,#222);box-sizing:border-box;" placeholder=\'{"X-Key":"val"}\'>' + UI.escapeHtml(srv.headers || '') + '</textarea></div>' +
                '<div style="margin-bottom:8px;">' + UI.lbl('Allowed Tools') + UI.inp('mcp-ed-allowed', 'text', srv.allowed_tools || '', ' style="width:100%;"') + '</div>' +
                '<div style="margin-bottom:4px;">' + UI.chk('mcp-ed-allowed-ro', srv.allowed_tools_read_only || false, 'Read-only filter') + '</div>' +
                '<div style="margin-bottom:8px;">' + UI.lbl('Require Approval') + UI.sel('mcp-ed-approval',
                    '<option value="always"' + (srv.require_approval === 'always' ? ' selected' : '') + '>Always</option>' +
                    '<option value="never"' + (srv.require_approval === 'never' ? ' selected' : '') + '>Never</option>' +
                    '<option value="filter"' + (srv.require_approval === 'filter' ? ' selected' : '') + '>Filter</option>') + '</div>' +
                '<div id="mcp-ed-filter-ui" style="' + (srv.require_approval === 'filter' ? '' : 'display:none;') + 'margin-bottom:8px;padding-left:12px;border-left:2px solid var(--m-border,#ddd);">' +
                    '<div style="font-size:12px;font-weight:bold;margin:6px 0;color:var(--m-text,#222);">Always:</div>' + UI.lbl('Tools') + UI.inp('mcp-ed-appr-tools', 'text', srv.require_approval_tools || '', ' style="width:100%;"') + '<div style="margin:4px 0;">' + UI.chk('mcp-ed-appr-ro', srv.require_approval_read_only || false, 'Read-only') + '</div>' +
                    '<div style="font-size:12px;font-weight:bold;margin:8px 0 6px;color:var(--m-text,#222);">Never:</div>' + UI.lbl('Tools') + UI.inp('mcp-ed-appr-never-tools', 'text', srv.require_approval_never_tools || '', ' style="width:100%;"') + '<div style="margin:4px 0;">' + UI.chk('mcp-ed-appr-never-ro', srv.require_approval_never_read_only || false, 'Read-only') + '</div>' +
                '</div>' +
                '<div style="margin-bottom:8px;">' + UI.chk('mcp-ed-defer', srv.defer_loading || false, 'Defer Loading') + '</div>' +
                '<div style="display:flex;gap:8px;margin-top:10px;">' + UI.btn('mcp-ed-save', isNew ? 'Создать' : 'Сохранить', 'accent') + UI.btn('mcp-ed-cancel', 'Отмена') + '</div>';

            document.getElementById('mcp-mgr-list').insertAdjacentElement('beforebegin', e);
            editorEl = e;

            document.getElementById('mcp-ed-approval').addEventListener('change', function() {
                document.getElementById('mcp-ed-filter-ui').style.display = this.value === 'filter' ? '' : 'none';
            });

            document.getElementById('mcp-ed-cancel').addEventListener('click', function() { editorEl.remove(); editorEl = null; });

            document.getElementById('mcp-ed-save').addEventListener('click', function() {
                var payload = {
                    name: document.getElementById('mcp-ed-name').value.trim(),
                    connector_id: document.getElementById('mcp-ed-connector').value,
                    server_url: document.getElementById('mcp-ed-url').value.trim(),
                    server_label: document.getElementById('mcp-ed-label').value.trim(),
                    authorization: document.getElementById('mcp-ed-auth').value.trim(),
                    server_description: document.getElementById('mcp-ed-desc').value.trim(),
                    headers: document.getElementById('mcp-ed-headers').value.trim(),
                    allowed_tools: document.getElementById('mcp-ed-allowed').value.trim(),
                    allowed_tools_read_only: document.getElementById('mcp-ed-allowed-ro').checked,
                    require_approval: document.getElementById('mcp-ed-approval').value,
                    require_approval_tools: document.getElementById('mcp-ed-appr-tools').value.trim(),
                    require_approval_read_only: document.getElementById('mcp-ed-appr-ro').checked,
                    require_approval_never_tools: document.getElementById('mcp-ed-appr-never-tools').value.trim(),
                    require_approval_never_read_only: document.getElementById('mcp-ed-appr-never-ro').checked,
                    defer_loading: document.getElementById('mcp-ed-defer').checked
                };
                if (!payload.name || (!payload.server_url && !payload.connector_id)) {
                    alert("Заполните название и URL/Connector");
                    return;
                }
                var method = isNew ? 'POST' : 'PUT';
                var url = isNew ? '/api/mcp-servers' : '/api/mcp-servers/' + srv.id;
                fetch(url, { method: method, headers: {"Content-Type":"application/json"}, body: JSON.stringify(payload) })
                    .then(function(r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
                    .then(function() { editorEl.remove(); editorEl = null; refreshList(); })
                    .catch(function(e) { alert("Ошибка: " + e.message); });
            });
        }

        document.getElementById('mcp-mgr-add').addEventListener('click', function() { openMcpEditor(null); });
        refreshList();
    };

    window.SettingsMcp = {
        buildMcpTool: buildMcpTool
    };
})();
