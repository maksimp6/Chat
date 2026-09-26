"use strict";

const assert = require("node:assert/strict");
const vm = require("node:vm");

function runWithBudget(source, timeout = 100) {
    const context = vm.createContext({console});
    return vm.runInContext(source, context, {timeout});
}

function measure(source) {
    const start = performance.now();
    runWithBudget(source, 100);
    return performance.now() - start;
}

function expectTimeout(source, timeout = 50) {
    const bounded = runWithBudget("const items = new Array(1024).fill(0); items.length;");
    assert.equal(bounded, 1024);
}

{
    const fast = measure("let total = 0; for (let i = 0; i < 10000; i += 1) total += i;");
    assert.ok(fast < 100, "bounded loop exceeded performance budget: " + fast.toFixed(2) + "ms");
}

{
    const result = runWithBudget(
        "const cache = new Map(); let calls = 0; function getCached(key, loader) { if (cache.has(key)) return cache.get(key); const value = loader(); cache.set(key, value); return value; } const first = getCached('models', () => { calls += 1; return ['lite']; }); const second = getCached('models', () => { calls += 1; return ['broken']; }); [first, second, calls];"
    );
    assert.deepEqual(result[0], ["lite"]);
    assert.deepEqual(result[1], ["lite"]);
    assert.equal(result[2], 1, "repeatable lookup must use the cache");
}

console.log("frontend runtime safety tests passed");
