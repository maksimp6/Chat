(function () {
  async function requestTreasury(path, options, fallbackMessage) {
    const requestOptions = Object.assign({ credentials: "same-origin" }, options || {});
    const response = await window.LogosDispatcher.request(path, requestOptions);
    let data = {};

    try {
      data = await response.json();
    } catch (error) {
      data = {};
    }

    if (!response.ok) {
      // Server text is English and may be a raw code; the user sees a Russian line with the HTTP status.
      throw new Error(fallbackMessage + " (HTTP " + response.status + ")");
    }

    return data;
  }

  let treasuryModal;
  let treasuryBalance;
  let treasuryCurrency;
  let treasuryExpenses;
  let treasuryAmount;
  let treasuryStatus;

  function ensureTreasuryModal() {
    if (treasuryModal) return;

    const UI = window.LogosCoreAPI.ui;
    const body = document.createElement("div");
    body.className = "treasury-modal-body";

    treasuryModal = UI.modal.create({
      id: "treasury-modal",
      title: "Казна",
      className: "treasury-modal",
      contentClassName: "treasury-modal-content",
      closeLabel: "Закрыть казну",
      closeAction: "treasury.close",
      body: body,
    });

    const balanceCard = document.createElement("div");
    balanceCard.className = "treasury-balance-card";

    const balanceLabel = document.createElement("span");
    balanceLabel.className = "treasury-balance-label";
    balanceLabel.textContent = "Текущий баланс";

    treasuryBalance = document.createElement("strong");
    treasuryBalance.className = "treasury-balance-value";
    treasuryBalance.textContent = "…";
    balanceCard.append(balanceLabel, treasuryBalance);

    const topUpSection = document.createElement("section");
    topUpSection.className = "treasury-action-section";
    const topUpTitle = document.createElement("h4");
    topUpTitle.textContent = "Пополнение";
    const topUpRow = document.createElement("div");
    topUpRow.className = "treasury-top-up-row";

    treasuryAmount = document.createElement("input");
    treasuryAmount.type = "number";
    treasuryAmount.min = "0.01";
    treasuryAmount.step = "0.01";
    treasuryAmount.inputMode = "decimal";
    treasuryAmount.placeholder = "Сумма";
    treasuryAmount.className = "treasury-amount-input";
    treasuryAmount.setAttribute("aria-label", "Сумма демо-пополнения");

    const topUpButton = UI.button({
      className: "treasury-action-btn btn-primary",
      text: "Пополнить демо",
      action: "treasury.top-up",
    });
    topUpRow.append(treasuryAmount, topUpButton);
    topUpSection.append(topUpTitle, topUpRow);

    const expensesSection = document.createElement("section");
    expensesSection.className = "treasury-action-section";
    const expensesTitle = document.createElement("h4");
    expensesTitle.textContent = "Расходы";
    treasuryExpenses = document.createElement("div");
    treasuryExpenses.className = "treasury-expenses-list";
    treasuryExpenses.setAttribute("aria-live", "polite");
    treasuryExpenses.textContent = "Загрузка…";
    expensesSection.append(expensesTitle, treasuryExpenses);

    treasuryStatus = document.createElement("div");
    treasuryStatus.className = "treasury-status";
    treasuryStatus.setAttribute("role", "status");

    body.append(balanceCard, topUpSection, expensesSection, treasuryStatus);
    (document.querySelector(".logos-pro-app") || document.body).appendChild(treasuryModal);
  }

  function setTreasuryStatus(message, isError) {
    treasuryStatus.textContent = message || "";
    treasuryStatus.classList.toggle("is-error", Boolean(isError));
  }

  // Up to 4 decimals: a model call costs a fraction of a kopeck, and rounding to 2 would show 0.
  function formatMoney(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    try {
      return new Intl.NumberFormat("ru-RU", {
        style: "currency",
        currency: treasuryCurrency,
        maximumFractionDigits: 4,
      }).format(number);
    } catch (error) {
      // A code Intl does not know (or none) is written as it came, after the number.
      return (
        number.toLocaleString("ru-RU", { maximumFractionDigits: 4 }) +
        (treasuryCurrency ? " " + treasuryCurrency : "")
      );
    }
  }

  // The server writes ISO time with microseconds; the standard Date format has three fraction digits (more is up to
  // each browser), so the rest is cut before parsing. Shown in the viewer's local time.
  function formatDate(value) {
    if (!value) return "";
    const date = new Date(String(value).replace(/(\.\d{3})\d+/, "$1"));
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function renderTreasuryAccount(account) {
    treasuryCurrency = account.currency || "";
    treasuryBalance.textContent = formatMoney(account.balance);

    const expenses = (account.ledger || []).filter(function (item) {
      return item.kind === "debit";
    });

    treasuryExpenses.textContent = "";
    if (!expenses.length) {
      const empty = document.createElement("p");
      empty.className = "treasury-empty-state";
      empty.textContent = "Расходов пока нет.";
      treasuryExpenses.appendChild(empty);
      return;
    }

    const list = document.createElement("ul");
    list.className = "treasury-expense-items";
    expenses.forEach(function (item) {
      const entry = document.createElement("li");
      entry.className = "treasury-expense-item";

      const description = document.createElement("span");
      description.className = "treasury-expense-description";
      description.textContent = item.description || "Расход";

      const amount = document.createElement("span");
      amount.className = "treasury-expense-amount";
      amount.textContent = formatMoney(item.amount);

      const meta = document.createElement("span");
      meta.className = "treasury-expense-meta";
      meta.textContent = formatDate(item.created_at);

      entry.append(description, amount, meta);
      list.appendChild(entry);
    });
    treasuryExpenses.appendChild(list);
  }

  async function loadTreasuryAccount() {
    return requestTreasury("/api/treasury/account", null, "Не удалось загрузить казну");
  }

  let topUpInFlight = false;

  async function performTreasuryTopUp(payload) {
    // One top-up at a time: a second click would credit the amount twice.
    if (topUpInFlight) return;
    const amount = Number(treasuryAmount.value);
    if (!Number.isFinite(amount) || amount <= 0) {
      setTreasuryStatus("Введите положительную сумму демо-пополнения.", true);
      treasuryAmount.focus();
      return;
    }

    const button = payload && payload.element;
    topUpInFlight = true;
    if (button) button.disabled = true;
    setTreasuryStatus("Пополнение…");
    try {
      const data = await requestTreasury(
        "/api/treasury/top-up",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ amount: amount }),
        },
        "Ошибка пополнения",
      );
      treasuryAmount.value = "";
      renderTreasuryAccount(data.account);
      setTreasuryStatus("Демо-пополнение выполнено.");
    } catch (error) {
      setTreasuryStatus(error.message, true);
    } finally {
      topUpInFlight = false;
      if (button) button.disabled = false;
    }
  }

  function closeTreasuryModal() {
    if (!treasuryModal) return;
    window.LogosCoreAPI.ui.modal.close(treasuryModal);
    document.removeEventListener("keydown", treasuryModal._escapeHandler);
  }

  async function openTreasuryPanel() {
    ensureTreasuryModal();
    window.LogosCoreAPI.ui.modal.open(treasuryModal);
    setTreasuryStatus("Загрузка…");
    treasuryBalance.textContent = "…";
    treasuryExpenses.textContent = "Загрузка…";
    treasuryAmount.focus();

    // Opening twice must not leave a second Escape listener behind.
    if (treasuryModal._escapeHandler) {
      document.removeEventListener("keydown", treasuryModal._escapeHandler);
    }
    treasuryModal._escapeHandler = function (event) {
      if (event.key === "Escape") closeTreasuryModal();
    };
    document.addEventListener("keydown", treasuryModal._escapeHandler);

    try {
      const account = await loadTreasuryAccount();
      renderTreasuryAccount(account);
      setTreasuryStatus("");
    } catch (error) {
      setTreasuryStatus(error.message, true);
      treasuryExpenses.textContent = "Не удалось загрузить расходы.";
    }
  }

  window.LogosCoreAPI.ui.actions.register("treasury.close", closeTreasuryModal);
  window.LogosCoreAPI.ui.actions.register("treasury.top-up", performTreasuryTopUp);

  window.openTreasuryPanel = openTreasuryPanel;
})();
