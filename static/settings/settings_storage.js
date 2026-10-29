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
    },
    // Matches yandex_client_modules.tool_config.SETTINGS_REVISION.
    settings_revision: 4,
    conv_metadata: "",
    voice: "kirill",
    response_mode: "audio",
    voice_instructions: "Ты — полезный голосовой ассистент.",
    vad_threshold: 0.3,
    vad_silence_duration_ms: 500,
    vad_prefix_padding_ms: 300,
  };

  var SETTINGS_REVISION = 4;

  // Copies saved before SETTINGS_REVISION are upgraded one revision at a time, as the server upgrades them
  // (yandex_client_modules.tool_config.upgrade_dialog_settings); a missing or non-integer revision counts as 0.
  // Revision 2: the former medium/off defaults nobody chose are read at the maximal values.
  // Revision 3: live answers (stream) are on for a copy with no stream key; a stored stream value, true or false, is kept.
  // Revision 4: the server adds the cache category to a stored tool selection; a local copy keeps none.
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
  var serverSettingsCache = {};
  // Bumped on every save, so a load that started before it cannot overwrite it.
  var settingsSaveVersion = {};
  // Set when the stored settings of a dialog could not be read, so the cache holds local values or defaults.
  var serverSettingsUnknown = {};
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
      if (global) base = window.SettingsUI.deepMerge(base, upgradeLegacySettings(JSON.parse(global)));
    } catch (e) {}
    if (convId) {
      try {
        var conv = localStorage.getItem(convKey(convId));
        if (conv) base = window.SettingsUI.deepMerge(base, upgradeLegacySettings(JSON.parse(conv)));
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
    var previousCache = serverSettingsCache[convId];
    var previousLocal = localStorage.getItem(convKey(convId));
    settingsSaveVersion[convId] = (settingsSaveVersion[convId] || 0) + 1;
    serverSettingsCache[convId] = settings;
    localStorage.setItem(convKey(convId), JSON.stringify(settings));
    return window.LogosDispatcher.request("/api/conversations/" + convId + "/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    })
      .then(function (r) {
        if (r.ok) return null;
        return r.json().catch(function () { return {}; }).then(function (body) {
          var error = new Error(body && body.message ? body.message : "HTTP " + r.status);
          error.serverMessage = Boolean(body && body.message);
          throw error;
        });
      })
      .then(
        function () {
          serverSettingsUnknown[convId] = false;
        },
        function (error) {
          // A refused copy must not stay in the cache or localStorage, or a reload would show it as saved.
          if (previousCache === undefined) delete serverSettingsCache[convId];
          else serverSettingsCache[convId] = previousCache;
          if (previousLocal === null) localStorage.removeItem(convKey(convId));
          else localStorage.setItem(convKey(convId), previousLocal);
          throw error;
        },
      );
  }

  // The stored record merged over the defaults and the global copy, as the window shows it.
  function recordFrom(data) {
    var base = JSON.parse(JSON.stringify(DEFAULTS));
    try {
      var global = globalSettingsCache;
      if (global === null) {
        global = localStorage.getItem(STORAGE_KEY);
        globalSettingsCache = global || "";
      }
      if (global) base = window.SettingsUI.deepMerge(base, upgradeLegacySettings(JSON.parse(global)));
    } catch (e) {}
    if (data && Object.keys(data).length > 0) {
      base = window.SettingsUI.deepMerge(base, upgradeLegacySettings(data));
    }
    return applyUltraSettings(base);
  }

  // Reads the stored record of a dialog; rejects when the server cannot be read.
  function fetchRecord(convId) {
    return window.LogosDispatcher.request("/api/conversations/" + convId + "/settings", null, {
      timeoutMs: 5000,
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(recordFrom);
  }

  // Загрузка настроек с сервера при смене диалога
  window.loadServerConvSettings = function (convId) {
    if (!convId) return Promise.resolve(load(convId));
    var versionAtRequest = settingsSaveVersion[convId] || 0;
    return fetchRecord(convId)
      .then(function (record) {
        // A save made while this GET was in flight is newer than the server snapshot it returned.
        if ((settingsSaveVersion[convId] || 0) !== versionAtRequest) return load(convId);
        serverSettingsCache[convId] = record;
        serverSettingsUnknown[convId] = false;
        return record;
      })
      .catch(function () {
        serverSettingsUnknown[convId] = true;
        return load(convId);
      });
  };

  // Writes only `changes` onto the stored record. The record is read again first, so values saved
  // from another tab or device are kept, and nothing is written when that read fails.
  function saveChanges(changes, convId) {
    if (!convId) return save(Object.assign(load(null), changes), null);
    return fetchRecord(convId).then(function (record) {
      // Tool selection is saved by PUT /tools; a settings save must not write back a copy of it.
      delete record.active_tool_categories;
      var storedTools = record.tools_config || {};
      var merged = Object.assign(record, changes);
      // tools_config is merged tool by tool, so a tool that `changes` does not name keeps its stored value.
      if (changes.tools_config) {
        merged.tools_config = Object.assign({}, storedTools, changes.tools_config);
      }
      return save(merged, convId);
    });
  }

  // «Сброс» only supplies the generation defaults; the caller saves the keys it resets.
  window.resetSettings = function () {
    return applyUltraSettings(JSON.parse(JSON.stringify(DEFAULTS)));
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
    save: save,
    saveChanges: saveChanges,
    isUnknown: function (convId) {
      return Boolean(serverSettingsUnknown[convId]);
    },
  };
})();

