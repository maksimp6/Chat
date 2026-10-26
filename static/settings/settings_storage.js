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
    stream: false,
    previous_response_id: "",
    parallel_tool_calls: true,
    tool_choice: "auto",
    prompt_cache_key: "",
    service_tier: "auto",
    top_logprobs: 0,
    reasoning_effort: "medium",
    text_format: "text",
    text_verbosity: "medium",
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
        context_size: "medium",
        allowed_domains: "",
        blocked_domains: "",
      },
      code_interpreter: { enabled: false },
      file_search: { enabled: false, vector_store_ids: "", max_results: 20 },
    },
    conv_metadata: "",
    voice: "kirill",
    response_mode: "audio",
    voice_instructions: "Ты — полезный голосовой ассистент.",
    vad_threshold: 0.3,
    vad_silence_duration_ms: 500,
    vad_prefix_padding_ms: 300,
  };

  var STORAGE_KEY = "logos_settings";
  var SKILLS_BACKUP_KEY = "logos_skills_backup";
  var serverSettingsCache = {};
  // Bumped on every save, so a load that started before it cannot overwrite it.
  var settingsSaveVersion = {};
  var globalSettingsCache = null; // cache

  function convKey(convId) {
    return STORAGE_KEY + "_conv_" + convId;
  }

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

  // Синхронная загрузка (из кэша или localStorage для обратной совместимости)
  function load(convId) {
    convId =
      convId || (typeof currentConvId !== "undefined" && currentConvId ? currentConvId : null);
    if (convId && serverSettingsCache[convId]) {
      return applyUltraSettings(JSON.parse(JSON.stringify(serverSettingsCache[convId])));
    }
    var base = JSON.parse(JSON.stringify(DEFAULTS));
    try {
      var global = globalSettingsCache;
      if (global === null) {
        global = localStorage.getItem(STORAGE_KEY);
        globalSettingsCache = global || "";
      }
      if (global) base = window.SettingsUI.deepMerge(base, JSON.parse(global));
    } catch (e) {}
    if (convId) {
      try {
        var conv = localStorage.getItem(convKey(convId));
        if (conv) base = window.SettingsUI.deepMerge(base, JSON.parse(conv));
      } catch (e) {}
    }
    return applyUltraSettings(base);
  }

  // Сохранение локально сразу; для диалога — и на сервер (нативное хранилище Kristall).
  // Промис отклоняется, если сервер не принял копию, чтобы окно могло остаться открытым.
  function save(settings, convId) {
    settings = applyUltraSettings(Object.assign({}, settings));
    convId =
      convId || (typeof currentConvId !== "undefined" && currentConvId ? currentConvId : null);
    if (!convId) {
      globalSettingsCache = JSON.stringify(settings);
      localStorage.setItem(STORAGE_KEY, globalSettingsCache);
      return Promise.resolve();
    }
    settingsSaveVersion[convId] = (settingsSaveVersion[convId] || 0) + 1;
    serverSettingsCache[convId] = settings;
    localStorage.setItem(convKey(convId), JSON.stringify(settings));
    return window.LogosDispatcher.request("/api/conversations/" + convId + "/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
    });
  }

  // Загрузка настроек с сервера при смене диалога
  window.loadServerConvSettings = function (convId) {
    if (!convId) return Promise.resolve(load(convId));
    var versionAtRequest = settingsSaveVersion[convId] || 0;
    return window.LogosDispatcher.request("/api/conversations/" + convId + "/settings", null, {
      timeoutMs: 5000,
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        // A save made while this GET was in flight is newer than the server snapshot it returned.
        if ((settingsSaveVersion[convId] || 0) !== versionAtRequest) return load(convId);
        var base = JSON.parse(JSON.stringify(DEFAULTS));
        try {
          var global = globalSettingsCache;
          if (global === null) {
            global = localStorage.getItem(STORAGE_KEY);
            globalSettingsCache = global || "";
          }
          if (global) base = window.SettingsUI.deepMerge(base, JSON.parse(global));
        } catch (e) {}
        if (data && Object.keys(data).length > 0) {
          base = window.SettingsUI.deepMerge(base, data);
        }
        serverSettingsCache[convId] = applyUltraSettings(base);
        return serverSettingsCache[convId];
      })
      .catch(function () {
        return load(convId);
      });
  };

  window.resetSettings = function (convId) {
    var defaults = applyUltraSettings(JSON.parse(JSON.stringify(DEFAULTS)));
    convId =
      convId || (typeof currentConvId !== "undefined" && currentConvId ? currentConvId : null);
    if (convId) {
      localStorage.removeItem(convKey(convId));
      delete serverSettingsCache[convId];
      window.LogosDispatcher.request("/api/conversations/" + convId + "/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(defaults),
      }).catch(function () {});
    } else {
      localStorage.removeItem(STORAGE_KEY);
      globalSettingsCache = "";
    }
    return defaults;
  };

  window.SettingsStorage = {
    DEFAULTS: DEFAULTS,
    STORAGE_KEY: STORAGE_KEY,
    SKILLS_BACKUP_KEY: SKILLS_BACKUP_KEY,
    ultraProfileFor: ultraProfileFor,
    applyUltraSettings: applyUltraSettings,
    load: load,
    save: save,
  };
})();

