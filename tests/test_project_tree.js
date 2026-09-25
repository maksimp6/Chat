const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("static/project_tree.js", "utf8");
const listeners = {};
const button = {
    addEventListener: (name, fn) => { listeners[name] = fn; },
};
const document = {
    addEventListener: (name, fn) => { if (name === "DOMContentLoaded") fn(); },
    getElementById: (id) => id === "project-tree-btn" ? button : null,
};
const context = {
    console,
    window: {fetch: async () => ({ok: true, json: async () => ({nodes: []})})},
    document,
};

(async () => {
    vm.runInNewContext(source, context);
    assert.equal(typeof context.window.ProjectTree.load, "function");
    assert.equal(typeof listeners.click, "function");

    const loader = context.window.ProjectTree.load;
    await loader();
    context.window.fetch = async () => ({ok: false, status: 503});
    await assert.rejects(loader, /HTTP 503/);

    console.log("project tree regression checks passed");
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
