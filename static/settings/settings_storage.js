(function () {
  "use strict";

  var DEFAULTS = {
    temperature: 0.7,
    top_p: 1.0,
    // 0 omits the application output cap; the provider's own limit still applies.
    max_output_tokens: 0,
    max_tool_calls: null,
    truncation: "disabled",
    store: true,
    // Off (owner, 30.10.2026, #1723): background mode queued every round at the provider
    // for 1-3 minutes; a chat answers synchronously and still streams.
    background: false,
    // Live (streamed) answers are on (owner, 05.11.2026: "make text live").
    stream: true,
    previous_response_id: "",
    parallel_tool_calls: true,
    tool_choice: "auto",
    prompt_cache_key: "",
    service_tier: "auto",
    top_logprobs: 0,
    reasoning_effort: "high",
    text_format: "text",
    text_verbosity: "high",
    json_schema_name: "response",
    json_schema:
      '{"type":"object","properties":{"result":{"type":"string"}},"required":["result"],"additionalProperties":false}',
    instructions: "Ты — полезный ассистент.",
    prompt_id: "",
    prompt_version: "",
    prompt_variables: "",
    tools_config: {
      web_search: {
        enabled: true,
        context_size: "high",
        allowed_domains: "",
        blocked_domains: "",
      },
      code_interpreter: { enabled: true },
      file_search: { enabled: true, vector_store_ids: "fvtvp6t2su0g5q4ft2ev", max_results: 50 },
      defer_loading: { eager_categories: [] },
    },
    conv_metadata: "",
  };

  // Settings are global and read-only (GET /api/settings): one set for every dialog, nothing saved.
  var serverSettings = null;
  // Set when the server settings could not be read, so the windows show the defaults.
  var serverSettingsUnknown = false;

  // The server publishes one canonical set of Ultra parameters per model.
  function ultraProfileFor(modelKey) {
    modelKey = modelKey || (typeof currentModel !== "undefined" ? currentModel : "");
    var catalog = typeof modelsData !== "undefined" && modelsData ? modelsData : {};
    var entry = (catalog.text || {})[modelKey];
    return entry && entry.ultra_profile ? entry.ultra_profile : null;
  }

  function applyUltraSettings(settings, modelKey) {
    var profile = ultraProfileFor(modelKey);
    var defaults = profile && profile.settings_defaults;
    if (!defaults || !Object.keys(defaults).length) {
      if (settings.model_profile === "ultra") {
        delete settings.model_profile;
        delete settings.model_profile_id;
      }
      return settings;
    }
    var userCap = settings.max_output_tokens;
    Object.keys(defaults).forEach(function (key) {
      settings[key] = defaults[key];
    });
    // Keep an explicit value intact so serialization and the server can reject invalid caps.
    if (userCap !== undefined && userCap !== null && userCap !== "") {
      settings.max_output_tokens = userCap;
    }
    settings.model_profile = "ultra";
    settings.model_profile_id = profile.profile_id;
    return settings;
  }

  // The global settings as last read from the server, else the defaults. A dialog id is
  // accepted and ignored: no dialog has settings of its own.
  function load() {
    var base = JSON.parse(JSON.stringify(serverSettings || DEFAULTS));
    return applyUltraSettings(base);
  }

  // The server's settings merged over the defaults, as the windows show them.
  function recordFrom(data) {
    var base = JSON.parse(JSON.stringify(DEFAULTS));
    if (data && Object.keys(data).length > 0) {
      base = window.SettingsUI.deepMerge(base, data);
    }
    return applyUltraSettings(base);
  }

  // Reads the global settings; rejects when the server cannot be read.
  function fetchRecord() {
    return window.LogosDispatcher.request("/api/settings", null, { timeoutMs: 5000 })
      .then(function (r) {
        if (!r.ok) return window.LogosHttpError(r);
        return r.json();
      })
      .then(recordFrom);
  }

  // Reads the global settings again (on every dialog switch); the dialog id is ignored.
  window.loadServerConvSettings = function () {
    return fetchRecord()
      .then(function (record) {
        serverSettings = record;
        serverSettingsUnknown = false;
        return load();
      })
      .catch(function () {
        serverSettingsUnknown = true;
        return load();
      });
  };

  window.SettingsStorage = {
    ultraProfileFor: ultraProfileFor,
    load: load,
    isUnknown: function () {
      return serverSettingsUnknown;
    },
  };
})();

