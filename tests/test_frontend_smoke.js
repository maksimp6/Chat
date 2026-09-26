"use strict";

const assert = require("node:assert/strict");
const vm = require("node:vm");
const { BrowserShim } = require("./browser_dom");

function load(sources, html, extra = {}) {
  return new BrowserShim(html).load(sources, extra);
}

function click(element) {
  assert.ok(element, "expected element");
  element.click();
}

(async function main() {
  {
    const { document, window } = load(
      ["static/core_api.js", "static/ui_runtime.js", "static/dispatcher.js", "static/core.js"],
      '<button id="theme-toggle" data-action="theme.cycle"></button>',
    );
    assert.equal(window.AliceTheme.getStored(), "light");
    assert.equal(window.AliceTheme.apply("dark", true), "dark");
    click(document.getElementById("theme-toggle"));
    assert.equal(window.AliceTheme.getStored(), "dim");
    assert.equal(
      document.getElementById("theme-toggle").getAttribute("aria-label"),
      "Цветовая схема: Приглушённая",
    );
    assert.equal(window.AliceTheme.apply("not-a-theme", false), "light");
  }

  {
    const { document, context } = load(
      [
        "static/core_api.js",
        "static/ui_runtime.js",
        "static/dispatcher.js",
        "static/core.js",
        "static/models.js",
      ],
      '<button id="model-btn" data-action="modal.open" data-modal="model-modal"></button><div id="model-modal" class="modal" hidden><div class="modal-content"><button id="close-modal" data-action="modal.close" data-modal="model-modal"></button><div id="model-list"></div></div></div>',
    );
    vm.runInContext(
      `modelsData = {text: {lite: {name: "Lite", input: "1", output: "2", multimodal: true}, full: {name: "Full", input: "3", output: "4"}}, voice: {voice: {name: "Voice", input: "5", output: "6"}}}; currentModel = "lite"; window.changeModel = function(model) { currentModel = model; };`,
      context,
    );
    click(document.getElementById("model-btn"));
    assert.equal(document.getElementById("model-modal").hidden, false);
    assert.equal(document.getElementById("model-modal").classList.contains("visible"), true);
    const options = document.getElementById("model-list").querySelectorAll(".model-option");
    assert.equal(options.length, 3);
    assert.ok(options[0].classList.contains("active"));
    assert.match(options[0].textContent, /Lite/);
    assert.match(options[0].textContent, /📷/);
    assert.match(options[2].textContent, /🎤/);
    click(document.getElementById("close-modal"));
    assert.equal(document.getElementById("model-modal").hidden, true);
    assert.equal(document.getElementById("model-modal").classList.contains("visible"), false);
    click(document.getElementById("model-btn"));
    click(options[1]);
    assert.equal(vm.runInContext("currentModel", context), "full");
    assert.equal(document.getElementById("model-modal").classList.contains("visible"), false);
  }

  {
    const { document, context } = load(
      [
        "static/core_api.js",
        "static/ui_runtime.js",
        "static/dispatcher.js",
        "static/core.js",
        "static/sidebar.js",
      ],
      '<button id="menu-btn" data-action="sidebar.toggle"></button><button id="close-sidebar-btn"></button><div id="overlay"></div><aside id="sidebar"></aside><button id="new-chat-btn"></button><div id="conv-list"></div>',
    );
    vm.runInContext(
      'conversations = [{id: "c1", title: "Первый чат", model: "lite"}]; currentConvId = "c1";',
      context,
    );
    vm.runInContext("renderSidebar()", context);
    const item = document.getElementById("conv-list").querySelector(".conv-item");
    assert.ok(item);
    assert.ok(item.classList.contains("active"));
    assert.equal(item.querySelector(".conv-title").textContent, "Первый чат");
    click(document.getElementById("menu-btn"));
    assert.ok(document.getElementById("sidebar").classList.contains("open"));
    assert.equal(document.getElementById("overlay").classList.contains("visible"), true);
    assert.equal(document.getElementById("menu-btn").getAttribute("aria-expanded"), "true");
    click(document.getElementById("overlay"));
    assert.equal(document.getElementById("sidebar").classList.contains("open"), false);
    assert.equal(document.getElementById("menu-btn").getAttribute("aria-expanded"), "false");
  }

  {
    const requests = [];
    const fetch = async (url, init) => {
      requests.push({ url, init });
      if (url === "/api/memory/manage") {
        return {
          ok: true,
          json: async () => ({
            config: { enabled: true, max_context_facts: 7 },
            facts: [{ category: "work", fact: "Test fact" }],
          }),
        };
      }
      if (url === "/api/memory/config") {
        assert.equal(init.method, "PUT");
        return { ok: true, json: async () => ({}) };
      }
      throw new Error("unexpected fetch: " + url);
    };
    const { document, window } = load(
      [
        "static/core_api.js",
        "static/ui_runtime.js",
        "static/dispatcher.js",
        "static/memory_panel.js",
      ],
      '<button id="memory-btn" data-action="modal.open" data-modal="memoryModal"></button><div id="memoryModal" class="modal" hidden><button id="memoryCloseBtn" data-action="modal.close" data-modal="memoryModal"></button><button id="memoryClearBtn"></button><input id="memEnabled"><input id="memLimit"><span id="memCount"></span><div id="memoryFactsList"></div>',
      { fetch },
    );

    click(document.getElementById("memory-btn"));
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(document.getElementById("memoryModal").hidden, false);
    assert.equal(document.getElementById("memoryModal").classList.contains("visible"), true);
    assert.equal(document.getElementById("memEnabled").checked, true);
    assert.equal(document.getElementById("memLimit").value, 7);
    assert.equal(document.getElementById("memCount").textContent, "1");
    assert.match(document.getElementById("memoryFactsList").textContent, /\[work\] Test fact/);
    assert.deepEqual(
      requests.map((item) => item.url),
      ["/api/memory/manage"],
    );

    click(document.getElementById("memoryCloseBtn"));
    assert.equal(document.getElementById("memoryModal").hidden, true);
    assert.equal(document.getElementById("memoryModal").classList.contains("visible"), false);
  }

  console.log("frontend smoke tests passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
