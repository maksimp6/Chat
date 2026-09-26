const assert = require("node:assert/strict");
const fs = require("node:fs");
const { BrowserShim } = require("./browser_dom");

const source = fs.readFileSync("static/file_manager.js", "utf8");

function response(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  };
}

async function runWithFetch(mockResponse) {
  const shim = new BrowserShim();
  const loaded = shim.load(["static/core_api.js", "static/file_manager.js"], {
    navigator: {},
    fetch: async () => mockResponse,
  });
  loaded.window.AliceDispatcher = {
    request: async () => mockResponse,
  };
  return loaded.window.fetchVectorStores();
}

(async () => {
  assert.deepEqual(await runWithFetch(response(200, [{ id: "vs-1" }])), [{ id: "vs-1" }]);

  assert.deepEqual(await runWithFetch(response(200, { data: [{ id: "vs-2" }] })), [{ id: "vs-2" }]);

  assert.deepEqual(await runWithFetch(response(200, { vector_stores: [{ id: "vs-3" }] })), [
    { id: "vs-3" },
  ]);

  await assert.rejects(
    runWithFetch(response(200, { error: "backend unavailable" })),
    /Некорректный ответ Vector Stores/,
  );

  await assert.rejects(
    runWithFetch(response(500, { error: "upstream failed" })),
    /Vector Stores: upstream failed/,
  );

  assert.match(source, /Array\.isArray\(stores\)/);
  assert.match(source, /Не удалось загрузить Vector Stores/);
  assert.match(source, /btn-retry-vs/);

  console.log("file_manager.js regression checks passed");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
