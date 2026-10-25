const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const storageSource = fs.readFileSync("static/settings/settings_storage.js", "utf8");
const paramsSource = fs.readFileSync("static/settings/settings_params.js", "utf8");

function runtime(model = "gpt-oss-120b", saved = {}) {
  const values = new Map([["logos_settings_conv_chat", JSON.stringify(saved)]]);
  const requests = [];
  const context = vm.createContext({
    window: {},
    currentModel: model,
    currentConvId: "chat",
    modelsData: {
      text: {
        "gpt-oss-120b": { ultra_profile: {
          profile_id: "ultra-gpt-oss-120b", default_profile: "ultra",
          settings_defaults: { max_output_tokens: 0, truncation: "disabled", reasoning_effort: "high" },
        } },
        "qwen3.6-35b-a3b": { ultra_profile: {
          profile_id: "ultra-qwen3.6-35b-a3b", default_profile: "ultra",
          settings_defaults: { max_output_tokens: 0, truncation: "disabled", reasoning_effort: "disabled" },
        } },
        "foreign-model": { provider: "other" },
      },
    },
    localStorage: {
      getItem: (key) => values.get(key) || null,
      setItem: (key, value) => values.set(key, value),
      removeItem: (key) => values.delete(key),
    },
    console,
  });
  context.window.SettingsUI = {
    deepMerge: (base, extra) => Object.assign(base, extra),
  };
  context.window.LogosDispatcher = {
    request: (url, init) => {
      requests.push({ url, init });
      return Promise.resolve({ ok: true, json: async () => saved });
    },
  };
  vm.runInContext(storageSource, context);
  vm.runInContext(paramsSource, context);
  return { context, window: context.window, requests, values };
}

test("saved medium and output caps cannot override Ultra on load or request", () => {
  const r = runtime("gpt-oss-120b", {
    reasoning_effort: "medium", truncation: "auto", max_output_tokens: 1500,
    instructions: "Keep my prompt", temperature: 0,
  });
  const settings = r.window.SettingsStorage.load("chat");
  assert.equal(settings.model_profile, "ultra");
  assert.equal(settings.model_profile_id, "ultra-gpt-oss-120b");
  const params = r.window.getResponsesParams();
  assert.equal(params.reasoning_effort, "high");
  assert.equal(params.max_output_tokens, 0);
  assert.equal(params.truncation, "disabled");
  assert.equal(params.instructions, "Keep my prompt");
  assert.equal(params.temperature, 0);
});

test("switching models recalculates Ultra instead of reusing another model's effort", () => {
  const r = runtime();
  assert.equal(r.window.getResponsesParams().reasoning_effort, "high");
  r.context.currentModel = "qwen3.6-35b-a3b";
  const params = r.window.getResponsesParams();
  assert.equal(params.reasoning_effort, undefined);
  assert.equal(params.max_output_tokens, 0);
  assert.equal(r.window.SettingsStorage.load("chat").model_profile_id, "ultra-qwen3.6-35b-a3b");
});

test("loading old server settings also resolves the active model profile", async () => {
  const r = runtime("gpt-oss-120b", {
    reasoning_effort: "medium", max_output_tokens: 2000, truncation: "auto",
  });
  const settings = await r.window.loadServerConvSettings("chat");
  assert.equal(settings.reasoning_effort, "high");
  assert.equal(settings.max_output_tokens, 0);
  assert.equal(settings.truncation, "disabled");
});

test("saving persists effective Ultra fields rather than old control values", async () => {
  const r = runtime();
  await r.window.SettingsStorage.save({
    reasoning_effort: "medium", max_output_tokens: 2500, truncation: "auto",
    instructions: "Saved prompt",
  }, "chat");
  const body = JSON.parse(r.requests[0].init.body);
  assert.equal(body.reasoning_effort, "high");
  assert.equal(body.max_output_tokens, 0);
  assert.equal(body.truncation, "disabled");
  assert.equal(body.model_profile_id, "ultra-gpt-oss-120b");
  assert.equal(body.instructions, "Saved prompt");
});

test("reset uses the selected model's Ultra values", () => {
  const r = runtime();
  const settings = r.window.resetSettings("chat");
  assert.equal(settings.reasoning_effort, "high");
  assert.equal(settings.max_output_tokens, 0);
  assert.equal(settings.model_profile, "ultra");
  const body = JSON.parse(r.requests[0].init.body);
  assert.equal(body.reasoning_effort, "high");
});

test("Yandex defaults and labels do not leak into a foreign provider", () => {
  const r = runtime("foreign-model", {
    model_profile: "ultra", model_profile_id: "ultra-gpt-oss-120b",
    reasoning_effort: "low", max_output_tokens: 123,
  });
  const settings = r.window.SettingsStorage.load("chat");
  assert.equal(settings.model_profile, undefined);
  assert.equal(settings.model_profile_id, undefined);
  assert.equal(settings.reasoning_effort, "low");
  assert.equal(settings.max_output_tokens, 123);
});
