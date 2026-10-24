"use strict";

// Conversation settings loading on the BrowserShim: real settings_storage.js.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

function boot(status, body) {
  const shim = new BrowserShim('<div id="root"></div>');
  return shim.load(["static/settings/ui_helpers.js", "static/settings/settings_storage.js"], {
    LogosDispatcher: {
      request: () => Promise.resolve({ ok: status < 400, status, json: async () => body }),
    },
  });
}

test("a denied settings request falls back to defaults and never merges the error body", async () => {
  const { window } = boot(404, { error: "conversation_not_found" });
  const settings = await window.loadServerConvSettings("c1");
  assert.equal("error" in settings, false);
  assert.deepEqual(settings, window.SettingsStorage.load("c1"));
});

test("server settings are merged over the defaults", async () => {
  const { window } = boot(200, { temperature: 0.3 });
  const settings = await window.loadServerConvSettings("c1");
  assert.equal(settings.temperature, 0.3);
  assert.equal("error" in settings, false);
});
