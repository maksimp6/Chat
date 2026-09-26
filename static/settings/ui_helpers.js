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
      "--m-card:#fff;--m-editor:#f8f9fa;--m-muted:#999;",
      "--m-accent:#4a90d9;--m-danger:#c33;--m-success:#28a745;",
      "--m-overlay:rgba(0,0,0,0.6);--m-shadow:rgba(0,0,0,0.3)}",
      '[data-theme="dark"],.dark,.theme-dark,body.dark,html.dark{',
      "--m-bg:#1a1a2e;--m-text:#e0e0e0;--m-border:#3a3a4e;--m-input-bg:#2a2a3e;",
      "--m-input-text:#e0e0e0;--m-section:#888;--m-section-border:#2a2a3e;",
      "--m-card:#1e1e2e;--m-editor:#252535;--m-muted:#666;",
      "--m-accent:#4a90d9;--m-danger:#e55;--m-success:#4caf50;",
      "--m-overlay:rgba(0,0,0,0.7);--m-shadow:rgba(0,0,0,0.5)}",
      "@media(prefers-color-scheme:dark){",
      ':root:not([data-theme="light"]):not(.theme-light){',
      "--m-bg:#1a1a2e;--m-text:#e0e0e0;--m-border:#3a3a4e;--m-input-bg:#2a2a3e;",
      "--m-input-text:#e0e0e0;--m-section:#888;--m-section-border:#2a2a3e;",
      "--m-card:#1e1e2e;--m-editor:#252535;--m-muted:#666;",
      "--m-accent:#4a90d9;--m-danger:#e55;--m-success:#4caf50;",
      "--m-overlay:rgba(0,0,0,0.7);--m-shadow:rgba(0,0,0,0.5)}}",
    ].join("");
    document.head.appendChild(s);
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function inp(id, type, val, extra) {
    return (
      '<input id="' +
      id +
      '" type="' +
      type +
      '" value="' +
      escapeHtml(val) +
      '" style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--m-border,#ddd);border-radius:6px;font-size:13px;background:var(--m-input-bg,#f5f5f5);color:var(--m-input-text,#222);"' +
      (extra || "") +
      ">"
    );
  }
  function sel(id, options, extra) {
    return (
      '<select id="' +
      id +
      '" style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--m-border,#ddd);border-radius:6px;font-size:13px;background:var(--m-input-bg,#f5f5f5);color:var(--m-input-text,#222);"' +
      (extra || "") +
      ">" +
      options +
      "</select>"
    );
  }
  function lbl(text, hint) {
    var t = hint ? ' title="' + escapeHtml(hint) + '"' : "";
    return (
      '<label style="font-size:13px;display:block;margin-bottom:4px;color:var(--m-text,#222);"' +
      t +
      ">" +
      text +
      "</label>"
    );
  }
  function chk(id, checked, text, extra, hint) {
    var t = hint ? ' title="' + escapeHtml(hint) + '"' : "";
    return (
      '<label style="font-size:13px;display:flex;align-items:center;gap:4px;color:var(--m-text,#222);"' +
      t +
      '><input id="' +
      id +
      '" type="checkbox"' +
      (checked ? " checked" : "") +
      (extra || "") +
      "> " +
      text +
      "</label>"
    );
  }
  function gap2(c1, c2) {
    return (
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px;">' +
      c1 +
      c2 +
      "</div>"
    );
  }
  function gap3(c1, c2, c3) {
    return (
      '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:10px;">' +
      c1 +
      c2 +
      c3 +
      "</div>"
    );
  }
  function section(title) {
    return (
      '<h3 style="font-size:14px;margin:16px 0 8px;color:var(--m-section,#666);border-bottom:1px solid var(--m-section-border,#eee);padding-bottom:4px;">' +
      title +
      "</h3>"
    );
  }
  function toolSection(title, id, content) {
    return (
      '<details style="margin-bottom:10px;border:1px solid var(--m-border,#ddd);border-radius:6px;padding:10px;background:var(--m-card,#fff);">' +
      '<summary style="cursor:pointer;font-weight:bold;color:var(--m-text,#222);">' +
      title +
      "</summary>" +
      '<div style="margin-top:10px;" id="' +
      id +
      '">' +
      content +
      "</div></details>"
    );
  }
  function ta(id, val, extra) {
    return (
      '<textarea id="' +
      id +
      '" style="width:100%;' +
      (extra || "") +
      'padding:8px;border:1px solid var(--m-border,#ddd);border-radius:6px;background:var(--m-input-bg,#f5f5f5);color:var(--m-input-text,#222);box-sizing:border-box;">' +
      escapeHtml(val) +
      "</textarea>"
    );
  }
  function btn(id, text, bg, extra) {
    var variant = bg === "accent" || bg === "success" || bg === "danger" ? bg : "default";
    var extraClass = extra ? " settings-btn-extra" : "";
    return (
      '<button class="alice-btn settings-btn settings-btn-' +
      variant +
      extraClass +
      '" id="' +
      id +
      '">' +
      text +
      "</button>"
    );
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
