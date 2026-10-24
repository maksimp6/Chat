(function () {
  "use strict";

  function renderRollback(previousTheme) {
    if (!previousTheme || !window.LogosTheme) return;
    var chatbox = document.getElementById("chatbox");
    if (!chatbox) return;

    var card = document.createElement("div");
    card.className = "msg bot";
    card.classList.add("theme-rollback-card");

    var button = document.createElement("button");
    button.type = "button";
    button.textContent = "↩ Вернуть предыдущую тему";
    button.className = "logos-btn theme-rollback-button";
    button.addEventListener("click", function () {
      window.LogosTheme.apply(previousTheme, true);
      button.disabled = true;
      button.textContent = "✓ Предыдущая тема восстановлена";
    });

    card.appendChild(button);
    chatbox.appendChild(card);
    chatbox.scrollTop = chatbox.scrollHeight;
  }

  window.applyThemeAssistantResult = function (executionResult) {
    var data = executionResult && executionResult.data;
    var action = data && data.frontend_action;
    if (!action || action.type !== "set_theme" || !window.LogosTheme) return false;

    var themes = window.LogosTheme.themes || {};
    if (!Object.prototype.hasOwnProperty.call(themes, action.theme)) return false;

    var previous = action.previous_theme || window.LogosTheme.getStored();
    window.LogosTheme.apply(action.theme, true);
    if (previous && previous !== action.theme) renderRollback(previous);
    return true;
  };
})();
