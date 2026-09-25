const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("static/model_loader.js", "utf8");

function makeLoader(fetchImpl) {
    const context = {
        console: {log() {}, info() {}, warn() {}, error() {}},
        window: {fetch: fetchImpl},
        AbortController: class {
            constructor() { this.signal = {}; }
            abort() {}
        },
        setTimeout,
        clearTimeout,
        Date
    };
    vm.runInNewContext(source, context);
    return context.window.AliceModelLoader;
}

async function response(status, payload, jsonError) {
    return {
        ok: status >= 200 && status < 300,
        status,
        async json() {
            if (jsonError) throw new Error("invalid json");
            return payload;
        }
    };
}

(async function() {
    var loader = makeLoader(async () => response(200, {
        text: {model_a: {name: "A"}},
        voice: {voice_a: {name: "Voice"}}
    }));
    var models = await loader.load();
    assert.equal(Object.keys(models.text).length, 1);
    assert.equal(Object.keys(models.voice).length, 1);

    loader = makeLoader(async () => response(200, {text: {}, voice: {}}));
    await assert.rejects(() => loader.load(), error => error.code === "EMPTY_MODELS");

    loader = makeLoader(async () => response(503, {error: "down"}));
    await assert.rejects(() => loader.load(), error => error.code === "HTTP_ERROR" && error.status === 503);

    loader = makeLoader(async () => { throw new Error("offline"); });
    await assert.rejects(() => loader.load(), error => error.code === "NETWORK_ERROR");

    loader = makeLoader(async () => response(200, null, true));
    await assert.rejects(() => loader.load(), error => error.code === "INVALID_JSON");

    loader = makeLoader(async () => response(200, {text: []}));
    await assert.rejects(() => loader.load(), error => error.message.includes("invalid text models"));

    console.log("model loader regression tests: ok");
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});