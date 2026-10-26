const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

const coreSource = fs.readFileSync("static/core.js", "utf8");
const modelsSource = fs.readFileSync("static/models.js", "utf8");

const PREPARED = {
  profile_id: "ultra-yandexgpt-5.1",
  status: "prepared_not_activated",
  transport: "text_generation_async",
  lifecycle_state: "listed",
  stop_and_recheck_on: null,
  context_window_tokens: 32768,
  output_ceiling_tokens: null,
  blockers: [
    "hosted_output_ceiling_unverified",
    "reasoning_capability_unverified",
    "provider_acceptance_not_run",
  ],
};

const RETIRED = {
  profile_id: "ultra-qwen3-235b-a22b-fp8",
  status: "blocked_retired",
  transport: "responses",
  lifecycle_state: "retired",
  stop_and_recheck_on: "2026-09-30",
  context_window_tokens: null,
  output_ceiling_tokens: null,
  blockers: ["model_retired"],
};

function modalAwareRuntime(html) {
  const shim = new BrowserShim(html);
  return shim.load([], {
    LogosCoreAPI: {
      ui: { actions: { register() {} }, modal: { close() {} }, status: { set() {} } },
      status: { set() {} },
    },
  });
}

function renderPicker(modelsData, currentModel = "yandexgpt-5.1") {
  const runtime = modalAwareRuntime('<div id="model-list"></div><div id="model-modal"></div>');
  vm.runInContext(
    `var currentModel = ${JSON.stringify(currentModel)};` +
      ` var modelsData = ${JSON.stringify(modelsData)};`,
    runtime.context,
  );
  vm.runInContext(modelsSource, runtime.context, { filename: "static/models.js" });
  vm.runInContext("renderModelModal()", runtime.context);
  return runtime;
}

async function loadCatalogThroughApi(payload) {
  const runtime = new BrowserShim(
    '<div id="app-root"></div><div id="model-list"></div><div id="model-modal"></div>',
  ).load([], {
    console: { log() {}, warn() {}, info() {}, error() {} },
    LogosDispatcher: {
      request: () => Promise.resolve({ ok: true, status: 200, json: async () => payload }),
    },
    LogosCoreAPI: {
      ui: { actions: { register() {} }, modal: { close() {} } },
      status: { set() {} },
    },
  });
  vm.runInContext(coreSource, runtime.context, { filename: "static/core.js" });
  vm.runInContext(modelsSource, runtime.context, { filename: "static/models.js" });
  await runtime.window.LogosModelCatalog.load();
  return runtime;
}

test("retired text model is disabled, labelled and never selected", () => {
  const runtime = renderPicker({
    text: {
      "yandexgpt-5.1": {
        name: "YandexGPT 5.1 Pro",
        input: 0.8,
        output: 0.8,
        ultra_profile: PREPARED,
      },
      "qwen3-235b-a22b-fp8": {
        name: "Qwen3 235B A22B FP8",
        input: 0.5,
        output: 0.5,
        available: false,
        ultra_profile: RETIRED,
      },
    },
    voice: {},
  });
  const [active, retired] = runtime.document.querySelectorAll(".model-option");

  assert.equal(retired.disabled, true);
  assert.match(retired.className, /model-option-unavailable/);
  assert.match(retired.querySelector("strong").textContent, /недоступна/);
  assert.equal(retired.querySelector(".model-option-ultra").textContent, "Ultra · снята");

  const selected = [];
  runtime.window.changeModel = (key) => selected.push(key);
  retired.dispatchEvent({ type: "click" });
  assert.deepEqual(selected, []);

  assert.equal(active.disabled, false);
  assert.doesNotMatch(active.className, /model-option-unavailable/);
  active.dispatchEvent({ type: "click" });
  assert.deepEqual(selected, ["yandexgpt-5.1"]);
});

test("retired voice model is shown as unavailable with the microphone badge", () => {
  const runtime = renderPicker(
    {
      text: {},
      voice: {
        "speech-realtime-250923": {
          name: "Speech Realtime 260528",
          input: 0.1,
          output: 0.2,
          available: false,
          ultra_profile: RETIRED,
        },
      },
    },
    "speech-realtime-250923",
  );
  const [voice] = runtime.document.querySelectorAll(".model-option");
  assert.equal(voice.disabled, true);
  assert.match(voice.querySelector("strong").textContent, /🎤.*недоступна/);
  assert.match(voice.className, /active/);
});

test("Ultra line reports status, context window and blocker count", () => {
  const runtime = renderPicker({
    text: {
      "yandexgpt-5.1": {
        name: "YandexGPT 5.1 Pro",
        input: 0.8,
        output: 0.8,
        ultra_profile: PREPARED,
      },
      "yandexgpt-5-lite": { name: "Alice AI LLM", input: 0.5, output: 1.2 },
    },
    voice: {},
  });
  const [withProfile, withoutProfile] = runtime.document.querySelectorAll(".model-option");
  const line = withProfile.querySelector(".model-option-ultra");

  assert.equal(line.textContent, "Ultra · не активирован · контекст 32768\u00a0ток. · блокеров: 3");
  assert.equal(line.title, PREPARED.blockers.join(", "));
  assert.equal(withoutProfile.querySelector(".model-option-ultra"), null);
  assert.equal(withoutProfile.disabled, false);
});

test("Ultra line tolerates a summary without blockers or window", () => {
  const runtime = renderPicker({
    text: {
      "gpt-oss-120b": {
        name: "GPT OSS 120B",
        input: 0.3,
        output: 0.3,
        ultra_profile: { status: "prepared_not_activated" },
      },
    },
    voice: {},
  });
  const line = runtime.document.querySelector(".model-option-ultra");
  assert.equal(line.textContent, "Ultra · не активирован · блокеров: 0");
});

test("catalog loaded from the API marks retired models and their Ultra status", async () => {
  const runtime = await loadCatalogThroughApi({
    text: {
      "yandexgpt-5.1": {
        name: "YandexGPT 5.1 Pro",
        input: 0.8,
        output: 0.8,
        type: "text",
        ultra_profile: PREPARED,
      },
      "qwen3-235b-a22b-fp8": {
        name: "Qwen3 235B A22B FP8",
        input: 0.5,
        output: 0.5,
        type: "text",
        available: false,
        ultra_profile: RETIRED,
      },
    },
    voice: {},
    degraded: false,
  });
  const options = runtime.document.querySelectorAll(".model-option");

  assert.equal(options.length, 2);
  assert.equal(options[0].disabled, false);
  assert.equal(options[1].disabled, true);
  assert.equal(options[1].querySelector(".model-option-ultra").textContent, "Ultra · снята");

  const selected = [];
  runtime.window.changeModel = (key) => selected.push(key);
  options[1].dispatchEvent({ type: "click" });
  options[0].dispatchEvent({ type: "click" });
  assert.deepEqual(selected, ["yandexgpt-5.1"]);
});


test("active Ultra defaults are labelled as the selected application policy", () => {
  const runtime = renderPicker({
    text: {
      "gpt-oss-120b": {
        name: "GPT OSS 120B",
        input: 0.3,
        output: 0.3,
        ultra_profile: {
          ...PREPARED,
          profile_id: "ultra-gpt-oss-120b",
          default_profile: "ultra",
          settings_defaults: {
            max_output_tokens: 0,
            truncation: "disabled",
            reasoning_effort: "high",
          },
        },
      },
    },
    voice: {},
  }, "gpt-oss-120b");
  const line = runtime.document.querySelector(".model-option-ultra").textContent;
  assert.match(line, /Ultra · по умолчанию/);
  assert.doesNotMatch(line, /не активирован/);
});
