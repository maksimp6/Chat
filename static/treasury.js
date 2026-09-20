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

  async function openTreasuryPanel() {
    let account;
    try {
      account = await requestTreasury(
        '/api/treasury/account',
        null,
        'Не удалось загрузить баланс'
      );
    } catch (error) {
      alert(error.message);
      return;
    }

    const amount = prompt(
      'Баланс: ' + account.balance + ' ' + account.currency +
      '\nВведите сумму DEMO-пополнения или отмените:'
    );
    if (amount === null) return;

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
      alert(
        'DEMO-пополнение выполнено. Баланс: ' +
        data.account.balance + ' ' + data.account.currency
      );
    } catch (error) {
      alert(error.message);
    }
  }

  window.openTreasuryPanel = openTreasuryPanel;

  window.openExpensesPanel = async function () {
    try {
      const account = await requestTreasury(
        '/api/treasury/account',
        null,
        'Ошибка загрузки расходов'
      );

      const expenses = (account.ledger || []).filter(function (item) {
        return item.kind === 'debit';
      });
      const text = expenses.length
        ? expenses.map(function (item) {
            return item.created_at + ': ' + item.amount + ' ' +
              account.currency + ', ' + item.description;
          }).join('\n')
        : 'Расходов пока нет.';

      alert(
        'Расходы\n\n' + text + '\n\nБаланс: ' +
        account.balance + ' ' + account.currency
      );
    } catch (error) {
      alert(error.message);
    }
  };
})();
