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
        "qwen3-235b-a22b-fp8": { ultra_profile: {
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

test("Ultra keeps explicit output caps while applying reasoning and truncation defaults", () => {
  const r = runtime("gpt-oss-120b", {
    reasoning_effort: "medium", truncation: "auto", max_output_tokens: 1500,
    instructions: "Keep my prompt", temperature: 0,
  });
  const settings = r.window.SettingsStorage.load("chat");
  assert.equal(settings.model_profile, "ultra");
  assert.equal(settings.model_profile_id, "ultra-gpt-oss-120b");
  assert.equal(settings.max_output_tokens, 1500);
  const params = r.window.getResponsesParams();
  assert.equal(params.reasoning_effort, "high");
  assert.equal(params.max_output_tokens, 1500);
  assert.equal(params.truncation, "disabled");
  assert.equal(params.instructions, "Keep my prompt");
  assert.equal(params.temperature, 0);
});

test("switching models recalculates Ultra instead of reusing another model's effort", () => {
  const r = runtime();
  assert.equal(r.window.getResponsesParams().reasoning_effort, "high");
  r.context.currentModel = "qwen3-235b-a22b-fp8";
  const params = r.window.getResponsesParams();
  assert.equal(params.reasoning_effort, undefined);
  assert.equal(params.max_output_tokens, 0);
  assert.equal(r.window.SettingsStorage.load("chat").model_profile_id, "ultra-qwen3.6-35b-a3b");
});

test("loading server settings applies Ultra without resetting an explicit 2000-token cap", async () => {
  const r = runtime("gpt-oss-120b", {
    reasoning_effort: "medium", max_output_tokens: 2000, truncation: "auto",
  });
  const settings = await r.window.loadServerConvSettings("chat");
  assert.equal(settings.reasoning_effort, "high");
  assert.equal(settings.max_output_tokens, 2000);
  assert.equal(settings.truncation, "disabled");
});

test("saving persists Ultra defaults and the user's explicit output cap", async () => {
  const r = runtime();
  await r.window.SettingsStorage.save({
    reasoning_effort: "medium", max_output_tokens: 2500, truncation: "auto",
    instructions: "Saved prompt",
  }, "chat");
  const body = JSON.parse(r.requests[0].init.body);
  assert.equal(body.reasoning_effort, "high");
  assert.equal(body.max_output_tokens, 2500);
  assert.equal(body.truncation, "disabled");
  assert.equal(body.model_profile_id, "ultra-gpt-oss-120b");
  assert.equal(body.instructions, "Saved prompt");
});

test("an explicit 2000-token cap survives local load, save, server load and serialization", async () => {
  const r = runtime("gpt-oss-120b", { max_output_tokens: 2000 });
  const loaded = r.window.SettingsStorage.load("chat");
  assert.equal(loaded.max_output_tokens, 2000);
  assert.equal(r.window.getResponsesParams().max_output_tokens, 2000);
  await r.window.SettingsStorage.save(loaded, "chat");
  assert.equal(JSON.parse(r.requests[0].init.body).max_output_tokens, 2000);
  assert.equal(JSON.parse(r.values.get("logos_settings_conv_chat")).max_output_tokens, 2000);
  const serverLoaded = await r.window.loadServerConvSettings("chat");
  assert.equal(serverLoaded.max_output_tokens, 2000);
  assert.equal(r.window.getResponsesParams().max_output_tokens, 2000);
});

test("an explicit zero tool-call limit is serialized as zero", () => {
  const r = runtime("gpt-oss-120b", { max_tool_calls: 0 });
  assert.equal(r.window.getResponsesParams().max_tool_calls, 0);
  const stringValue = runtime("gpt-oss-120b", { max_tool_calls: "0" });
  assert.equal(stringValue.window.getResponsesParams().max_tool_calls, 0);
});

test("numeric settings reject malformed strings instead of accepting a numeric prefix", () => {
  for (const name of ["max_output_tokens", "max_tool_calls", "temperature", "top_p", "top_logprobs"]) {
    const r = runtime("gpt-oss-120b", { [name]: "2000junk" });
    assert.equal(r.window.SettingsStorage.load("chat")[name], "2000junk");
    assert.throws(() => r.window.getResponsesParams(), new RegExp(name));
  }
});

test("text verbosity and JSON format travel together without replacing each other", () => {
  const schema = {
    type: "object",
    properties: { result: { type: "string" } },
    required: ["result"],
    additionalProperties: false,
  };
  const r = runtime("gpt-oss-120b", {
    text_verbosity: "high",
    text_format: "json",
    json_schema_name: "audit_result",
    json_schema: JSON.stringify(schema),
  });
  const text = JSON.parse(JSON.stringify(r.window.getResponsesParams().text));
  assert.deepEqual(text, {
    verbosity: "high",
    format: { type: "json_schema", name: "audit_result", schema },
  });
});

test("invalid JSON schema fails explicitly instead of falling back to plain text", () => {
  const r = runtime("gpt-oss-120b", {
    text_format: "json", json_schema: "{broken",
  });
  assert.throws(() => r.window.getResponsesParams(), /json_schema/);
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
