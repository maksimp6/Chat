(function () {
  "use strict";

  // The schema is typed as JSON text in settings; the provider expects an object.
  function parseJsonSchema(raw) {
    if (raw && typeof raw === "object" && !Array.isArray(raw)) return raw;
    if (typeof raw !== "string" || !raw.trim()) return null;
    try {
      var parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed;
    } catch (error) {
      console.warn("[SETTINGS] json_schema is not valid JSON; text format is not sent", error);
    }
    return null;
  }

  function numericSetting(value, fallback, name) {
    if (value === undefined || value === null || value === "") return fallback;
    if (typeof value !== "number" && typeof value !== "string") {
      throw new Error(name + ": требуется число");
    }
    var number = Number(value);
    if (!Number.isFinite(number)) throw new Error(name + ": требуется конечное число");
    return number;
  }

  // WIKIFUNCTIONS-CANDIDATE WF-19: https://www.wikifunctions.org/wiki/Z10079 + Z10047.
  // Заменить строковую ветку boolean композицией trim/lowercase; адаптер сохраняет
  // bool без преобразования, null/undefined fallback, только true/false и прежнюю ошибку с name.
  function booleanSetting(value, fallback, name) {
    if (value === undefined || value === null) return fallback;
    if (typeof value === "boolean") return value;
    if (typeof value === "string" && /^(true|false)$/i.test(value.trim())) {
      return value.trim().toLowerCase() === "true";
    }
    throw new Error(name + ": требуется true или false");
  }

  window.getResponsesParams = function () {
    var Storage = window.SettingsStorage;
    var s = Storage.load(currentConvId) || {};

    var params = {
      temperature: numericSetting(s.temperature, 0.7, "temperature"),
      top_p: numericSetting(s.top_p, 1.0, "top_p"),
      max_output_tokens: numericSetting(s.max_output_tokens, 0, "max_output_tokens"),
      truncation: s.truncation || "disabled",
      store: booleanSetting(s.store, true, "store"),
      background: booleanSetting(s.background, true, "background"),
      stream: booleanSetting(s.stream, false, "stream"),
      instructions: s.instructions || "",
      tool_choice: s.tool_choice || "auto",
      parallel_tool_calls: booleanSetting(s.parallel_tool_calls, true, "parallel_tool_calls"),
      max_tool_calls: numericSetting(s.max_tool_calls, null, "max_tool_calls"),
    };

    if (s.reasoning_effort && s.reasoning_effort !== "disabled") {
      params.reasoning_effort = s.reasoning_effort;
    }

    if (s.text_verbosity) {
      params.text = { verbosity: s.text_verbosity };
    }
    if (s.text_format === "json") {
      var schema = parseJsonSchema(s.json_schema);
      if (!schema) throw new Error("json_schema: требуется корректный JSON-объект");
      params.text = Object.assign({}, params.text, {
        format: {
          type: "json_schema",
          name: s.json_schema_name || "response",
          schema: schema,
        },
      });
    }

    if (s.service_tier && s.service_tier !== "auto") {
      params.service_tier = s.service_tier;
    }

    if (s.previous_response_id) {
      params.previous_response_id = String(s.previous_response_id).trim();
    }

    if (s.prompt_cache_key) {
      params.prompt_cache_key = String(s.prompt_cache_key).trim();
    }

    if (s.top_logprobs !== undefined && s.top_logprobs !== null && s.top_logprobs !== "") {
      params.top_logprobs = numericSetting(s.top_logprobs, 0, "top_logprobs");
    }

    return params;
  };
})();

