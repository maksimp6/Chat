(function () {
  const IDENTITY_STORAGE_KEY = 'alice_web_installation_id';

  function getInstallationId() {
    let installationId = localStorage.getItem(IDENTITY_STORAGE_KEY);
    if (installationId) return installationId;

    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
      installationId = 'web-' + window.crypto.randomUUID().replace(/-/g, '');
    } else {
      installationId = 'web-' + Date.now().toString(36) + Math.random().toString(36).slice(2);
    }

    localStorage.setItem(IDENTITY_STORAGE_KEY, installationId);
    return installationId;
  }

  async function bootstrapIdentity() {
    const response = await fetch('/api/users/bootstrap', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      cache: 'no-store',
      body: JSON.stringify({
        installation_id: getInstallationId(),
        metadata: {platform: 'web'}
      })
    });

    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      // Keep the original bootstrap failure useful to the caller without
      // exposing response internals.
    }

    if (!response.ok) {
      throw new Error(data.error || 'Не удалось создать пользовательскую identity');
    }

    return data;
  }

  async function requestTreasury(path, options, fallbackMessage) {
    const requestOptions = Object.assign({credentials: 'same-origin'}, options || {});
    let response = await fetch(path, requestOptions);
    let data = {};

    try {
      data = await response.json();
    } catch (error) {
      data = {};
    }

    if (
      response.status === 401 &&
      (
        data.error === 'authenticated owner identity is required' ||
        data.error === 'invalid authenticated owner token'
      )
    ) {
      await bootstrapIdentity();

      response = await fetch(path, requestOptions);
      try {
        data = await response.json();
      } catch (error) {
        data = {};
      }
    }

    if (!response.ok) {
      throw new Error(data.error || fallbackMessage);
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

    treasuryModal = document.createElement('div');
    treasuryModal.id = 'treasury-modal';
    treasuryModal.className = 'modal treasury-modal';
    treasuryModal.setAttribute('role', 'dialog');
    treasuryModal.setAttribute('aria-modal', 'true');
    treasuryModal.setAttribute('aria-labelledby', 'treasury-modal-title');
    treasuryModal.setAttribute('aria-hidden', 'true');

    const content = document.createElement('div');
    content.className = 'modal-content treasury-modal-content';

    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'treasury-modal-close';
    closeButton.setAttribute('aria-label', 'Закрыть казначейство');
    closeButton.textContent = '×';
    closeButton.addEventListener('click', closeTreasuryModal);

    const title = document.createElement('h3');
    title.id = 'treasury-modal-title';
    title.textContent = 'Казначейство';

    const balanceCard = document.createElement('div');
    balanceCard.className = 'treasury-balance-card';

    const balanceLabel = document.createElement('span');
    balanceLabel.className = 'treasury-balance-label';
    balanceLabel.textContent = 'Текущий баланс';

    treasuryBalance = document.createElement('strong');
    treasuryBalance.className = 'treasury-balance-value';
    treasuryBalance.textContent = '…';

    balanceCard.append(balanceLabel, treasuryBalance);

    const topUpSection = document.createElement('section');
    topUpSection.className = 'treasury-action-section';

    const topUpTitle = document.createElement('h4');
    topUpTitle.textContent = 'Пополнение';

    const topUpRow = document.createElement('div');
    topUpRow.className = 'treasury-top-up-row';

    treasuryAmount = document.createElement('input');
    treasuryAmount.type = 'number';
    treasuryAmount.min = '0.01';
    treasuryAmount.step = '0.01';
    treasuryAmount.inputMode = 'decimal';
    treasuryAmount.placeholder = 'Сумма';
    treasuryAmount.className = 'treasury-amount-input';
    treasuryAmount.setAttribute('aria-label', 'Сумма DEMO-пополнения');

    const topUpButton = document.createElement('button');
    topUpButton.type = 'button';
    topUpButton.className = 'treasury-action-btn';
    topUpButton.textContent = 'Пополнить DEMO';
    topUpButton.addEventListener('click', performTreasuryTopUp);

    topUpRow.append(treasuryAmount, topUpButton);
    topUpSection.append(topUpTitle, topUpRow);

    const expensesSection = document.createElement('section');
    expensesSection.className = 'treasury-action-section';

    const expensesTitle = document.createElement('h4');
    expensesTitle.textContent = 'Расходы';

    treasuryExpenses = document.createElement('div');
    treasuryExpenses.className = 'treasury-expenses-list';
    treasuryExpenses.setAttribute('aria-live', 'polite');
    treasuryExpenses.textContent = 'Загрузка…';

    expensesSection.append(expensesTitle, treasuryExpenses);

    treasuryStatus = document.createElement('div');
    treasuryStatus.className = 'treasury-status';
    treasuryStatus.setAttribute('role', 'status');

    content.append(closeButton, title, balanceCard, topUpSection, expensesSection, treasuryStatus);
    treasuryModal.appendChild(content);
    treasuryModal.addEventListener('click', function (event) {
      if (event.target === treasuryModal) closeTreasuryModal();
    });

    document.querySelector('.alice-pro-app').appendChild(treasuryModal);
  }

  function setTreasuryStatus(message, isError) {
    treasuryStatus.textContent = message || '';
    treasuryStatus.classList.toggle('is-error', Boolean(isError));
  }

  function renderTreasuryAccount(account) {
    treasuryCurrency = account.currency || '';
    treasuryBalance.textContent = String(account.balance) + (treasuryCurrency ? ' ' + treasuryCurrency : '');

    const expenses = (account.ledger || []).filter(function (item) {
      return item.kind === 'debit';
    });

    treasuryExpenses.textContent = '';
    if (!expenses.length) {
      const empty = document.createElement('p');
      empty.className = 'treasury-empty-state';
      empty.textContent = 'Расходов пока нет.';
      treasuryExpenses.appendChild(empty);
      return;
    }

    const list = document.createElement('ul');
    list.className = 'treasury-expense-items';
    expenses.forEach(function (item) {
      const entry = document.createElement('li');
      entry.className = 'treasury-expense-item';

      const description = document.createElement('span');
      description.className = 'treasury-expense-description';
      description.textContent = item.description || 'Расход';

      const meta = document.createElement('span');
      meta.className = 'treasury-expense-meta';
      meta.textContent = (item.created_at || '') + ' · ' + item.amount + ' ' + treasuryCurrency;

      entry.append(description, meta);
      list.appendChild(entry);
    });
    treasuryExpenses.appendChild(list);
  }

  async function loadTreasuryAccount() {
    return requestTreasury(
      '/api/treasury/account',
      null,
      'Не удалось загрузить казначейство'
    );
  }

  async function performTreasuryTopUp() {
    const amount = Number(treasuryAmount.value);
    if (!Number.isFinite(amount) || amount <= 0) {
      setTreasuryStatus('Введите положительную сумму DEMO-пополнения.', true);
      treasuryAmount.focus();
      return;
    }

    setTreasuryStatus('Пополнение…');
    try {
      const data = await requestTreasury(
        '/api/treasury/top-up',
        {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({amount: amount})
        },
        'Ошибка пополнения'
      );
      treasuryAmount.value = '';
      renderTreasuryAccount(data.account);
      setTreasuryStatus('DEMO-пополнение выполнено.');
    } catch (error) {
      setTreasuryStatus(error.message, true);
    }
  }

  function closeTreasuryModal() {
    if (!treasuryModal) return;
    treasuryModal.classList.remove('visible');
    treasuryModal.setAttribute('aria-hidden', 'true');
    document.removeEventListener('keydown', treasuryModal._escapeHandler);
  }

  async function openTreasuryPanel() {
    ensureTreasuryModal();
    treasuryModal.classList.add('visible');
    treasuryModal.setAttribute('aria-hidden', 'false');
    setTreasuryStatus('Загрузка…');
    treasuryBalance.textContent = '…';
    treasuryExpenses.textContent = 'Загрузка…';
    treasuryAmount.focus();

    treasuryModal._escapeHandler = function (event) {
      if (event.key === 'Escape') closeTreasuryModal();
    };
    document.addEventListener('keydown', treasuryModal._escapeHandler);

    try {
      const account = await loadTreasuryAccount();
      renderTreasuryAccount(account);
      setTreasuryStatus('');
    } catch (error) {
      setTreasuryStatus(error.message, true);
      treasuryExpenses.textContent = 'Не удалось загрузить расходы.';
    }
  }

  window.openTreasuryPanel = openTreasuryPanel;
})();
