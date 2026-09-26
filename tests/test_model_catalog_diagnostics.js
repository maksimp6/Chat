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
    assert.equal(events.some((entry) => entry.event === expected), true, expected);
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

  console.log("Model catalog diagnostics and retry recovery passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
