(function () {
  "use strict";

  function injectModalStyles() {
    if (document.getElementById("settings-modal-theme")) return;
    var s = document.createElement("style");
    s.id = "settings-modal-theme";
    s.textContent = [
      ':root,[data-theme="light"],.theme-light{',
      "--m-bg:#fff;--m-text:#222;--m-border:#ddd;--m-input-bg:#f5f5f5;",
      "--m-input-text:#222;--m-section:#666;--m-section-border:#eee;",
      "--m-card:#fff;--m-editor:#f8f9fa;--m-muted:#6e6e73;",
      "--m-accent:#4a90d9;--m-danger:#c33;--m-success:#28a745;",
      "--m-overlay:rgba(0,0,0,0.6);--m-shadow:rgba(0,0,0,0.3)}",
      '[data-theme="dark"],.dark,.theme-dark,body.dark,html.dark{',
      "--m-bg:#1a1a2e;--m-text:#e0e0e0;--m-border:#3a3a4e;--m-input-bg:#2a2a3e;",
      "--m-input-text:#e0e0e0;--m-section:#888;--m-section-border:#2a2a3e;",
      "--m-card:#1e1e2e;--m-editor:#252535;--m-muted:#9a9aa3;",
      "--m-accent:#4a90d9;--m-danger:#e55;--m-success:#4caf50;",
      "--m-overlay:rgba(0,0,0,0.7);--m-shadow:rgba(0,0,0,0.5)}",
      '[data-theme="dim"]{',
      "--m-bg:#24292f;--m-text:#e6edf3;--m-border:#444c56;--m-input-bg:#2d333b;",
      "--m-input-text:#e6edf3;--m-section:#8b949e;--m-section-border:#444c56;",
      "--m-card:#24292f;--m-editor:#2d333b;--m-muted:#9ea7b3;",
      "--m-accent:#58a6ff;--m-danger:#f85149;--m-success:#3fb950;",
      "--m-overlay:rgba(0,0,0,0.7);--m-shadow:rgba(0,0,0,0.5)}",
      '[data-theme="high-contrast"]{',
      "--m-bg:#ffffff;--m-text:#000000;--m-border:#000000;--m-input-bg:#ffffff;",
      "--m-input-text:#000000;--m-section:#000000;--m-section-border:#000000;",
      "--m-card:#ffffff;--m-editor:#ffffff;--m-muted:#333333;",
      "--m-accent:#004c99;--m-danger:#b00020;--m-success:#1e7e34;",
      "--m-overlay:rgba(0,0,0,0.6);--m-shadow:rgba(0,0,0,0.3)}",
    ].join("");
    document.head.appendChild(s);
  }

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
  function element(tag, css, attrs) {
    var node = document.createElement(tag);
    if (css) node.style.cssText = css;
    Object.keys(attrs || {}).forEach(function (name) {
      var value = attrs[name];
      if (name === "style") {
        node.style.cssText += ";" + String(value || "");
      } else if (value !== false && value !== null && value !== undefined) {
        node.setAttribute(name, value === true ? "" : String(value));
      }
    });
    return node;
  }

  function markup(node, html) {
    if (html !== null && html !== undefined) node.innerHTML = String(html);
    return node;
  }

  var INPUT_STYLE = "width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--m-border,#ddd);border-radius:6px;font-size:13px;background:var(--m-input-bg,#f5f5f5);color:var(--m-input-text,#222);";

  function inp(id, type, val, extra) {
    var node = element("input", INPUT_STYLE, extra);
    node.id = String(id);
    node.type = String(type);
    node.setAttribute("value", val === null || val === undefined ? "" : String(val));
    return node.outerHTML;
  }

  function sel(id, options, extra) {
    var node = element("select", INPUT_STYLE, extra);
    node.id = String(id);
    markup(node, options);
    return node.outerHTML;
  }

  function lbl(text, hint) {
    var node = element("label", "font-size:13px;display:block;margin-bottom:4px;color:var(--m-text,#222);");
    if (hint) node.title = String(hint);
    return markup(node, text).outerHTML;
  }

  function chk(id, checked, text, extra, hint) {
    var label = element("label", "font-size:13px;display:flex;align-items:flex-start;gap:4px;color:var(--m-text,#222);");
    if (hint) label.title = String(hint);
    var input = element("input", "margin:2px 0 0;flex:0 0 auto;", extra);
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
    return markup(element("div", "display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px;align-items:end;"), String(c1 || "") + String(c2 || "")).outerHTML;
  }

  function gap3(c1, c2, c3) {
    return markup(element("div", "display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:10px;align-items:end;"), String(c1 || "") + String(c2 || "") + String(c3 || "")).outerHTML;
  }

  function section(title) {
    return markup(element("h3", "font-size:14px;margin:16px 0 8px;color:var(--m-section,#666);border-bottom:1px solid var(--m-section-border,#eee);padding-bottom:4px;"), title).outerHTML;
  }

  function toolSection(title, id, content) {
    var node = element("details", "margin-bottom:10px;border:1px solid var(--m-border,#ddd);border-radius:6px;padding:10px;background:var(--m-card,#fff);");
    node.appendChild(markup(element("summary", "cursor:pointer;font-weight:bold;color:var(--m-text,#222);"), title));
    var body = markup(element("div", "margin-top:10px;"), content);
    body.id = String(id);
    node.appendChild(body);
    return node.outerHTML;
  }

  function ta(id, val, extra) {
    var node = element("textarea", "width:100%;padding:8px;border:1px solid var(--m-border,#ddd);border-radius:6px;background:var(--m-input-bg,#f5f5f5);color:var(--m-input-text,#222);box-sizing:border-box;" + String(extra || ""));
    node.id = String(id);
    node.textContent = val === null || val === undefined ? "" : String(val);
    return node.outerHTML;
  }

  function btn(id, text, bg, extra) {
    var variant = bg === "accent" || bg === "success" || bg === "danger" ? bg : "default";
    var node = element("button");
    node.id = String(id);
    node.type = "button";
    node.className = "logos-btn settings-btn settings-btn-" + variant + (extra ? " settings-btn-extra" : "");
    return markup(node, text).outerHTML;
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
  // WIKIFUNCTIONS-CANDIDATE WF-18: https://www.wikifunctions.org/wiki/Z25614 + Z873 + Z872 + Z10079.
  // Заменить нормализатор списка композицией split(",") -> map(trim) -> filter(nonempty);
  // адаптер сохраняет falsy -> [], порядок и повторные значения, не добавляет dedup.
  function strToArr(str) {
    if (!str) return [];
    return str
      .split(",")
      .map(function (d) {
        return d.trim();
      })
      .filter(Boolean);
  }

  window.SettingsUI = {
    injectModalStyles: injectModalStyles,
    escapeHtml: escapeHtml,
    inp: inp,
    sel: sel,
    lbl: lbl,
    chk: chk,
    gap2: gap2,
    gap3: gap3,
    section: section,
    toolSection: toolSection,
    ta: ta,
    btn: btn,
    deepMerge: deepMerge,
    strToArr: strToArr,
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
