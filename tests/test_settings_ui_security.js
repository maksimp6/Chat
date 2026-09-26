const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("static/settings/ui_helpers.js", "utf8");

const context = {
  window: {},
  document: {
    getElementById() {
      return null;
    },
    createElement() {
      return {};
    },
    head: {
      appendChild() {},
    },
  },
};

vm.runInNewContext(source, context, { filename: "static/settings/ui_helpers.js" });

const payload = JSON.parse(
  '{"safe":{"nested":1},"__proto__":{"polluted":"yes"},"constructor":{"prototype":{"polluted":"yes"}}}',
);
const result = context.window.SettingsUI.deepMerge({}, payload);

assert.equal(Object.prototype.polluted, undefined);
assert.equal({}.polluted, undefined);
assert.equal(result.safe.nested, 1);
assert.equal(Object.prototype.hasOwnProperty.call(result, "__proto__"), false);
assert.equal(Object.prototype.hasOwnProperty.call(result, "constructor"), false);

const inherited = Object.create({ inherited: "ignore" });
inherited.own = "keep";
const inheritedResult = context.window.SettingsUI.deepMerge({}, inherited);
assert.deepEqual(Object.keys(inheritedResult), ["own"]);
assert.equal(inheritedResult.own, "keep");

console.log("Settings deepMerge prototype-pollution regression passed");
