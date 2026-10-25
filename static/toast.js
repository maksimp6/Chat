(function () {
  "use strict";

  var HIDE_AFTER_MS = 4000;
  var REMOVE_AFTER_MS = 300;
  var stack = null;

  function ensureStack() {
    if (stack && stack.isConnected) return stack;
    stack = document.createElement("div");
    stack.className = "logos-toast-stack";
    document.body.appendChild(stack);
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
    setTimeout(function () {
      item.classList.add("logos-toast-hide");
      setTimeout(function () {
        item.remove();
      }, REMOVE_AFTER_MS);
    }, HIDE_AFTER_MS);
    return item;
  }

  window.LogosToast = { show: show };
})();
