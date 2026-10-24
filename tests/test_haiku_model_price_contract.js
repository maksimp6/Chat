const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");
const { BrowserShim } = require("../browser/emulator/browser_dom");

// Exercise the actual model picker with the same BrowserShim used by Alice.
const shim = new BrowserShim('<div id="model-list"></div>');
const runtime = shim.load([], {});
vm.runInContext(
  `var currentModel = "aliceai-llm";
   var modelsData = {text: {
     "aliceai-llm": {name: "Alice", input: 0.5, output: 1.2},
     "claude-haiku-5-5": {name: "Haiku 5.5", provider: "anthropic", currency: "USD",
       price_unit: "per_million_tokens", input: 0.1, output: 0.5}
   }, voice: {}};`,
  runtime.context,
);
vm.runInContext(fs.readFileSync("static/models.js", "utf8"), runtime.context);
vm.runInContext("renderModelModal()", runtime.context);
const options = runtime.document.querySelectorAll(".model-option");
const yandexPrice = options[0].querySelector(".model-option-price").textContent;
const haikuPrice = options[1].querySelector(".model-option-price").textContent;
test("model picker preserves Yandex RUB and displays Haiku USD per million", () => {
  assert.match(yandexPrice, /₽\/1K/);
  assert.doesNotMatch(haikuPrice, /₽|RUB|\/1K/);
  assert.match(haikuPrice, /USD|\$/);
  assert.match(haikuPrice, /1M|million|миллион|1\s*000\s*000/);
});

test("model picker passes the exact Haiku model key on selection", () => {
  let selected;
  runtime.window.changeModel = (key) => {
    selected = key;
  };
  options[1].dispatchEvent({ type: "click" });
  assert.equal(selected, "claude-haiku-5-5");
});
