const assert = require("node:assert/strict");
const { BrowserShim } = require("./browser_dom");

(async () => {
  const shim = new BrowserShim('<button id="project-tree-btn" type="button"></button>');
  const loaded = shim.load(["static/core_api.js", "static/project_tree.js"], {
    fetch: async () => ({ ok: true, json: async () => ({ nodes: [] }) }),
  });
  loaded.window.AliceDispatcher = {
    request: async () => ({ ok: true, json: async () => ({ nodes: [] }) }),
  };

  assert.equal(typeof loaded.window.ProjectTree.load, "function");
  const button = loaded.document.getElementById("project-tree-btn");
  assert.ok(button, "project tree trigger should exist");

  const loader = loaded.window.ProjectTree.load;
  await loader();

  loaded.window.AliceDispatcher.request = async () => ({ ok: false, status: 503 });
  await assert.rejects(loader, /HTTP 503/);

  console.log("project tree regression checks passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
