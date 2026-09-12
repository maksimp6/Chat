(function() {
    "use strict";

    window.getResponsesParams = function() {
        var Storage = window.SettingsStorage;
        var currentConvId = typeof window.currentConvId !== "undefined" ? window.currentConvId : null;
        var s = Storage.load(currentConvId) || {};

        var params = {
            temperature: typeof s.temperature === "number" ? s.temperature : 0.7,
            top_p: typeof s.top_p === "number" ? s.top_p : 1.0,
            max_output_tokens: parseInt(s.max_output_tokens, 10) || 2000,
            truncation: s.truncation || "disabled",
            store: s.store !== false,
            background: s.background !== false,
            stream: s.stream === true,
            instructions: s.instructions || "",
            tool_choice: s.tool_choice || "auto",
            parallel_tool_calls: s.parallel_tool_calls !== false,
            max_tool_calls: parseInt(s.max_tool_calls, 10) || 10
        };

        if (s.reasoning_effort && s.reasoning_effort !== "disabled") {
            params.reasoning_effort = s.reasoning_effort;
        }

        if (s.text_format === "json") {
            params.text = {
                format: "json",
                json_schema: {
                    name: s.json_schema_name || "response",
                    schema: s.json_schema || "{}"
                }
            };
        }

        if (s.service_tier && s.service_tier !== "auto") {
            params.service_tier = s.service_tier;
        }

        if (s.previous_response_id) {
            params.previous_response_id = s.previous_response_id.trim();
        }

        if (s.prompt_cache_key) {
            params.prompt_cache_key = s.prompt_cache_key.trim();
        }

        if (s.top_logprobs > 0) {
            params.top_logprobs = parseInt(s.top_logprobs, 10);
        }

        return params;
    };
})();
