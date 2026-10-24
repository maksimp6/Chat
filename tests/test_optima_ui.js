"use strict";

// Offline interaction tests use the real settings and picker modules. The only
// network substitute is a settings store; no model requests are made.
const assert = require("node:assert/strict");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

const SOURCES = [
  "static/settings/ui_helpers.js",
  "static/settings/settings_storage.js",
  "static/settings/settings_params.js",
  "static/settings/settings_modal.js",
  "static/models.js",
];
const AVAILABLE = {
  name: "GPT OSS 20B",
  optima_profile: {
    profile_id: "optima-gpt-oss-20b",
    status: "available",
    transport: "responses",
    reasoning_effort: "medium",
  },
};

function boot({ server = {}, model = "gpt-oss-20b", modelInfo = AVAILABLE, status = 200 } = {}) {
  const requests = [];
  const shim = new BrowserShim(
    '<div class="logos-pro-app"><div id="model-list"></div><div id="model-modal"></div></div>',
  );
  const runtime = shim.load(SOURCES, {
    currentConvId: "c1",
    currentModel: model,
    modelsData: { text: { [model]: modelInfo }, voice: {} },
    LogosCoreAPI: { ui: { modal: { close() {} } } },
    LogosTheme: { getStored: () => "light", themes: { light: "Светлая" } },
    LogosDispatcher: {
      request(url, options = {}) {
        options = options || {};
        const match = url.match(/^\/api\/conversations\/([^/]+)\/settings$/);
        assert.ok(match, "Only settings persistence is allowed: " + url);
        requests.push({ url, method: options.method || "GET", body: options.body });
        if (options.method === "PUT" && status === 200) server[match[1]] = JSON.parse(options.body);
        return Promise.resolve({
          ok: status === 200,
          status,
          json: async () => JSON.parse(JSON.stringify(server[match[1]] || {})),
        });
      },
    },
    confirm: () => true,
    alert: () => { throw new Error("Unexpected alert"); },
  });
  return { ...runtime, server, requests };
}

function choose(runtime, profile) {
  const select = runtime.document.getElementById("set-model-profile");
  select.value = profile;
  select.dispatchEvent({ type: "change" });
}

function openSettings(runtime) {
  runtime.window.openSettingsModal();
  // BrowserShim parses option attributes but does not implement the native
  // selected-option -> select.value reflection. Supply that browser behavior.
  for (const select of runtime.document.querySelectorAll("select")) {
    const option = select.querySelector("option[selected]") || select.querySelector("option");
    if (option) select.value = option.getAttribute("value");
  }
  for (const textarea of runtime.document.querySelectorAll("textarea")) {
    textarea.value = textarea.textContent;
  }
  runtime.document.getElementById("set-model-profile").dispatchEvent({ type: "change" });
}

async function clickSave(runtime) {
  runtime.document.getElementById("set-save-btn").click();
  await new Promise((resolve) => setImmediate(resolve));
}

test("Optima selection saves separately and never inherits legacy output or reasoning", async () => {
  const runtime = boot();
  const { window, document, server } = runtime;
  const storage = window.SettingsStorage;
  await storage.save({
    ...storage.load("c1"),
    max_output_tokens: 2800,
    reasoning_effort: "high",
    truncation: "auto",
    temperature: 0.2,
    top_p: 0.9,
    instructions: "Keep my prompt",
    tools_config: { web_search: { enabled: false } },
  }, "c1");
  openSettings(runtime);
  assert.equal(document.getElementById("set-model-profile").value, "custom");
  assert.equal(document.getElementById("set-optima-generation").hidden, true);
  choose(runtime, "optima");
  assert.equal(document.getElementById("set-custom-generation").hidden, true);
  assert.equal(document.getElementById("set-optima-generation").hidden, false);
  assert.equal(document.getElementById("set-optima-tokens").value, "0");
  await clickSave(runtime);

  assert.equal(document.getElementById("settings-modal-custom"), null);
  assert.equal(server.c1.model_profile, "optima");
  assert.equal(server.c1.optima_max_output_tokens, 0);
  assert.equal(server.c1.max_output_tokens, 2800);
  assert.equal(server.c1.reasoning_effort, "high");
  assert.equal(server.c1.truncation, "auto");
  assert.equal(server.c1.tools_config.web_search.enabled, false);
  const params = window.getResponsesParams();
  assert.equal(params.model_profile, "optima");
  assert.equal(params.max_output_tokens, undefined);
  assert.equal(params.reasoning_effort, undefined);
  assert.equal(params.truncation, "disabled");
  assert.equal(params.temperature, 0.2);
  assert.equal(params.top_p, 0.9);
  assert.equal(params.instructions, "Keep my prompt");
});

test("Optima survives a fresh browser session and keeps conversation scope", async () => {
  const first = boot();
  openSettings(first);
  choose(first, "optima");
  first.document.getElementById("set-optima-tokens").value = "6144";
  await clickSave(first);

  const second = boot({ server: first.server });
  await second.window.loadServerConvSettings("c1");
  openSettings(second);
  assert.equal(second.document.getElementById("set-model-profile").value, "optima");
  assert.equal(second.document.getElementById("set-optima-tokens").value, "6144");
  assert.equal(second.window.getResponsesParams().max_output_tokens, 6144);

  second.window.currentConvId = "c2";
  await second.window.loadServerConvSettings("c2");
  const other = second.window.getResponsesParams();
  assert.equal(other.model_profile, undefined);
  assert.equal(second.window.SettingsStorage.load("c2").model_profile, "custom");
});

test("saving Optima does not rewrite a stored zero manual limit", async () => {
  const runtime = boot();
  const storage = runtime.window.SettingsStorage;
  await storage.save({ ...storage.load("c1"), max_output_tokens: 0 }, "c1");
  openSettings(runtime);
  choose(runtime, "optima");
  await clickSave(runtime);
  assert.equal(runtime.server.c1.max_output_tokens, 0);
  assert.equal(runtime.window.getResponsesParams().max_output_tokens, undefined);
});

test("returning to custom restores manual generation values", async () => {
  const runtime = boot();
  const storage = runtime.window.SettingsStorage;
  await storage.save({
    ...storage.load("c1"),
    model_profile: "optima",
    optima_max_output_tokens: 6144,
    max_output_tokens: 4200,
    reasoning_effort: "low",
    truncation: "auto",
  }, "c1");
  openSettings(runtime);
  choose(runtime, "custom");
  assert.equal(runtime.document.getElementById("set-custom-generation").hidden, false);
  assert.equal(runtime.document.getElementById("set-optima-generation").hidden, true);
  assert.equal(runtime.document.getElementById("set-tokens").value, "4200");
  await clickSave(runtime);
  const params = runtime.window.getResponsesParams();
  assert.equal(params.model_profile, undefined);
  assert.equal(params.max_output_tokens, 4200);
  assert.equal(params.reasoning_effort, "low");
  assert.equal(params.truncation, "auto");
  assert.equal(storage.load("c1").optima_max_output_tokens, 6144);
});

test("unsupported model cannot enable Optima and a saved choice is not downgraded", async () => {
  const runtime = boot({ model: "foreign-model", modelInfo: { name: "Foreign model" } });
  openSettings(runtime);
  const option = runtime.document.querySelector('option[value="optima"]');
  assert.equal(option.hasAttribute("disabled"), true);

  const storage = runtime.window.SettingsStorage;
  await storage.save({ ...storage.load("c1"), model_profile: "optima" }, "c1");
  openSettings(runtime);
  assert.equal(runtime.document.getElementById("set-model-profile").value, "optima");
  assert.match(runtime.document.getElementById("set-profile-status").textContent, /недоступна/);
  const before = runtime.requests.length;
  await clickSave(runtime);
  assert.equal(runtime.requests.length, before);
  assert.match(runtime.document.getElementById("set-status").textContent, /недоступна/);
  assert.equal(runtime.window.getResponsesParams().model_profile, "optima");
  choose(runtime, "custom");
  await clickSave(runtime);
  assert.equal(runtime.window.getResponsesParams().model_profile, undefined);
});

test("invalid Optima budget is rejected instead of becoming an unlimited request", async () => {
  const runtime = boot();
  openSettings(runtime);
  choose(runtime, "optima");
  runtime.document.getElementById("set-optima-tokens").value = "-1";
  await clickSave(runtime);
  assert.equal(runtime.requests.length, 0);
  assert.match(runtime.document.getElementById("set-status").textContent, /целым числом/);

  const storage = runtime.window.SettingsStorage;
  for (const value of [-1, 1.5, "bad", "Infinity", Number.MAX_SAFE_INTEGER + 1]) {
    await storage.save({ ...storage.load("c1"), model_profile: "optima", optima_max_output_tokens: value }, "c1");
    assert.throws(() => runtime.window.getResponsesParams(), /целым числом/);
  }
});

test("unknown saved profile is rejected without silently using custom", async () => {
  const runtime = boot();
  const storage = runtime.window.SettingsStorage;
  for (const value of ["turbo", " ", true, 0, {}]) {
    await storage.save({ ...storage.load("c1"), model_profile: value }, "c1");
    assert.throws(() => runtime.window.getResponsesParams(), /Неизвестный профиль/);
  }
  for (const value of [undefined, null, "", "custom"]) {
    await storage.save({ ...storage.load("c1"), model_profile: value }, "c1");
    assert.equal(runtime.window.getResponsesParams().model_profile, undefined);
  }
});

test("failed Optima save stays visible and reports server failure", async () => {
  const runtime = boot({ status: 500 });
  openSettings(runtime);
  choose(runtime, "optima");
  await clickSave(runtime);
  assert.notEqual(runtime.document.getElementById("settings-modal-custom"), null);
  assert.match(runtime.document.getElementById("set-status").textContent, /Не удалось сохранить/);
  assert.equal(runtime.server.c1, undefined);
});

test("picker lists Optima only for an available model without nested buttons", () => {
  const runtime = boot();
  runtime.context.modelsData.text.retired = { ...AVAILABLE, available: false, name: "Retired" };
  runtime.context.modelsData.text.manual = { name: "Manual only" };
  runtime.context.renderModelModal();
  const options = runtime.document.querySelectorAll(".model-option");
  assert.equal(options.length, 3);
  assert.equal(options[0].querySelector(".model-option-optima").textContent, "Optima · сбалансированный профиль");
  assert.equal(options[0].querySelector("button"), null);
  assert.equal(options[1].querySelector(".model-option-optima"), null);
  assert.equal(options[2].querySelector(".model-option-optima"), null);
});
