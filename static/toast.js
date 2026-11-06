(function () {
  "use strict";

  var HIDE_AFTER_MS = 4000;
  // Errors stay long enough to read and can be dismissed by a click.
  var ERROR_HIDE_AFTER_MS = 8000;
  var stack = null;
  var watcher = new MutationObserver(placeStack);

  // A window is a showModal() dialog, in the browser's top layer above any z-index, and the page behind it is inert. So
  // the stack lives in the top-most open window: on <body> a failure inside a window would show behind it. A window that
  // closes or leaves the page hands its messages on; the close event would miss a window removed at once.
  function placeStack() {
    if (!stack) return;
    var open = document.querySelectorAll("dialog[open]");
    var host = open.length ? open[open.length - 1] : document.body;
    if (stack.parentNode !== host) host.appendChild(stack);
    watcher.disconnect();
    if (host === document.body) return;
    watcher.observe(host, { attributes: true, attributeFilter: ["open"] });
    watcher.observe(host.parentNode, { childList: true });
  }

  function ensureStack() {
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "logos-toast-stack";
    }
    placeStack();
    return stack;
  }

  // Shows a short, non-blocking message. Text is set with textContent, so server
  // error text is never parsed as HTML.
  function show(message, options) {
    var isError = !!(options && options.type === "error");
    var item = document.createElement("div");
    item.className = "logos-toast" + (isError ? " logos-toast-error" : "");
    item.setAttribute("role", isError ? "alert" : "status");
    item.textContent = message == null ? "" : String(message);
    ensureStack().appendChild(item);
    item.addEventListener("click", function () {
      item.remove();
    });
    setTimeout(function () {
      item.remove();
    }, isError ? ERROR_HIDE_AFTER_MS : HIDE_AFTER_MS);
    return item;
  }

  window.LogosToast = {
    show: show,
    error: function (message) {
      return show(message, { type: "error" });
    },
  };
})();
