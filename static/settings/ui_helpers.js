(function () {
  "use strict";

  // WIKIFUNCTIONS-CANDIDATE WF-16: https://www.wikifunctions.org/wiki/Z12316.
  // Собрать escape через четыре regex replacements в порядке & < > "; сохранить
  // null/undefined -> "", String(str), g и НЕ экранировать апостроф (generic HTML escape не подходит).
  function escapeHtml(str) {
    // 0 and false are real values; only a missing value renders empty.
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  // Keep the public HTML-string contract while letting the DOM serialize values and attributes.
  // Markup arguments are composed by settings code; user/provider text goes through textContent
  // or setAttribute before serialization.
  function element(tag, className, attrs) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    Object.keys(attrs || {}).forEach(function (name) {
      var value = attrs[name];
      if (value !== false && value !== null && value !== undefined) {
        node.setAttribute(name, value === true ? "" : String(value));
      }
    });
    return node;
  }

  function markup(node, html) {
    if (html !== null && html !== undefined) node.innerHTML = String(html);
    return node;
  }

  function inp(id, type, val, extra) {
    var node = element("input", "settings-control", extra);
    node.id = String(id);
    node.type = String(type);
    node.setAttribute("value", val === null || val === undefined ? "" : String(val));
    return node.outerHTML;
  }

  function sel(id, options, extra) {
    var node = element("select", "settings-control", extra);
    node.id = String(id);
    markup(node, options);
    return node.outerHTML;
  }

  function lbl(text, hint) {
    var node = element("label", "settings-label");
    if (hint) node.title = String(hint);
    return markup(node, text).outerHTML;
  }

  function chk(id, checked, text, extra, hint) {
    var label = element("label", "settings-check");
    if (hint) label.title = String(hint);
    var input = element("input", "", extra);
    input.id = String(id);
    input.type = "checkbox";
    if (checked) input.setAttribute("checked", "");
    label.appendChild(input);
    label.appendChild(document.createTextNode(" "));
    var caption = markup(document.createElement("span"), text);
    label.appendChild(caption);
    return label.outerHTML;
  }

  function gap2(c1, c2) {
    return markup(element("div", "settings-grid-2"), String(c1 || "") + String(c2 || "")).outerHTML;
  }

  function gap3(c1, c2, c3) {
    return markup(element("div", "settings-grid-3"), String(c1 || "") + String(c2 || "") + String(c3 || "")).outerHTML;
  }

  function section(title) {
    return markup(element("h3", "settings-section"), title).outerHTML;
  }

  function ta(id, val, extra) {
    var node = element("textarea", "settings-control");
    if (extra) node.style.cssText = String(extra);
    node.id = String(id);
    node.textContent = val === null || val === undefined ? "" : String(val);
    return node.outerHTML;
  }

  function deepMerge(target, source) {
    for (var key in source) {
      if (!Object.prototype.hasOwnProperty.call(source, key)) continue;
      if (key === "__proto__" || key === "prototype" || key === "constructor") continue;

      if (source[key] && typeof source[key] === "object" && !Array.isArray(source[key])) {
        var current =
          Object.prototype.hasOwnProperty.call(target, key) &&
          target[key] &&
          typeof target[key] === "object" &&
          !Array.isArray(target[key])
            ? target[key]
            : {};
        target[key] = deepMerge(current, source[key]);
      } else {
        target[key] = source[key];
      }
    }
    return target;
  }

  window.SettingsUI = {
    escapeHtml: escapeHtml,
    inp: inp,
    sel: sel,
    lbl: lbl,
    chk: chk,
    gap2: gap2,
    gap3: gap3,
    section: section,
    ta: ta,
    deepMerge: deepMerge,
  };
})();

// Confirm and prompt drawn in the page instead of window.confirm/prompt: an Android WebView without a
// dialog handler may show nothing and answer «no». Both return a Promise: confirm resolves true or false,
// prompt resolves the entered text or null. «Отмена» and Escape cancel; Enter or the main button accepts.
// Shared by every window (sidebar, files, memory, environments, settings); it needs neither the core API
// nor SettingsUI.
(function () {
  "use strict";

  var dialogCount = 0;

  function ask(kind, message, options) {
    var settings = options || {};
    return new Promise(function (resolve) {
      dialogCount += 1;
      var id = "logos-dialog-" + dialogCount;
      var previousFocus = document.activeElement;

      var dialog = document.createElement("dialog");
      dialog.id = id;
      dialog.className = "modal logos-dialog";
      dialog.setAttribute("role", kind === "confirm" ? "alertdialog" : "dialog");
      dialog.setAttribute("aria-modal", "true");
      dialog.setAttribute("aria-labelledby", id + "-message");

      var form = document.createElement("form");
      form.className = "modal-content logos-dialog-content";
      form.noValidate = true;

      var text = document.createElement("p");
      text.id = id + "-message";
      text.className = "logos-dialog-message";
      text.textContent = message == null ? "" : String(message);
      form.appendChild(text);

      var field = null;
      if (kind === "prompt") {
        field = document.createElement("input");
        field.type = "text";
        field.className = "logos-dialog-input";
        field.autocomplete = "off";
        field.setAttribute("enterkeyhint", "done");
        field.setAttribute("aria-labelledby", text.id);
        field.value = settings.value == null ? "" : String(settings.value);
        form.appendChild(field);
      }

      var actions = document.createElement("div");
      actions.className = "logos-dialog-actions";
      var cancelButton = document.createElement("button");
      cancelButton.type = "button";
      cancelButton.className = "logos-btn logos-dialog-cancel";
      cancelButton.textContent = settings.cancelText || "Отмена";
      var acceptButton = document.createElement("button");
      acceptButton.type = "submit";
      acceptButton.className =
        "logos-btn logos-dialog-accept " + (settings.danger ? "settings-btn-danger" : "settings-btn-accent");
      acceptButton.textContent =
        settings.confirmText || (kind === "prompt" ? "Сохранить" : "Подтвердить");
      actions.append(cancelButton, acceptButton);
      form.appendChild(actions);
      dialog.appendChild(form);

      var finished = false;
      function finish(accepted) {
        if (finished) return;
        finished = true;
        if (dialog.open) dialog.close();
        dialog.remove();
        if (previousFocus && previousFocus.isConnected && typeof previousFocus.focus === "function") {
          previousFocus.focus();
        }
        if (kind === "prompt") resolve(accepted ? field.value : null);
        else resolve(accepted);
      }

      form.addEventListener("submit", function (event) {
        event.preventDefault();
        finish(true);
      });
      cancelButton.addEventListener("click", function () {
        finish(false);
      });
      // Escape (and the system «back» that closes a dialog) cancels; the window underneath stays open.
      dialog.addEventListener("cancel", function (event) {
        event.preventDefault();
        finish(false);
      });
      dialog.addEventListener("keydown", function (event) {
        if (event.key !== "Escape" || event.isComposing) return;
        event.preventDefault();
        event.stopPropagation();
        finish(false);
      });
      // A click on the dimmed area around the question cancels it, as Escape does. A press that starts on the card
      // and ends outside it (selecting the typed text) is not such a click.
      // While the prompt's field has focus, that tap only leaves the field (a phone hides its keyboard this way), so
      // the typed text is not lost; the next tap cancels.
      var pressedBackdrop = false;
      var fieldAtPress = false;
      dialog.addEventListener(typeof window.PointerEvent === "function" ? "pointerdown" : "mousedown", function (event) {
        pressedBackdrop = event.target === dialog;
        fieldAtPress = pressedBackdrop && !!field && document.activeElement === field;
      });
      dialog.addEventListener("click", function (event) {
        var fromBackdrop = event.target === dialog && pressedBackdrop;
        var leaveField = fieldAtPress;
        pressedBackdrop = false;
        fieldAtPress = false;
        if (!fromBackdrop) return;
        if (leaveField) {
          if (document.activeElement === field) field.blur();
          return;
        }
        finish(false);
      });

      (document.querySelector(".logos-pro-app") || document.body).appendChild(dialog);
      dialog.classList.add("visible");
      try {
        dialog.showModal();
      } catch (error) {
        dialog.remove();
        throw error;
      }
      // A destructive action starts on «Отмена», so a stray Enter does not delete anything.
      if (field) {
        field.focus();
        field.select();
      } else {
        (settings.danger ? cancelButton : acceptButton).focus();
      }
    });
  }

  window.LogosDialog = Object.freeze({
    // options: confirmText, cancelText, danger (red main button, focus starts on «Отмена»).
    confirm: function (message, options) {
      return ask("confirm", message, options);
    },
    // options: value (initial text), confirmText, cancelText.
    prompt: function (message, options) {
      return ask("prompt", message, options);
    },
  });
})();
