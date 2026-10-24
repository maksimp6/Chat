"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

async function boot(settings) {
  const saves = [];
  const shim = new BrowserShim("<body></body>");
  const browser = shim.load(
    ["static/core_api.js", "static/settings/ui_helpers.js", "static/settings/ssh_runtime_modal.js"],
    {
      LogosDispatcher: {
        request: async (_url, init = {}) => {
          if (init.method === "PUT") {
            const payload = JSON.parse(init.body);
            saves.push(payload);
            return { ok: true, json: async () => ({ settings: payload }) };
          }
          return { ok: true, json: async () => settings };
        },
      },
    },
  );
  browser.window.openSshRuntimeModal();
  await new Promise((resolve) => setTimeout(resolve, 0));
  return { ...browser, saves };
}

test("inactive SSH policy controls stay in a disabled fieldset after loading", async () => {
  const { document } = await boot({ enabled: true, read_only: false });
  assert.equal(document.getElementById("ssh-settings-status").textContent, "Конфигурация загружена.");
  const fieldset = document.getElementById("ssh-inactive-restrictions");
  assert.equal(fieldset.hasAttribute("disabled"), true);
  assert.match(fieldset.textContent, /Ограничения отключены/);
  assert.match(fieldset.textContent, /Подтверждения операций не запрашиваются/);
  assert.doesNotMatch(fieldset.textContent, /Опасные операции остаются под подтверждением/);
  for (const id of [
    "set-ssh-command-allowlist",
    "set-ssh-allow-privileged",
    "set-ssh-approval-write",
    "set-ssh-approval-privileged",
  ]) {
    assert.equal(document.getElementById(id).closest("fieldset"), fieldset);
  }
  for (const id of [
    "set-ssh-enabled", "set-ssh-readonly", "set-ssh-allow-exec", "set-ssh-allow-write",
  ]) {
    const control = document.getElementById(id);
    assert.equal(control.closest("fieldset"), null);
    assert.equal(Boolean(control.disabled), false);
  }
});

test("saving active SSH settings preserves the loaded inactive legacy values", async () => {
  const legacy = {
    command_allowlist: ["^ls$", "^cat /srv/"],
    allow_privileged_operations: true,
    approval_required_for_write: false,
    approval_required_for_privileged: true,
  };
  const { document, saves } = await boot({ ...legacy, enabled: false, read_only: false });
  document.getElementById("set-ssh-enabled").checked = true;
  document.getElementById("set-ssh-save-btn").click();
  await Promise.resolve();
  assert.equal(saves.length, 1);
  assert.equal(saves[0].enabled, true);
  for (const [key, value] of Object.entries(legacy)) assert.deepEqual(saves[0][key], value);
  assert.equal(document.getElementById("ssh-inactive-restrictions").hasAttribute("disabled"), true);
});
