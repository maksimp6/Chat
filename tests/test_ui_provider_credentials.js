"use strict";

// Provider key modal on the BrowserShim: real static/provider_credentials.js,
// mocked dispatcher responses.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

const STATUS = {
  providers: [
    {
      provider: "yandex",
      status: "configured",
      credential: { fingerprint: "abcdef1234567890", expires_at: null },
    },
    { provider: "cloudru", status: "not_configured", credential: { fingerprint: null } },
  ],
};

function reply(status, body) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

function boot(responses = []) {
  const requests = [];
  const queue = [...responses];
  const dispatcher = {
    request: (url, options = {}) => {
      const body = options.body ? JSON.parse(options.body) : null;
      requests.push({ url, method: options.method || "GET", body });
      const next = queue.shift();
      if (!next) return reply(500, { error: "no mocked response for " + url });
      return reply(next.status || 200, next.body);
    },
  };
  const shim = new BrowserShim("<body></body>");
  const { document, window } = shim.load(
    ["static/core_api.js", "static/provider_credentials.js"],
    { LogosDispatcher: dispatcher },
  );
  return { document, window, requests };
}

async function settle() {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

function fieldError(document, inputId) {
  const wrap = document.getElementById(inputId).parentNode;
  return wrap.querySelector(".provider-field-error");
}

function errorText(document, inputId) {
  const error = fieldError(document, inputId);
  return error.classList.contains("is-visible") ? error.textContent : "";
}

function outputText(document) {
  return document.getElementById("provider-credentials-status").textContent;
}

function saveButton(document) {
  return document.querySelector(".provider-credentials-actions button");
}

function writes(requests) {
  return requests.filter((request) => request.method !== "GET");
}

test("opening the modal shows the stored state of each provider", async () => {
  const { document, window, requests } = boot([{ body: STATUS }]);
  window.openProviderCredentialsModal();
  await settle();

  const modal = document.getElementById("provider-credentials-modal");
  assert.equal(modal.hidden, false);
  assert.equal(modal.classList.contains("visible"), true);
  assert.equal(requests[0].url, "/api/provider-credentials/status");
  assert.deepEqual(
    Array.from(document.querySelectorAll(".provider-status-line")).map((node) => node.textContent),
    ["Настроен • ключ сохранён", "Не настроен"],
  );
});

test("a failed status load is reported inside the modal", async () => {
  const { document, window } = boot([{ status: 401, body: { error: "unauthorized" } }]);
  window.openProviderCredentialsModal();
  await settle();

  assert.equal(outputText(document), "Ошибка проверки: сервер вернул ошибку 401");
});

test("submitting without any key shows a field error and sends nothing", async () => {
  const { document, window, requests } = boot([{ body: STATUS }]);
  window.openProviderCredentialsModal();
  await settle();

  saveButton(document).click();
  await settle();

  assert.deepEqual(writes(requests), []);
  assert.equal(errorText(document, "provider-yandex-key"), "Введите API key.");
  assert.equal(errorText(document, "provider-cloudru-key"), "Введите API key.");
});

test("a Yandex key without a project id is rejected before any request", async () => {
  const { document, window, requests } = boot([{ body: STATUS }]);
  window.openProviderCredentialsModal();
  await settle();

  document.getElementById("provider-yandex-key").value = "AQVN-secret";
  saveButton(document).click();
  await settle();

  assert.deepEqual(writes(requests), []);
  assert.equal(
    errorText(document, "provider-yandex-project"),
    "Project ID обязателен вместе с Yandex API key.",
  );
  assert.equal(document.getElementById("provider-yandex-key").value, "AQVN-secret");
});

test("a valid Yandex key is sent once, cleared from the form, and the status refreshes", async () => {
  const { document, window, requests } = boot([{ body: STATUS }, { body: STATUS }, { body: STATUS }]);
  window.openProviderCredentialsModal();
  await settle();

  document.getElementById("provider-yandex-key").value = "AQVN-secret";
  document.getElementById("provider-yandex-project").value = "b1g1fekh2198nuan1tnh";
  saveButton(document).click();
  await settle();

  const sent = writes(requests);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].url, "/api/provider-credentials");
  assert.equal(sent[0].method, "PUT");
  assert.deepEqual(sent[0].body, {
    yandex_api_key: "AQVN-secret",
    yandex_project_id: "b1g1fekh2198nuan1tnh",
  });
  assert.equal(document.getElementById("provider-yandex-key").value, "");
  assert.equal(document.getElementById("provider-yandex-project").value, "");
  assert.equal(saveButton(document).disabled, false);
  assert.equal(requests[requests.length - 1].url, "/api/provider-credentials/status");
});

test("a rejected Cloud.ru key is reported on its field and the typed key is kept for a retry", async () => {
  const { document, window } = boot([
    { body: STATUS },
    { status: 401, body: { error: "authorization_failed", provider: "cloudru", status: "invalid" } },
  ]);
  window.openProviderCredentialsModal();
  await settle();

  document.getElementById("provider-cloudru-key").value = "bad-key";
  saveButton(document).click();
  await settle();

  assert.equal(
    errorText(document, "provider-cloudru-key"),
    "Неверный Cloud.ru API key или ключ не имеет доступа к Foundation Models.",
  );
  assert.equal(document.getElementById("provider-cloudru-key").value, "bad-key");
});

test("a provider outage is reported on the Yandex field that failed", async () => {
  const { document, window } = boot([
    { body: STATUS },
    { status: 502, body: { error: "provider_health_check_failed", provider: "yandex" } },
  ]);
  window.openProviderCredentialsModal();
  await settle();

  document.getElementById("provider-yandex-key").value = "AQVN-secret";
  document.getElementById("provider-yandex-project").value = "b1g1fekh2198nuan1tnh";
  saveButton(document).click();
  await settle();

  assert.equal(
    errorText(document, "provider-yandex-key"),
    "Не удалось проверить Yandex Cloud API key: сервис недоступен или вернул ошибку.",
  );
});

test("a storage failure after a valid check is reported in the modal output", async () => {
  const { document, window } = boot([
    { body: STATUS },
    { status: 503, body: { error: "credential_storage_failed" } },
  ]);
  window.openProviderCredentialsModal();
  await settle();

  document.getElementById("provider-cloudru-key").value = "good-key";
  saveButton(document).click();
  await settle();

  assert.equal(outputText(document), "Ключ проверен, но не удалось сохранить его на сервере.");
});

test("closing the modal clears any unsent key from the form", async () => {
  const { document, window } = boot([{ body: STATUS }]);
  window.LogosCoreAPI.ui.events.mountClicks(document.body);
  window.openProviderCredentialsModal();
  await settle();

  document.getElementById("provider-cloudru-key").value = "unsent-key";
  document.querySelector("#provider-credentials-modal .modal-close").click();

  const modal = document.getElementById("provider-credentials-modal");
  assert.equal(modal.hidden, true);
  assert.equal(document.getElementById("provider-cloudru-key").value, "");
});

test("each field's label is associated with its input", async () => {
  const { document, window } = boot([{ body: STATUS }]);
  window.openProviderCredentialsModal();
  await settle();

  const input = document.getElementById("provider-cloudru-key");
  const label = input.parentNode.querySelector(".provider-field-label");
  assert.equal(label.htmlFor, "provider-cloudru-key");
});
