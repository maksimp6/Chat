// Copies text for the chat and the trace viewer. The Clipboard API exists only in secure contexts (https or
// localhost), and the phone reaches Logos over plain http on the LAN: there the copy command is used, which the
// browser allows only inside the tap that asked for it, so every path runs synchronously in that tap.
(function () {
  "use strict";

  // The copy command with the text handed to the copy event itself: no selection, focus or scrolling is involved.
  function copyThroughEvent(text) {
    var written = false;
    function onCopy(event) {
      if (!event.clipboardData) return;
      event.clipboardData.setData("text/plain", text);
      event.preventDefault();
      written = true;
    }
    document.addEventListener("copy", onCopy, true);
    var accepted = false;
    try {
      accepted = document.execCommand("copy");
    } catch (_) {
      accepted = false;
    }
    document.removeEventListener("copy", onCopy, true);
    return accepted && written;
  }

  // The copy command over a selected field, for a browser that gives the copy event no clipboard data.
  function copyThroughSelection(text) {
    var active = document.activeElement;
    var field = document.createElement("textarea");
    field.value = text;
    field.setAttribute("readonly", "");
    field.setAttribute("aria-hidden", "true");
    field.tabIndex = -1;
    // Inside the screen (a field outside it is not selected on some phones), invisible, and 16px so iOS does not zoom.
    field.style.cssText =
      "position:fixed;top:0;left:0;width:1px;height:1px;margin:0;padding:0;border:0;opacity:0;font-size:16px;";
    document.body.appendChild(field);
    field.focus({ preventScroll: true });
    field.select();
    field.setSelectionRange(0, field.value.length);
    var copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (_) {
      copied = false;
    }
    field.remove();
    if (active && active !== document.body && typeof active.focus === "function") active.focus({ preventScroll: true });
    return copied;
  }

  function copyWithCommand(text) {
    return copyThroughEvent(text) || copyThroughSelection(text)
      ? Promise.resolve()
      : Promise.reject(new Error("Браузер не дал доступа к буферу обмена"));
  }

  // Resolves once the text is in the clipboard; rejects when the browser refused every way.
  function copy(value) {
    var text = value === null || value === undefined ? "" : String(value);
    if (window.isSecureContext && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      return navigator.clipboard.writeText(text).catch(function () {
        return copyWithCommand(text);
      });
    }
    return copyWithCommand(text);
  }

  window.LogosClipboard = Object.freeze({ copy: copy });
})();
