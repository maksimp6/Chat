"use strict";

(function () {
  var MODULE_API_VERSION = "1";
  var MAX_TRACE_COUNT = 200;
  var MAX_DATA_DEPTH = 6;
  var MAX_STRING_LENGTH = 2048;
  var SENSITIVE_KEY_RE = /(?:api[_-]?key|authorization|password|passwd|secret|token|credential|cookie|private[_-]?key)/i;

  var modules = new Map();
  var statuses = new Map();
  var subscribers = new Set();
  var revoked = new Set();
  var traces = new Map();
  var traceCounter = 0;
  var SYSTEM_MODULES = {
    core: true,
    network: true,
    ui: true,
    dispatcher: true
  };

  function now() {
    return Date.now();
  }

  function makeId(prefix) {
    traceCounter += 1;
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return prefix + ":" + window.crypto.randomUUID();
    }
    return prefix + ":" + now().toString(36) + ":" + traceCounter.toString(36);
  }

  function redact(value, depth, seen) {
    if (depth > MAX_DATA_DEPTH) return "[MAX_DEPTH]";
    if (value === null || value === undefined) return value;
    if (typeof value === "string") {
      return redactString(value);
    }
    if (typeof value !== "object") return value;

    if (seen.has(value)) return "[Circular]";
    seen.add(value);

    if (Array.isArray(value)) {
      return value.slice(0, 128).map(function (item) {
        return redact(item, depth + 1, seen);
      });
    }

    var result = {};
    Object.keys(value).slice(0, 128).forEach(function (key) {
      if (SENSITIVE_KEY_RE.test(key)) {
        result[key] = "[REDACTED]";
        return;
      }
      result[key] = redact(value[key], depth + 1, seen);
    });
    return result;
  }

  function redactString(value) {
    var text = String(value || "").slice(0, MAX_STRING_LENGTH);
    text = text.replace(/(bearer\s+)[a-z0-9._~+/=-]+/gi, "$1[REDACTED]");
    text = text.replace(/((?:api[_-]?key|authorization|password|passwd|secret|token|credential|cookie|private[_-]?key)\s*[:=]\s*)[^\s,;]+/gi, "$1[REDACTED]");
    return text;
  }

  function safeData(value) {
    return redact(value, 0, new WeakSet());
  }

  function assertModuleId(moduleId) {
    if (typeof moduleId !== "string" || !/^[a-z0-9][a-z0-9._-]{0,63}$/i.test(moduleId)) {
      throw new Error("Invalid module id");
    }
  }

  function assertCapability(moduleId, capability) {
    assertModuleId(moduleId);
    if (typeof capability !== "string" || !capability) {
      throw new Error("Invalid capability");
    }
    if (isRevoked("capability", moduleId + ":" + capability) || isRevoked("module", moduleId)) {
      throw new Error("Capability revoked");
    }
    var record = modules.get(moduleId);
    if (!record || record.capabilities.indexOf(capability) === -1) {
      throw new Error("Capability denied: " + capability);
    }
    return true;
  }

  function registerModule(descriptor) {
    descriptor = descriptor || {};
    assertModuleId(descriptor.id);
    if (modules.has(descriptor.id)) throw new Error("Module already registered: " + descriptor.id);

    var capabilities = Array.isArray(descriptor.capabilities)
      ? descriptor.capabilities.map(String)
      : [];

    modules.set(descriptor.id, Object.freeze({
      id: descriptor.id,
      version: String(descriptor.version || "0.0.0"),
      apiVersion: String(descriptor.apiVersion || MODULE_API_VERSION),
      dependencies: Array.isArray(descriptor.dependencies) ? descriptor.dependencies.slice() : [],
      capabilities: capabilities
    }));

    setStatus(descriptor.id, "ready", "Зарегистрирован", {
      revocable: true
    });
    return createModuleContext(descriptor.id);
  }

  function createModuleContext(moduleId) {
    var record = modules.get(moduleId);
    if (!record) throw new Error("Unknown module: " + moduleId);

    return Object.freeze({
      module: Object.freeze({
        id: record.id,
        version: record.version,
        apiVersion: record.apiVersion
      }),
      status: Object.freeze({
        set: function (status, message, metadata) {
          return setStatus(moduleId, status, message, metadata);
        }
      }),
      trace: Object.freeze({
        begin: function (operation, metadata) {
          return beginTrace(moduleId, operation, metadata);
        }
      }),
      security: Object.freeze({
        redact: safeData,
        require: function (capability) {
          return assertCapability(moduleId, capability);
        }
      }),
      revoke: Object.freeze({
        isRevoked: function () {
          return isRevoked("module", moduleId);
        }
      })
    });
  }

  function setStatus(moduleId, status, message, metadata) {
    assertModuleId(moduleId);
    if (!SYSTEM_MODULES[moduleId] && !modules.has(moduleId)) {
      throw new Error("Module is not registered: " + moduleId);
    }
    var allowed = {
      ready: true,
      running: true,
      waiting: true,
      degraded: true,
      blocked: true,
      revoked: true,
      error: true,
      stopped: true
    };
    if (!allowed[status]) throw new Error("Unknown status: " + status);

    var value = {
      moduleId: moduleId,
      status: status,
      message: typeof message === "string" ? redactString(message).slice(0, 300) : "",
      metadata: safeData(metadata || {}),
      timestamp: now()
    };
    statuses.set(moduleId, value);
    notify(value);
    return Object.freeze(Object.assign({}, value));
  }

  function clearStatus(moduleId) {
    statuses.delete(moduleId);
    notify({
      moduleId: moduleId,
      status: "stopped",
      message: "",
      metadata: {},
      timestamp: now()
    });
  }

  function notify(value) {
    var safeValue = safeData(value);
    subscribers.forEach(function (callback) {
      try {
        callback(safeValue);
      } catch (error) {
        console.error("[CORE STATUS]", error);
      }
    });
    window.dispatchEvent(new CustomEvent("alice:status", {detail: safeValue}));
  }

  function subscribeStatus(callback) {
    if (typeof callback !== "function") throw new Error("Status subscriber must be a function");
    subscribers.add(callback);
    statuses.forEach(function (value) {
      callback(safeData(value));
    });
    return function () {
      subscribers.delete(callback);
    };
  }

  function isRevoked(type, id) {
    return revoked.has(String(type) + ":" + String(id));
  }

  function revoke(type, id, reason) {
    var key = String(type) + ":" + String(id);
    revoked.add(key);

    if (type === "module") {
      setStatus(String(id), "revoked", "Доступ отозван", {
        reason: typeof reason === "string" ? reason : "revoked",
        revocable: true
      });
    }

    window.dispatchEvent(new CustomEvent("alice:revocation", {
      detail: {
        type: String(type),
        id: String(id),
        reason: typeof reason === "string" ? redactString(reason).slice(0, 300) : "revoked",
        timestamp: now()
      }
    }));

    return true;
  }

  function revokeAll(moduleId, reason) {
    assertModuleId(moduleId);
    revoke("module", moduleId, reason || "module_revoked");
    var record = modules.get(moduleId);
    if (record) {
      record.capabilities.forEach(function (capability) {
        revoke("capability", moduleId + ":" + capability, reason || "module_revoked");
      });
    }
    return true;
  }

  function beginTrace(moduleId, operation, metadata) {
    assertModuleId(moduleId);
    var traceId = makeId("trace");
    var entry = {
      traceId: traceId,
      moduleId: moduleId,
      operation: typeof operation === "string" ? operation.slice(0, 200) : "operation",
      startedAt: now(),
      events: [],
      errors: [],
      metadata: safeData(metadata || {})
    };

    traces.set(traceId, entry);
    if (traces.size > MAX_TRACE_COUNT) {
      traces.delete(traces.keys().next().value);
    }

    function add(eventType, payload) {
      entry.events.push({
        type: eventType,
        timestamp: now(),
        data: safeData(payload)
      });
      return traceId;
    }

    function end(status) {
      entry.completedAt = now();
      entry.status = status || "completed";
      return safeData(entry);
    }

    return Object.freeze({
      traceId: traceId,
      event: function (name, payload) {
        return add(String(name || "event"), payload);
      },
      toolCall: function (tool, args) {
        return add("tool_call:" + String(tool || "unknown"), args);
      },
      response: function (response) {
        return add("response", response);
      },
      error: function (error) {
        var message = error && error.message ? error.message : String(error || "error");
        entry.errors.push({
          timestamp: now(),
          message: redactString(message).slice(0, 500)
        });
        return traceId;
      },
      end: end,
      snapshot: function () {
        return safeData(entry);
      }
    });
  }

  function defer(callback, delayMs) {
    if (typeof callback !== "function") throw new Error("Scheduler callback must be a function");
    var ms = Math.min(Math.max(Number(delayMs || 0), 0), 30000);
    if (ms === 0 && typeof queueMicrotask === "function") {
      var cancelled = false;
      queueMicrotask(function () {
        if (!cancelled) callback();
      });
      return Object.freeze({cancel: function () { cancelled = true; }});
    }
    if (typeof AbortSignal === "undefined" || typeof AbortSignal.timeout !== "function") {
      throw new Error("Scheduler is unavailable: AbortSignal.timeout is required");
    }
    var signal = AbortSignal.timeout(ms);
    var active = true;
    var listener = function () {
      if (!active) return;
      active = false;
      callback();
    };
    signal.addEventListener("abort", listener, {once: true});
    return Object.freeze({
      cancel: function () {
        active = false;
        signal.removeEventListener("abort", listener);
      }
    });
  }

  function getStatusSnapshot() {
    var snapshot = {};
    statuses.forEach(function (value, key) {
      snapshot[key] = safeData(value);
    });
    return snapshot;
  }

  window.AliceCoreAPI = Object.freeze({
    apiVersion: MODULE_API_VERSION,
    module: Object.freeze({
      register: registerModule,
      context: createModuleContext,
      list: function () {
        return Array.from(modules.values()).map(function (item) {
          return safeData(item);
        });
      }
    }),
    status: Object.freeze({
      set: setStatus,
      clear: clearStatus,
      subscribe: subscribeStatus,
      snapshot: getStatusSnapshot
    }),
    trace: Object.freeze({
      begin: beginTrace
    }),
    revocation: Object.freeze({
      revoke: revoke,
      isRevoked: isRevoked,
      revokeAll: revokeAll
    }),
    security: Object.freeze({
      redact: safeData
    }),
    scheduler: Object.freeze({
      defer: defer
    })
  });

  setStatus("core", "running", "Ядро запускается");
})();
