"use strict";

// Cloud.ru IAM key wizard on the BrowserShim: real static/cloudru_iam.js with mocked dispatcher
// responses. The module is not loaded by the page yet; these tests keep it correct for when it is.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

function boot() {
  const requests = [];
  const dispatcher = {
    request: (url, options = {}) => {
      requests.push({ url, method: options.method || "GET" });
      return new Promise(() => {});
    },
  };
  const shim = new BrowserShim("<body></body>");
  const { window, document } = shim.load(["static/core_api.js", "static/cloudru_iam.js"], {
    LogosDispatcher: dispatcher,
  });
  window.LogosCoreAPI.ui.events.mountClicks(document.body);
  return { window, document, requests };
}

test("closing the wizard clears the admin token and the issued secret", () => {
  const { document } = boot();
  document.getElementById("cloudru-iam-admin-token").value = "admin-token-value";
  document.getElementById("cloudru-iam-output").textContent = "KEY SECRET: issued-secret";

  document.querySelector("#cloudru-iam-modal .modal-close").click();

  assert.equal(document.getElementById("cloudru-iam-admin-token").value, "");
  assert.equal(document.getElementById("cloudru-iam-output").textContent, "");
});

test("a second click while a key is being issued does not issue a second key", async () => {
  const { document, requests } = boot();
  const submit = document.getElementById("cloudru-iam-submit");
  submit.click();
  submit.click();

  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/cloudru/iam/api-keys");
  assert.equal(submit.disabled, true);
});
