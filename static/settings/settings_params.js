(function() {
    "use strict";

    // === Сборка параметров для Responses API ===
    window.getResponsesParams = function() {
        var UI = window.SettingsUI;
        var s = window.getSettings();
        var params = {
            temperature: parseFloat(s.temperature),
            top_p: parseFloat(s.top_p),
            max_output_tokens: parseInt(s.max_output_tokens, 10),
            truncation: s.truncation,
            store: s.store,
            background: s.background,
            stream: s.stream,
            instructions: s.instructions,
            parallel_tool_calls: (s.parallel_tool_calls !== false && s.parallel_tool_calls !== 'false')
        };
        if (s.conv_metadata) {
            try { params.metadata = JSON.parse(s.conv_metadata); } catch(e) {}
        }
        if (parseInt(s.max_tool_calls, 10) > 0)
            params.max_tool_calls = parseInt(s.max_tool_calls, 10);
        if (s.prompt_cache_key)
            params.prompt_cache_key = s.prompt_cache_key;
        if (s.service_tier && s.service_tier !== "auto")
            params.service_tier = s.service_tier;
        if (parseInt(s.top_logprobs, 10) > 0)
            params.top_logprobs = parseInt(s.top_logprobs, 10);
        if (s.previous_response_id)
            params.previous_response_id = s.previous_response_id;
        if (s.reasoning_effort && s.reasoning_effort !== "none")
            /* reasoning отключен во избежание 500 ошибки */

        // Формат вывода: text или json_schema
        if (s.text_format === "json") {
            var schema = {};
            try { schema = JSON.parse(s.json_schema || "{}"); } catch(e) {}
            params.text = {
                format: {
                    type: "json_schema",
                    json_schema: {
                        name: s.json_schema_name || "response",
                        strict: true,
                        schema: schema
                    }
                },
                verbosity: s.text_verbosity
            };
        } else {
            params.text = { format: { type: "text" }, verbosity: s.text_verbosity };
        }

        // Prompt Templates
        if (s.prompt_id) {
            var p = { id: s.prompt_id };
            if (s.prompt_version) p.version = s.prompt_version;
            if (s.prompt_variables) {
                try { p.variables = JSON.parse(s.prompt_variables); } catch(e) {}
            }
            params.prompt = p;
        }

        if (s.tool_choice && s.tool_choice !== "auto")
            params.tool_choice = s.tool_choice;

        // === Сборка инструментов (tools) ===
        var tools = [];
        var cfg = s.tools_config || {};

        // Skills
        if (s.selected_skills && s.selected_skills.length > 0) {
            var refs = s.selected_skills.map(function(sk) {
                var r = { type: "skill_reference", skill_id: sk.id };
                if (sk.version) r.version = sk.version;
                else if (sk.latest_version) r.version = sk.latest_version;
                return r;
            });
            tools.push({ type: "shell", environment: { type: "container_auto", skills: refs } });
        }

        // Web Search
        if (cfg.web_search && cfg.web_search.enabled) {
            var ws = { type: "web_search", search_context_size: cfg.web_search.context_size };
            if (cfg.web_search.allowed_domains || cfg.web_search.blocked_domains) {
                var fl = {};
                if (cfg.web_search.allowed_domains)
                    fl.allowed_domains = UI.strToArr(cfg.web_search.allowed_domains);
                if (cfg.web_search.blocked_domains)
                    fl.blocked_domains = UI.strToArr(cfg.web_search.blocked_domains);
                ws.filters = fl;
            }
            tools.push(ws);
        }

        // Code Interpreter
        if (cfg.code_interpreter && cfg.code_interpreter.enabled)
            tools.push({ type: "code_interpreter" });

        // File Search
        if (cfg.file_search && cfg.file_search.enabled && cfg.file_search.vector_store_ids) {
            tools.push({
                type: "file_search",
                vector_store_ids: UI.strToArr(cfg.file_search.vector_store_ids),
                max_results: parseInt(cfg.file_search.max_results, 10)
            });
        }

        // MCP Серверы — из кэша
        var enabledIds = s.enabled_mcp_servers || [];
        var approvalMap = s.mcp_approval || {};
        window.getMcpServersCache().forEach(function(srv) {
            if (enabledIds.indexOf(srv.id) !== -1) {
                if (srv.connector_id === 'local_git' || srv.connector_id === 'termux_api') {
                    tools.push({ type: "local", connector_id: srv.connector_id, name: srv.name });
                } else if (srv.server_url || (srv.connector_id && srv.connector_id.indexOf('connector_') === 0)) {
                    var override = (srv.id in approvalMap) ? approvalMap[srv.id] : null;
                    tools.push(window.SettingsMcp.buildMcpTool(srv, override));
                }
            }
        });

        if (tools.length > 0) params.tools = tools;
        return params;
    };

    // === Параметры для Realtime API (голос) ===
    window.getRealtimeParams = function() {
        var s = window.getSettings();
        return {
            voice: s.voice,
            response_mode: s.response_mode,
            instructions: s.voice_instructions,
            vad_threshold: parseFloat(s.vad_threshold),
            vad_silence_duration_ms: parseInt(s.vad_silence_duration_ms, 10),
            vad_prefix_padding_ms: parseInt(s.vad_prefix_padding_ms, 10)
        };
    };

    // === Metadata для Conversations API ===
    window.getConvMetadata = function() {
        var s = window.getSettings();
        if (!s.conv_metadata) return {};
        try { return JSON.parse(s.conv_metadata); } catch(e) { return {}; }
    };

    // === Vector Stores для File Search ===
    var vsCache = [];
    var vsFetchPromise = null;

    window.fetchVectorStores = function() {
        if (vsFetchPromise) return vsFetchPromise;
        vsFetchPromise = fetch('/api/vector-stores')
            .then(function(r) { if (!r.ok) return { data: [] }; return r.json(); })
            .then(function(data) {
                vsCache = (data && data.data) || [];
                vsFetchPromise = null;
                return vsCache;
            })
            .catch(function(e) { vsFetchPromise = null; return []; });
        return vsFetchPromise;
    };

    // Рендер списка хранилищ с чекбоксами
    window.renderVsList = function(vsList) {
        var UI = window.SettingsUI;
        var container = document.getElementById('vs-list-container');
        if (!container) return;
        var currentIds = document.getElementById('inp-fs-ids').value.split(',').map(function(s) { return s.trim(); }).filter(Boolean);
        if (!vsList || vsList.length === 0) {
            container.innerHTML = '<div style="padding:5px;color:var(--m-muted,#999);font-size:12px;">Нет доступных хранилищ.</div>';
            return;
        }
        container.innerHTML = vsList.map(function(vs) {
            var checked = currentIds.indexOf(vs.id) !== -1;
            return '<div style="display:flex;align-items:center;gap:6px;padding:4px;border-bottom:1px solid var(--m-section-border,#eee);">' +
                '<input type="checkbox" class="vs-chk track-change" value="' + UI.escapeHtml(vs.id) + '" data-name="' + UI.escapeHtml(vs.name || vs.id) + '" ' + (checked ? 'checked' : '') + '>' +
                '<span style="font-size:12px;color:var(--m-text,#222);">' + UI.escapeHtml(vs.name || vs.id) + '</span>' +
                '<span style="font-size:10px;color:var(--m-muted,#666);margin-left:auto;">' + UI.escapeHtml((vs.id || '').substring(0, 8)) + '...</span>' +
                '</div>';
        }).join('');
        // Обработчики чекбоксов — обновляют скрытое поле inp-fs-ids
        container.querySelectorAll('.vs-chk').forEach(function(chk) {
            chk.addEventListener('change', function() {
                var ids = document.getElementById('inp-fs-ids').value.split(',').map(function(s) { return s.trim(); }).filter(Boolean);
                var id = this.value;
                if (this.checked && ids.indexOf(id) === -1) {
                    ids.push(id);
                } else if (!this.checked) {
                    ids = ids.filter(function(x) { return x !== id; });
                }
                document.getElementById('inp-fs-ids').value = ids.join(',');
            });
        });
    };

    // === Загрузка списка Skills ===
    window.fetchSkillsList = function() {
        var Storage = window.SettingsStorage;
        return fetch('/api/skills')
            .then(function(r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
            .then(function(data) {
                var skills = data.data || [];
                localStorage.setItem(Storage.SKILLS_BACKUP_KEY, JSON.stringify(skills));
                return skills;
            })
            .catch(function(e) {
                var b = localStorage.getItem(Storage.SKILLS_BACKUP_KEY);
                return b ? JSON.parse(b) : [];
            });
    };
})();
