(function () {
  async function openTreasuryPanel() {
    let account;
    try {
      const response = await fetch('/api/treasury/account');
      account = await response.json();
      if (!response.ok) throw new Error(account.error || 'Не удалось загрузить баланс');
    } catch (error) { alert(error.message); return; }
    const amount = prompt(`Баланс: ${account.balance} ${account.currency}\nВведите сумму DEMO-пополнения или отмените:`);
    if (amount === null) return;
    try {
      const response = await fetch('/api/treasury/top-up', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({amount: amount}) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Ошибка пополнения');
      alert(`DEMO-пополнение выполнено. Баланс: ${data.account.balance} ${data.account.currency}`);
    } catch (error) { alert(error.message); }
  }
  window.openTreasuryPanel = openTreasuryPanel;
  window.openExpensesPanel = async function () {
    try {
      const response = await fetch('/api/treasury/account');
      const account = await response.json();
      if (!response.ok) throw new Error(account.error || 'Ошибка загрузки расходов');
      const expenses = (account.ledger || []).filter(item => item.kind === 'debit');
      const text = expenses.length ? expenses.map(item => `${item.created_at}: ${item.amount} ${account.currency}, ${item.description}`).join('\n') : 'Расходов пока нет.';
      alert(`Расходы\n\n${text}\n\nБаланс: ${account.balance} ${account.currency}`);
    } catch (error) { alert(error.message); }
  };
})();
