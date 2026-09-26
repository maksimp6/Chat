const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const { BrowserShim } = require("./browser_dom");

const source = fs.readFileSync("static/core.js", "utf8");

async function createRuntime(handler) {
  const shim = new BrowserShim('<div id="app-root"></div><div id="model-list"></div>');
  const events = [];
  const quietConsole = {
    log() {},
    warn() {},
    info(label, context) {
      if (label === "[MODEL_CATALOG]") events.push(context);
    },
    error(label, context) {
      if (label === "[MODEL_CATALOG]") events.push(context);
    },
  };
  const runtime = shim.load([], {
    console: quietConsole,
    AliceDispatcher: { request: handler },
    AliceCoreAPI: {
      ui: { actions: { register() {} } },
      status: { set() {} },
    },
  });
  vm.runInContext(source, runtime.context, { filename: "static/core.js" });
  vm.runInContext(fs.readFileSync("static/models.js", "utf8"), runtime.context, {
    filename: "static/models.js",
  });
  return { runtime, events };
}

function response(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      if (payload instanceof Error) throw payload;
      return payload;
    },
  };
}

(async () => {
  let next = () => response({ text: { lite: { name: "Lite" } }, voice: {} });
  const { runtime, events } = await createRuntime((url) => {
    if (url === "/api/conversations") return Promise.resolve(response({ conversations: [] }));
    return Promise.resolve(next());
  });

  await runtime.window.AliceModelCatalog.load();
  assert.deepEqual(
    events.slice(-2).map((entry) => entry.event),
    ["request_start", "success"],
  );
  assert.equal(events.at(-1).modelCount, 1);
  assert.equal(JSON.stringify(events).includes("Authorization"), false);

  for (const [factory, expected] of [
    [() => response({ text: {}, voice: {} }), "empty_response"],
    [() => response({}, 503), "http_error"],
    [() => response(new SyntaxError("bad json")), "parsing_error"],
    [() => response("not an object"), "invalid_response"],
  ]) {
    next = factory;
    await assert.rejects(runtime.window.AliceModelCatalog.load());
    assert.equal(
      events.some((entry) => entry.event === expected),
      true,
      expected,
    );
  }

  next = () => {
    throw new TypeError("offline");
  };
  await assert.rejects(runtime.window.AliceModelCatalog.load());
  assert.equal(events.at(-1).event, "network_error");

  next = () => response({ text: { recovered: { name: "Recovered" } }, voice: {} });
  await runtime.window.AliceModelCatalog.load();
  assert.equal(events.at(-1).event, "success");
  assert.equal(vm.runInContext("modelsData.text.recovered.name", runtime.context), "Recovered");

  let retryRequests = 0;
  const retry = await createRuntime((url) => {
    if (url === "/api/conversations") return Promise.resolve(response({ conversations: [] }));
    retryRequests += 1;
    if (retryRequests === 1) return Promise.reject(new TypeError("offline"));
    return Promise.resolve(
      response({ text: { recovered: { name: "Recovered model" } }, voice: {} }),
    );
  });
  await assert.rejects(retry.runtime.window.AliceModelCatalog.load());

  // Re-rendering and repeated initialization replace the state rather than
  // accumulating controls or event listeners.
  vm.runInContext(
    "renderModelModal(); renderModelModal(); initModels(); initModels();",
    retry.runtime.context,
  );
  const modelList = retry.runtime.document.getElementById("model-list");
  let retryButtons = modelList.querySelectorAll("button");
  assert.equal(retryButtons.length, 1);
  const retryButton = retryButtons[0];
  const status = modelList.querySelector('[role="status"]');
  assert.ok(status, "the model error must be announced as a status");
  assert.equal(status.getAttribute("aria-live"), "polite");
  assert.equal(retryButton.tagName, "BUTTON");
  assert.equal(retryButton.type, "button");
  assert.equal(retryButton.textContent, "Повторить");

  retryButton.click();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(retryRequests, 2, "one real retry click must issue exactly one additional request");
  assert.equal(modelList.querySelector('[role="status"]'), null, "success must clear error state");
  retryButtons = modelList.querySelectorAll("button");
  assert.equal(retryButtons.length, 1, "success must replace retry with the loaded model");
  assert.equal(retryButtons[0].tagName, "BUTTON");
  assert.equal(retryButtons[0].type, "button");
  assert.equal(retryButtons[0].getAttribute("aria-label"), "Recovered model");
  assert.equal(retry.events.at(-1).event, "success");

  console.log("Model catalog diagnostics and retry recovery passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
