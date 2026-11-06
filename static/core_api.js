"use strict";

(function () {
  var MODULE_API_VERSION = "1";
  var MAX_TRACE_COUNT = 200;
  var MAX_DATA_DEPTH = 6;
  var MAX_STRING_LENGTH = 2048;
  var SENSITIVE_KEY_RE =
    /(?:api[_-]?key|authorization|password|passwd|secret|token|credential|cookie|private[_-]?key)/i;

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
    dispatcher: true,
  };

  function makeId(prefix) {
    traceCounter += 1;
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return prefix + ":" + window.crypto.randomUUID();
    }
    return prefix + ":" + Date.now().toString(36) + ":" + traceCounter.toString(36);
  }

  function redact(value, depth, seen) {
    if (depth > MAX_DATA_DEPTH) return "[MAX_DEPTH]";
    if (value === null || value === undefined) return value;
    if (typeof value === "string") {
      return redactString(value);
    }
    if (typeof value !== "object") return value;

    // `seen` holds only the ancestors of this value, so a shared reference is copied
    // in full and only a true cycle is marked.
    if (seen.has(value)) return "[Circular]";
    seen.add(value);
    try {
      if (Array.isArray(value)) {
        return value.slice(0, 128).map(function (item) {
          return redact(item, depth + 1, seen);
        });
      }

      var result = {};
      Object.keys(value)
        .slice(0, 128)
        .forEach(function (key) {
          if (SENSITIVE_KEY_RE.test(key)) {
            result[key] = "[REDACTED]";
            return;
          }
          result[key] = redact(value[key], depth + 1, seen);
        });
      return result;
    } finally {
      seen.delete(value);
    }
  }

  // WIKIFUNCTIONS-CANDIDATE WF-15: https://www.wikifunctions.org/wiki/Z12316.
  // Заменить ядро маскирования двумя regex replacements; сохранить String(value || ""),
  // предварительный slice, порядок шаблонов, gi и $1. Секреты обрабатывать только локально.
  function redactString(value) {
    var text = String(value || "").slice(0, MAX_STRING_LENGTH);
    text = text.replace(/(bearer\s+)[a-z0-9._~+/=-]+/gi, "$1[REDACTED]");
    // Also matches JSON-style pairs such as "api_key": "value".
    text = text.replace(
      /((?:api[_-]?key|authorization|password|passwd|secret|token|credential|cookie|private[_-]?key)["']?\s*[:=]\s*["']?)[^\s,;"']+/gi,
      "$1[REDACTED]",
    );
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

    modules.set(
      descriptor.id,
      Object.freeze({
        id: descriptor.id,
        version: String(descriptor.version || "0.0.0"),
        apiVersion: String(descriptor.apiVersion || MODULE_API_VERSION),
        dependencies: Array.isArray(descriptor.dependencies) ? descriptor.dependencies.slice() : [],
        capabilities: capabilities,
      }),
    );

    setStatus(descriptor.id, "ready", "Зарегистрирован", {
      revocable: true,
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
        apiVersion: record.apiVersion,
      }),
      status: Object.freeze({
        set: function (status, message, metadata) {
          return setStatus(moduleId, status, message, metadata);
        },
      }),
      trace: Object.freeze({
        begin: function (operation, metadata) {
          return beginTrace(moduleId, operation, metadata);
        },
      }),
      security: Object.freeze({
        redact: safeData,
        require: function (capability) {
          return assertCapability(moduleId, capability);
        },
      }),
      revoke: Object.freeze({
        isRevoked: function () {
          return isRevoked("module", moduleId);
        },
      }),
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
      stopped: true,
    };
    if (!allowed[status]) throw new Error("Unknown status: " + status);

    var value = {
      moduleId: moduleId,
      status: status,
      message: typeof message === "string" ? redactString(message).slice(0, 300) : "",
      metadata: safeData(metadata || {}),
      timestamp: Date.now(),
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
      timestamp: Date.now(),
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
    window.dispatchEvent(new CustomEvent("logos:status", { detail: safeValue }));
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

    // An unknown module can still be revoked; there is no status record to update.
    if (type === "module" && (SYSTEM_MODULES[String(id)] || modules.has(String(id)))) {
      setStatus(String(id), "revoked", "Доступ отозван", {
        reason: typeof reason === "string" ? reason : "revoked",
        revocable: true,
      });
    }

    window.dispatchEvent(
      new CustomEvent("logos:revocation", {
        detail: {
          type: String(type),
          id: String(id),
          reason: typeof reason === "string" ? redactString(reason).slice(0, 300) : "revoked",
          timestamp: Date.now(),
        },
      }),
    );

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
      startedAt: Date.now(),
      events: [],
      errors: [],
      metadata: safeData(metadata || {}),
    };

    traces.set(traceId, entry);
    if (traces.size > MAX_TRACE_COUNT) {
      traces.delete(traces.keys().next().value);
    }

    function add(eventType, payload) {
      entry.events.push({
        type: eventType,
        timestamp: Date.now(),
        data: safeData(payload),
      });
      return traceId;
    }

    function end(status) {
      entry.completedAt = Date.now();
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
          timestamp: Date.now(),
          message: redactString(message).slice(0, 500),
        });
        return traceId;
      },
      end: end,
      snapshot: function () {
        return safeData(entry);
      },
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
      return Object.freeze({
        cancel: function () {
          cancelled = true;
        },
      });
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
    signal.addEventListener("abort", listener, { once: true });
    return Object.freeze({
      cancel: function () {
        active = false;
        signal.removeEventListener("abort", listener);
      },
    });
  }

  function getStatusSnapshot() {
    var snapshot = {};
    statuses.forEach(function (value, key) {
      snapshot[key] = safeData(value);
    });
    return snapshot;
  }

  var uiActions = new Map();
  var uiClickRoot = null;
  var uiClickUnmount = null;

  function assertActionName(action) {
    if (typeof action !== "string" || !/^[a-z0-9][a-z0-9._:-]{0,127}$/i.test(action)) {
      throw new Error("Invalid UI action");
    }
  }

  function registerAction(action, handler) {
    assertActionName(action);
    if (typeof handler !== "function") throw new Error("UI action handler must be a function");
    if (uiActions.has(action)) throw new Error("UI action already registered: " + action);
    uiActions.set(action, handler);
    return function () {
      if (uiActions.get(action) === handler) uiActions.delete(action);
    };
  }

  function dispatchAction(action, payload, event) {
    assertActionName(action);
    var handler = uiActions.get(action);
    if (!handler) throw new Error("Unknown UI action: " + action);
    return handler(payload, event);
  }

  function actionParams(element) {
    var params = {};
    if (!element || !element.dataset) return params;
    Object.keys(element.dataset).forEach(function (key) {
      if (key !== "action") params[key] = element.dataset[key];
    });
    return params;
  }

  function actionPayload(button, event) {
    return Object.freeze({
      action: button.dataset.action,
      element: button,
      event: event,
      params: Object.freeze(actionParams(button)),
    });
  }

  function findActionButton(target, root) {
    if (!target || typeof target.closest !== "function") return null;
    var button = target.closest("button[data-action]");
    if (!button) return null;
    if (root === document.body) return button;
    if (typeof root.contains === "function" && root.contains(button)) return button;
    return null;
  }

  // A tap that comes before the page's scripts have all run is kept, its button marked as waiting, and run once they
  // have, instead of being dropped as an unknown action (owner, 05.11.2026: the header icons do not respond). On a
  // 10 KB/s link the ~45 deferred scripts run 30-40 s after the buttons show; most actions are registered only by
  // header_actions.js near the end or at DOMContentLoaded, and a window opened earlier (🤖 models, 🧠 memory) would
  // show before its script fills it. Only the latest such tap is kept: it is the owner's latest intent.
  var pageScriptsRan = document.readyState === "complete";
  var heldTap = null;

  function markTapPending(button, pending) {
    if (!button || !button.classList) return;
    button.classList.toggle("logos-action-pending", pending);
    if (pending) button.setAttribute("aria-busy", "true");
    else button.removeAttribute("aria-busy");
  }

  function runTap(button, event) {
    try {
      dispatchAction(button.dataset.action, actionPayload(button, event), event);
    } catch (error) {
      console.error("[UI ACTION]", error);
    }
  }

  function takeTap(button, event) {
    if (!pageScriptsRan) {
      if (heldTap && heldTap.button !== button) markTapPending(heldTap.button, false);
      heldTap = { button: button, event: event };
      markTapPending(button, true);
      return;
    }
    runTap(button, event);
  }

  function runHeldTap() {
    if (pageScriptsRan) return;
    pageScriptsRan = true;
    var tap = heldTap;
    heldTap = null;
    if (!tap) return;
    markTapPending(tap.button, false);
    if (tap.button.isConnected === false || tap.button.disabled) return;
    runTap(tap.button, tap.event);
  }

  if (!pageScriptsRan) {
    // DOMContentLoaded listeners of later scripts run after this one, so the held tap waits one more turn for them.
    if (typeof document.addEventListener === "function") {
      document.addEventListener(
        "DOMContentLoaded",
        function () {
          setTimeout(runHeldTap, 0);
        },
        { once: true },
      );
    }
    if (typeof window.addEventListener === "function") {
      window.addEventListener("load", runHeldTap, { once: true });
    }
  }

  function mountClickDispatcher(root) {
    if (!root || typeof root.addEventListener !== "function") {
      throw new Error("UI click dispatcher root is required");
    }
    if (uiClickRoot === root) return uiClickUnmount;
    if (uiClickRoot) throw new Error("UI click dispatcher already mounted");

    function onClick(event) {
      var button = findActionButton(event.target, root);
      if (!button || button.disabled) return;
      takeTap(button, event);
    }

    root.addEventListener("click", onClick);
    uiClickRoot = root;
    uiClickUnmount = function () {
      if (uiClickRoot !== root) return;
      root.removeEventListener("click", onClick);
      uiClickRoot = null;
      uiClickUnmount = null;
    };
    // A tap from before this mount (static/boot.js kept it) is taken as if it came now.
    var early =
      window.LogosBoot && typeof window.LogosBoot.takeEarlyTap === "function"
        ? window.LogosBoot.takeEarlyTap()
        : null;
    if (early) {
      markTapPending(early.button, false);
      if (findActionButton(early.button, root) && !early.button.disabled) takeTap(early.button, early.event);
    }
    return uiClickUnmount;
  }

  function createButton(options) {
    options = options || {};
    assertActionName(options.action);
    var button = document.createElement("button");
    button.type = "button";
    button.className =
      "logos-btn" + (options.className ? " " + String(options.className).trim() : "");
    button.dataset.action = options.action;
    if (options.id) button.id = String(options.id);
    if (options.params) {
      Object.keys(options.params).forEach(function (key) {
        if (key === "action") return;
        button.dataset[key] = String(options.params[key]);
      });
    }
    if (options.label) button.setAttribute("aria-label", String(options.label));
    if (options.title) button.title = String(options.title);
    if (options.text !== undefined) button.textContent = String(options.text);
    if (!button.textContent && !button.getAttribute("aria-label") && !button.title) {
      throw new Error("UI button requires text or an accessible label");
    }
    return button;
  }

  function createModal(options) {
    options = options || {};
    if (!options.id) throw new Error("UI modal requires id");
    if (!options.title) throw new Error("UI modal requires title");

    var modal = document.createElement("dialog");
    modal.id = String(options.id);
    modal.className = "modal" + (options.className ? " " + String(options.className).trim() : "");
    modal.hidden = true;
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-hidden", "true");

    var content = document.createElement("div");
    content.className =
      "modal-content" +
      (options.contentClassName ? " " + String(options.contentClassName).trim() : "");

    var title = document.createElement("h2");
    title.id = modal.id + "-title";
    title.className = "modal-title";
    title.textContent = String(options.title);
    modal.setAttribute("aria-labelledby", title.id);

    var closeParams = Object.assign({}, options.closeParams || {});
    if (!closeParams.modal) closeParams.modal = modal.id;
    var close = createButton({
      className: "modal-close",
      label: options.closeLabel || "Закрыть",
      action: options.closeAction || "modal.close",
      params: closeParams,
      text: "×",
    });

    content.appendChild(close);
    content.appendChild(title);
    if (options.body) content.appendChild(options.body);
    modal.appendChild(content);
    return modal;
  }

  var preparedModals = new WeakSet();
  // Windows shown through openModal, oldest first: the last one still open is the top-most.
  var shownModals = [];
  var POINTER_DOWN = typeof window.PointerEvent === "function" ? "pointerdown" : "mousedown";

  function forgetShownModal(modal) {
    shownModals = shownModals.filter(function (item) {
      return item !== modal && item.open && item.isConnected;
    });
  }

  // Escape, a click on the backdrop and the close button take one path, the close button's action, so a window's own
  // cleanup runs. A failing action still closes the window: its cancel event is already prevented by then, and the
  // window would otherwise stay open.
  function requestModalClose(modal, event) {
    var button = modal.querySelector(".modal-close") ||
      modal.querySelector('button[data-action="modal.close"]');
    if (button && button.dataset.action) {
      try {
        dispatchAction(button.dataset.action, actionPayload(button, event), event);
        return;
      } catch (error) {
        console.error("[UI MODAL]", error);
      }
    }
    closeModal(modal);
  }

  // The text field in this window that has focus, or null. A phone hides its keyboard by a tap outside the field; on
  // the backdrop that tap must not close the window and lose the typed text (🔑 wipes a pasted key on close).
  var TEXT_FIELD = "input:not([type=checkbox],[type=radio],[type=button],[type=submit],[type=reset],[type=file]), textarea";
  function focusedTextField(modal) {
    var field = document.activeElement;
    return field && field !== modal && modal.contains(field) && field.matches(TEXT_FIELD) ? field : null;
  }

  function prepareModal(modal) {
    if (!modal || modal.tagName !== "DIALOG" || !modal.classList.contains("modal") ||
        typeof modal.showModal !== "function" || typeof modal.close !== "function") {
      throw new Error("A supported native dialog is required");
    }
    if (preparedModals.has(modal)) return;
    preparedModals.add(modal);
    modal.addEventListener("cancel", function (event) {
      event.preventDefault();
      requestModalClose(modal, event);
    });
    // Every window fills the screen and the page behind it is inert, so a click outside the card lands on the window
    // itself. It was dropped before: with 🤖 open, taps on 🧰 and 🔐 seen through the dimmed layer did nothing. Now it
    // closes the window. A press that starts in the card and ends outside it (selecting text) is not such a click.
    var pressedBackdrop = false;
    // Read at the press: by the click the browser has already moved focus off the field.
    var fieldAtPress = null;
    modal.addEventListener(POINTER_DOWN, function (event) {
      pressedBackdrop = event.target === modal;
      fieldAtPress = pressedBackdrop ? focusedTextField(modal) : null;
    });
    modal.addEventListener("click", function (event) {
      if (event.target !== modal) return;
      var fromBackdrop = pressedBackdrop;
      var field = fieldAtPress;
      pressedBackdrop = false;
      fieldAtPress = null;
      // A window's own backdrop handler, added after this one, must not close it a second time.
      event.stopImmediatePropagation();
      if (!fromBackdrop) return;
      // The first tap only leaves the field (and hides the keyboard); the next one closes the window.
      if (field) {
        if (document.activeElement === field) field.blur();
        return;
      }
      requestModalClose(modal, event);
    });
    modal.addEventListener("close", function () {
      // A close event can be queued immediately before a dialog is reopened.
      if (modal.open) return;
      forgetShownModal(modal);
      modal.classList.remove("visible");
      modal.hidden = true;
      modal.setAttribute("aria-hidden", "true");
    });
  }

  function openModal(modal) {
    prepareModal(modal);
    modal.hidden = false;
    modal.classList.add("visible");
    modal.setAttribute("aria-hidden", "false");
    try {
      if (!modal.open) modal.showModal();
    } catch (error) {
      modal.classList.remove("visible");
      modal.hidden = true;
      modal.setAttribute("aria-hidden", "true");
      throw error;
    }
    forgetShownModal(modal);
    shownModals.push(modal);
    return modal;
  }

  function closeModal(modal) {
    prepareModal(modal);
    if (modal.open) modal.close();
    forgetShownModal(modal);
    modal.classList.remove("visible");
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
    return modal;
  }

  // The window Escape is for. Focus is only ever inside the top-most window (the browser makes the ones under it
  // inert); with focus elsewhere it is the newest window opened here. A window shown another way (the confirm and
  // prompt questions) is left to its own handler and the browser's Escape.
  function escapeDialog(target) {
    var focused = target && typeof target.closest === "function" ? target.closest("dialog[open]") : null;
    if (focused) return focused;
    var open = document.querySelectorAll("dialog[open]");
    for (var index = 0; index < open.length; index += 1) {
      if (!preparedModals.has(open[index])) return null;
    }
    forgetShownModal(null);
    return shownModals.length ? shownModals[shownModals.length - 1] : null;
  }

  // Escape closes the top-most window.
  function onEscapeKey(event) {
    if (event.key !== "Escape" || event.defaultPrevented || event.isComposing) return;
    var dialog = escapeDialog(event.target);
    if (!dialog) return;
    // Prevented, the key does not also reach the browser's own Escape, which would then close a second window.
    event.preventDefault();
    // The cancel event is the path the browser's Escape takes, so the window's own handlers run.
    if (dialog.dispatchEvent(new Event("cancel", { cancelable: true })) && dialog.open) dialog.close();
  }

  if (typeof document.addEventListener === "function") {
    document.addEventListener("keydown", onEscapeKey);
  }

  function modalFromPayload(payload) {
    var modalId = payload && payload.params && payload.params.modal;
    if (!modalId) throw new Error("Modal action requires data-modal");
    var modal = document.getElementById(String(modalId));
    if (!modal) throw new Error("Unknown modal: " + modalId);
    return modal;
  }

  registerAction("modal.open", function (payload) {
    var modal = modalFromPayload(payload);
    document.dispatchEvent(
      new CustomEvent("logos:modal:before-open", {
        detail: Object.freeze({
          modal: modal,
          modalId: modal.id,
          payload: payload,
        }),
      }),
    );
    return openModal(modal);
  });

  registerAction("modal.close", function (payload) {
    return closeModal(modalFromPayload(payload));
  });

  window.LogosCoreAPI = Object.freeze({
    apiVersion: MODULE_API_VERSION,
    module: Object.freeze({
      register: registerModule,
      context: createModuleContext,
      list: function () {
        return Array.from(modules.values()).map(function (item) {
          return safeData(item);
        });
      },
    }),
    status: Object.freeze({
      set: setStatus,
      clear: clearStatus,
      subscribe: subscribeStatus,
      snapshot: getStatusSnapshot,
    }),
    trace: Object.freeze({
      begin: beginTrace,
    }),
    revocation: Object.freeze({
      revoke: revoke,
      isRevoked: isRevoked,
      revokeAll: revokeAll,
    }),
    security: Object.freeze({
      redact: safeData,
    }),
    scheduler: Object.freeze({
      defer: defer,
    }),
    ui: Object.freeze({
      button: createButton,
      actions: Object.freeze({ register: registerAction, dispatch: dispatchAction }),
      events: Object.freeze({ mountClicks: mountClickDispatcher }),
      modal: Object.freeze({ create: createModal, open: openModal, close: closeModal }),
    }),
  });

  setStatus("core", "running", "Ядро запускается");
})();
