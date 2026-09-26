const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const coreSource = fs.readFileSync("static/core_api.js", "utf8");
const source = fs.readFileSync("static/file_manager.js", "utf8");

function response(status, payload) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => payload,
    };
}

async function runWithFetch(mockResponse) {
    const context = {
        window: {},
        document: {
            createElement: () => ({}),
            body: {},
        },
        console,
        setTimeout,
        clearTimeout,
        navigator: {},
    };
    context.window.document = context.document;
    vm.runInNewContext(coreSource, context, { filename: "static/core_api.js" });
    context.window.AliceDispatcher = {
        request: async () => mockResponse,
    };
    vm.runInNewContext(source, context, { filename: "static/file_manager.js" });
    return context.window.fetchVectorStores();
}

(async () => {
    assert.deepEqual(
        await runWithFetch(response(200, [{ id: "vs-1" }])),
        [{ id: "vs-1" }],
    );

    assert.deepEqual(
        await runWithFetch(response(200, { data: [{ id: "vs-2" }] })),
        [{ id: "vs-2" }],
    );

    assert.deepEqual(
        await runWithFetch(response(200, { vector_stores: [{ id: "vs-3" }] })),
        [{ id: "vs-3" }],
    );

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
