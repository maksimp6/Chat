"use strict";

// Settings persistence and the settings window on the BrowserShim: real settings modules,
// mocked dispatcher responses.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

const SOURCES = [
  "static/core_api.js",
  "static/settings/ui_helpers.js",
  "static/settings/settings_storage.js",
  "static/settings/settings_modal.js",
];

function boot({ html = "<body></body>", status = 200, extra = {} } = {}) {
  const requests = [];
  const dispatcher = {
    request: (url, options = {}) => {
      requests.push({ url, method: options.method || "GET" });
      return Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        json: async () => ({}),
      });
    },
  };
  const shim = new BrowserShim(html);
  const { window, document } = shim.load(SOURCES, {
    LogosDispatcher: dispatcher,
    LogosTheme: { getStored: () => "light", themes: { light: "Светлая" } },
    confirm: () => true,
    ...extra,
  });
  return { window, document, requests };
}

async function settle() {
  for (let i = 0; i < 10; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

test("a global save is what the next load returns, without a reload", () => {
  const { window } = boot();
  const storage = window.SettingsStorage;
  storage.load(null);
  storage.save({ ...storage.load(null), temperature: 0.1 }, null);
  assert.equal(storage.load(null).temperature, 0.1);
});

test("a server rejection of a conversation save is reported to the caller", async () => {
  const { window } = boot({ status: 500 });
  const storage = window.SettingsStorage;
  const outcome = storage.save({ ...storage.load("c1") }, "c1").then(
    () => "saved",
    () => "failed",
  );
  assert.equal(await outcome, "failed");
});

test("a zero temperature typed into the settings window is kept, not replaced by the default", async () => {
  const { window, document } = boot();
  window.openSettingsModal();
  await settle();

  document.getElementById("set-temp").value = "0";
  document.getElementById("set-save-btn").click();
  await settle();

  assert.equal(window.SettingsStorage.load(null).temperature, 0);
});

test("a stored zero is shown in its field, not as an empty box", async () => {
  const { window, document } = boot();
  window.SettingsStorage.save({ ...window.SettingsStorage.load(null), temperature: 0 }, null);
  window.openSettingsModal();
  await settle();

  assert.equal(document.getElementById("set-temp").value, "0");
});

test("a failed settings save keeps the window open and says so", async () => {
  const { window, document } = boot({ status: 500, extra: { currentConvId: "c1" } });
  window.openSettingsModal();
  await settle();

  document.getElementById("set-save-btn").click();
  await settle();

  assert.notEqual(document.getElementById("settings-modal-custom"), null);
  assert.match(document.getElementById("set-status").textContent, /Не удалось/);
});

test("a failed tools save keeps the tools window open and says so", async () => {
  const { window, document } = boot({ status: 500, extra: { currentConvId: "c1" } });
  window.openToolsModal();
  await settle();

  document.getElementById("tools-save-btn").click();
  await settle();

  assert.notEqual(document.getElementById("tools-modal-custom"), null);
  assert.match(document.getElementById("tools-status").textContent, /Не удалось/);
});
