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
    background: true,
    // New dialogs stream the answer into the chat as the model writes it.
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
    selected_skills: [],
    enabled_mcp_servers: [],
    mcp_approval: {},
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
    // Matches yandex_client_modules.tool_config.SETTINGS_REVISION.
    settings_revision: 6,
    conv_metadata: "",
    voice: "kirill",
    response_mode: "audio",
    voice_instructions: "Ты — полезный голосовой ассистент.",
    vad_threshold: 0.3,
    vad_silence_duration_ms: 500,
    vad_prefix_padding_ms: 300,
  };

  var SETTINGS_REVISION = 6;

  // Copies saved before SETTINGS_REVISION are upgraded one revision at a time, as the server upgrades them
  // (yandex_client_modules.tool_config.upgrade_dialog_settings); a missing or non-integer revision counts as 0.
  // Revision 2: the former medium/off defaults nobody chose are read at the maximal values.
  // Revision 3: live answers (stream) are on for a copy with no stream key; a stored stream value, true or false, is kept.
  // Revision 4: the server adds the cache category to a stored tool selection; a local copy keeps none.
  // Revisions 5 and 6: the server added a helper category and took it out again; a local copy keeps none.
  function upgradeLegacySettings(settings) {
    if (!settings || typeof settings !== "object") return settings;
    var revision = Number.isInteger(settings.settings_revision) ? settings.settings_revision : 0;
    if (revision >= SETTINGS_REVISION) return settings;
    if (revision < 2) upgradeToRevision2(settings);
    if (revision < 3 && !("stream" in settings)) settings.stream = true;
    settings.settings_revision = SETTINGS_REVISION;
    return settings;
  }

  function upgradeToRevision2(settings) {
    if (settings.reasoning_effort === undefined || settings.reasoning_effort === "low" || settings.reasoning_effort === "medium") {
      settings.reasoning_effort = "high";
    }
    if (["low", "medium", "concise", "verbose", undefined].indexOf(settings.text_verbosity) !== -1) {
      settings.text_verbosity = "high";
    }
    if (settings.truncation === "auto") settings.truncation = "disabled";
    if ("max_output_tokens" in settings) settings.max_output_tokens = 0;
    if ("max_tool_calls" in settings) settings.max_tool_calls = null;
    if ("parallel_tool_calls" in settings) settings.parallel_tool_calls = true;
    var tools = settings.tools_config && typeof settings.tools_config === "object" ? settings.tools_config : {};
    tools.web_search = Object.assign({}, tools.web_search || {}, { enabled: true, context_size: "high" });
    tools.code_interpreter = Object.assign({}, tools.code_interpreter || {}, { enabled: true });
    tools.file_search = Object.assign({}, tools.file_search || {}, { enabled: true, max_results: 50 });
    settings.tools_config = tools;
  }

  var STORAGE_KEY = "logos_settings";
  var SKILLS_BACKUP_KEY = "logos_skills_backup";
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
      base = window.SettingsUI.deepMerge(base, upgradeLegacySettings(data));
    }
    return applyUltraSettings(base);
  }

  // Reads the global settings; rejects when the server cannot be read.
  function fetchRecord() {
    return window.LogosDispatcher.request("/api/settings", null, { timeoutMs: 5000 })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
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
    DEFAULTS: DEFAULTS,
    STORAGE_KEY: STORAGE_KEY,
    SKILLS_BACKUP_KEY: SKILLS_BACKUP_KEY,
    SETTINGS_REVISION: SETTINGS_REVISION,
    upgradeLegacySettings: upgradeLegacySettings,
    ultraProfileFor: ultraProfileFor,
    applyUltraSettings: applyUltraSettings,
    load: load,
    isUnknown: function () {
      return serverSettingsUnknown;
    },
  };
})();

