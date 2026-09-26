"use strict";

const assert = require("node:assert/strict");
const vm = require("node:vm");

function runWithBudget(source, timeout = 100) {
    const context = vm.createContext({console});
    return vm.runInContext(source, context, {timeout});
}

function expectTimeout(source, timeout = 50) {
    assert.throws(
        () => runWithBudget(source, timeout),
        (error) => error && (error.code === "ERR_SCRIPT_EXECUTION_TIMEOUT" || /timed out/i.test(error.message)),
    );
}

function measure(source, timeout = 100) {
    const start = process.hrtime.bigint();
    runWithBudget(source, timeout);
    return Number(process.hrtime.bigint() - start) / 1e6;
}

{
    expectTimeout("while (true) {}");
    expectTimeout("for (;;) {}");
    expectTimeout("let i = 0; while (i < 1e12) { i += 1; }");
}

{
    const result = runWithBudget(
        "let state = 0; try { for (let i = 0; i < 100; i += 1) { if (i === 7) throw new Error('controlled failure'); state += 1; } } catch (error) { state = -1; } finally { state += 1000; } state;"
    );
    assert.equal(result, 1006, "failed loop must reach its cleanup/finally path");
}

{
    assert.throws(
        () => runWithBudget("const items = new Array(10_000_001).fill(0);"),
        /Invalid array length|heap out of memory|timed out/i,
    );
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
