"use strict";

// Request parameters built from conversation settings, on the BrowserShim.

const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

function boot(settings) {
  const shim = new BrowserShim('<div id="root"></div>');
  const { window } = shim.load(
    ["static/settings/ui_helpers.js", "static/settings/settings_storage.js", "static/settings/settings_params.js"],
    {
      currentConvId: "c1",
      LogosDispatcher: { request: () => Promise.resolve({ ok: true, status: 200, json: async () => ({}) }) },
    },
  );
  window.SettingsStorage.save(settings, "c1");
  return window.getResponsesParams();
}

test("a JSON schema is sent as a json_schema object, not as text", () => {
  const params = boot({
    text_format: "json",
    json_schema_name: "answer",
    json_schema: '{"type":"object","properties":{"ok":{"type":"boolean"}}}',
  });
  // The object comes from the VM context, so compare its JSON shape across realms.
  assert.deepEqual(JSON.parse(JSON.stringify(params.text)), {
    format: {
      type: "json_schema",
      name: "answer",
      schema: { type: "object", properties: { ok: { type: "boolean" } } },
    },
  });
});

test("an invalid JSON schema is not sent instead of being sent as a string", () => {
  const params = boot({ text_format: "json", json_schema: "{not json" });
  assert.equal(params.text, undefined);
});

test("a numeric previous_response_id no longer crashes the request builder", () => {
  const params = boot({ previous_response_id: 42 });
  assert.equal(params.previous_response_id, "42");
});
