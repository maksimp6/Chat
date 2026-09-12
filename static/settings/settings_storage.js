(function() {
    "use strict";
    
    var DEFAULTS = {
        temperature: 0.7, top_p: 1.0, max_output_tokens: 2000,
        max_tool_calls: 10, truncation: "disabled",
        store: true, background: true,
        stream: false, previous_response_id: "", parallel_tool_calls: true,
        tool_choice: "auto", prompt_cache_key: "", service_tier: "auto", top_logprobs: 0,
        reasoning_effort: "medium",
        text_format: "text", text_verbosity: "medium",
        json_schema_name: "response",
        json_schema: '{"type":"object","properties":{"result":{"type":"string"}},"required":["result"],"additionalProperties":false}',
        instructions: "Ты — полезный ассистент.",
        prompt_id: "", prompt_version: "", prompt_variables: "",
        selected_skills: [],
        enabled_mcp_servers: [],
        mcp_approval: {},
        tools_config: {
            web_search: { enabled: false, context_size: "medium", allowed_domains: "", blocked_domains: "" },
            code_interpreter: { enabled: false },
            file_search: { enabled: false, vector_store_ids: "", max_results: 20 }
        },
        conv_metadata: "", 
        voice: "kirill", response_mode: "audio",
        voice_instructions: "Ты — полезный голосовой ассистент.",
        vad_threshold: 0.3, vad_silence_duration_ms: 500, vad_prefix_padding_ms: 300
    };
    
    var STORAGE_KEY = "alice_pro_settings";
    var SKILLS_BACKUP_KEY = "alice_pro_skills_backup";
    
    function convKey(convId) { return STORAGE_KEY + "_conv_" + convId; }

    function load(convId) {
        convId = convId || (typeof currentConvId !== 'undefined' && currentConvId ? currentConvId : null);
        var base = JSON.parse(JSON.stringify(DEFAULTS));
        try { 
            var global = localStorage.getItem(STORAGE_KEY); 
            if (global) base = window.SettingsUI.deepMerge(base, JSON.parse(global)); 
        } catch(e) {}
        if (convId) { 
            try { 
                var conv = localStorage.getItem(convKey(convId)); 
                if (conv) base = window.SettingsUI.deepMerge(base, JSON.parse(conv)); 
            } catch(e) {} 
        }
        return base;
    }
    
    function save(settings, convId) {
        convId = convId || (typeof currentConvId !== 'undefined' && currentConvId ? currentConvId : null);
        if (convId) localStorage.setItem(convKey(convId), JSON.stringify(settings));
        else localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    }

    window.getSettings = function(convId) { return load(convId); };
    window.saveSettings = function(settings, convId) { save(settings, convId); };
    window.resetSettings = function(convId) {
        convId = convId || (typeof currentConvId !== 'undefined' && currentConvId ? currentConvId : null);
        if (convId) localStorage.removeItem(convKey(convId)); else localStorage.removeItem(STORAGE_KEY);
        return JSON.parse(JSON.stringify(DEFAULTS));
    };
    window.clearConversationSettings = function(convId) { if (convId) localStorage.removeItem(convKey(convId)); };

    window.SettingsStorage = {
        DEFAULTS: DEFAULTS,
        STORAGE_KEY: STORAGE_KEY,
        SKILLS_BACKUP_KEY: SKILLS_BACKUP_KEY,
        load: load,
        save: save
    };
})();
