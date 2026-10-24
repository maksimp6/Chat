"use strict";

const assert = require("node:assert/strict");
const { BrowserShimScenarioRunner } = require("./browser_self_test_runner");

const fixtures = {
  composer:
    '<main><input id="message" value=""><button id="send" type="button">Send</button></main>',
};

(function testSuccessfulScenario() {
  const report = new BrowserShimScenarioRunner(fixtures).run([
    { action: "navigate", target: "composer" },
    { action: "inspect", target: "#message" },
    { action: "fill", target: "#message", value: "Hello Alice" },
    {
      action: "assert_state",
      target: "#message",
      value: JSON.stringify({ exists: true, value: "Hello Alice", hidden: false }),
    },
    { action: "click", target: "#send" },
  ]);

  assert.equal(report.success, true);
  assert.equal(report.steps.length, 5);
  assert.equal(report.steps[0].data.url, "fixture://composer");
  assert.equal(report.steps[3].data.value, "Hello Alice");
})();

(function testMissingTargetIsReported() {
  const report = new BrowserShimScenarioRunner(fixtures).run([
    { action: "navigate", target: "composer" },
    { action: "inspect", target: "#missing" },
  ]);

  assert.equal(report.success, false);
  assert.equal(report.steps[1].error.code, "target_not_found");
})();

(function testBlockedActionsAndAssertionsAreReported() {
  const screenshot = new BrowserShimScenarioRunner(fixtures).run([
    { action: "navigate", target: "composer" },
    { action: "screenshot", target: "#send" },
  ]);
  const assertion = new BrowserShimScenarioRunner(fixtures).run([
    { action: "navigate", target: "composer" },
    { action: "assert_state", target: "#send", value: JSON.stringify({ text: "Not send" }) },
  ]);

  assert.equal(screenshot.success, false);
  assert.equal(screenshot.steps[1].error.code, "unsupported_action");
  assert.equal(assertion.success, false);
  assert.equal(assertion.steps[1].error.code, "assertion_failed");
})();

(function testNavigationAndInputValidationAreReported() {
  const navigation = new BrowserShimScenarioRunner(fixtures).run([
    { action: "navigate", target: "missing" },
  ]);
  const validation = new BrowserShimScenarioRunner(fixtures).run([
    { action: "navigate", target: "composer" },
    { action: "fill", target: "#message", value: 3 },
  ]);

  assert.equal(navigation.success, false);
  assert.equal(navigation.steps[0].error.code, "navigation_failed");
  assert.equal(validation.success, false);
  assert.equal(validation.steps[1].error.code, "validation_failed");
})();

console.log("browser self-test runner passed");
