"use strict";

// Treasury panel on the BrowserShim: real static/treasury.js with mocked dispatcher responses.
// The page has no .logos-pro-app root here on purpose: the panel must still open.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

const ACCOUNT = { balance: "5.00", currency: "DEMO", ledger: [] };
const TOPPED_UP = { balance: "10.00", currency: "DEMO", ledger: [] };

function boot({ topUp = { body: { account: TOPPED_UP } } } = {}) {
  const requests = [];
  const dispatcher = {
    request: (url, options = {}) => {
      const method = options.method || "GET";
      requests.push({ url, method, body: options.body ? JSON.parse(options.body) : null });
      if (url === "/api/treasury/account") {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ ...ACCOUNT }) });
      }
      if (url === "/api/treasury/top-up") {
        const status = topUp.status || 200;
        return new Promise((resolve) => {
          setTimeout(
            () => resolve({ ok: status < 300, status, json: async () => topUp.body }),
            0,
          );
        });
      }
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
    },
  };
  const shim = new BrowserShim("<body></body>");
  const { window, document } = shim.load(["static/core_api.js", "static/treasury.js"], {
    LogosDispatcher: dispatcher,
  });
  window.LogosCoreAPI.ui.events.mountClicks(document.body);
  return { window, document, requests };
}

async function settle() {
  for (let i = 0; i < 12; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

async function openPanel(window, document) {
  window.openTreasuryPanel();
  await settle();
  document.querySelector(".treasury-amount-input").value = "5";
}

function topUpButton(document) {
  return document.querySelector(".treasury-action-btn");
}

test("the panel opens and shows the current balance without an app root", async () => {
  const { window, document } = boot();
  await openPanel(window, document);

  assert.equal(document.getElementById("treasury-modal").hidden, false);
  assert.equal(document.querySelector(".treasury-balance-value").textContent, "5.00 DEMO");
});

test("a double click on top-up credits the amount once", async () => {
  const { window, document, requests } = boot();
  await openPanel(window, document);

  topUpButton(document).click();
  topUpButton(document).click();
  await settle();

  const topUps = requests.filter((request) => request.url === "/api/treasury/top-up");
  assert.equal(topUps.length, 1);
  assert.deepEqual(topUps[0].body, { amount: 5 });
});

test("a zero amount is refused without a request", async () => {
  const { window, document, requests } = boot();
  await openPanel(window, document);

  document.querySelector(".treasury-amount-input").value = "0";
  topUpButton(document).click();
  await settle();

  assert.equal(requests.some((request) => request.url === "/api/treasury/top-up"), false);
  assert.match(document.querySelector(".treasury-status").textContent, /положительную/);
});

test("a rejected top-up shows a localized HTTP error and leaves the balance unchanged", async () => {
  const { window, document } = boot({
    topUp: { status: 409, body: { error: "treasury is locked" } },
  });
  await openPanel(window, document);

  topUpButton(document).click();
  await settle();

  assert.equal(document.querySelector(".treasury-status").textContent, "Ошибка пополнения (HTTP 409)");
  assert.equal(document.querySelector(".treasury-balance-value").textContent, "5.00 DEMO");
});

test("a successful top-up shows the new balance", async () => {
  const { window, document } = boot();
  await openPanel(window, document);

  topUpButton(document).click();
  await settle();

  assert.equal(document.querySelector(".treasury-balance-value").textContent, "10.00 DEMO");
  assert.equal(document.querySelector(".treasury-amount-input").value, "");
});

test("Escape closes the panel", async () => {
  const { window, document } = boot();
  await openPanel(window, document);

  document.dispatchEvent({ type: "keydown", key: "Escape" });

  assert.equal(document.getElementById("treasury-modal").hidden, true);
});
