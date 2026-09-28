"use strict";

const { BrowserShim } = require("./browser_dom");

class BrowserShimScenarioError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

class BrowserShimScenarioRunner {
  constructor(fixtures = {}) {
    this.fixtures = { ...fixtures };
  }

  run(steps) {
    if (!Array.isArray(steps)) {
      throw new TypeError("BrowserShim scenario steps must be an array");
    }

    let browser = null;
    const report = { success: true, steps: [] };

    for (const [index, step] of steps.entries()) {
      try {
        const outcome = this._runStep(browser, step);
        browser = outcome.browser || browser;
        report.steps.push({
          index,
          action: String(step && step.action),
          target: String(step && step.target),
          success: true,
          data: outcome.data,
        });
      } catch (error) {
        report.success = false;
        report.steps.push({
          index,
          action: String(step && step.action),
          target: String(step && step.target),
          success: false,
          error: {
            code: error.code || "execution_failed",
            message: error.message,
          },
        });
        return report;
      }
    }

    return report;
  }

  _runStep(browser, step) {
    if (!step || typeof step !== "object") {
      throw new BrowserShimScenarioError("validation_failed", "Scenario step must be an object");
    }

    const action = String(step.action || "");
    const target = String(step.target || "");

    if (!action || !target) {
      throw new BrowserShimScenarioError(
        "validation_failed",
        "Scenario step action and target are required",
      );
    }

    if (action === "navigate") {
      const html = this.fixtures[target];
      if (typeof html !== "string") {
        throw new BrowserShimScenarioError(
          "navigation_failed",
          "Unknown BrowserShim fixture: " + target,
        );
      }
      const nextBrowser = new BrowserShim(html);
      return {
        browser: nextBrowser,
        data: { fixture: target, url: "fixture://" + target },
      };
    }

    if (!browser) {
      throw new BrowserShimScenarioError(
        "navigation_required",
        "Navigate to a BrowserShim fixture before browser actions",
      );
    }

    if (action === "screenshot") {
      throw new BrowserShimScenarioError(
        "unsupported_action",
        "BrowserShim does not generate raster screenshots; inspect or assert state instead",
      );
    }

    const element = browser.document.querySelector(target);
    if (!element) {
      throw new BrowserShimScenarioError("target_not_found", "No element matches: " + target);
    }

    if (action === "inspect") {
      return { data: inspectElement(element, target) };
    }

    if (action === "click") {
      element.click();
      return { data: inspectElement(element, target) };
    }

    if (action === "fill") {
      if (typeof step.value !== "string") {
        throw new BrowserShimScenarioError("validation_failed", "Fill value must be a string");
      }
      element.value = step.value;
      element.dispatchEvent({ type: "input", bubbles: true });
      return { data: inspectElement(element, target) };
    }

    if (action === "assert_state") {
      const actual = inspectElement(element, target);
      const expected = parseExpectedState(step.value);
      assertExpectedState(actual, expected);
      return { data: actual };
    }

    throw new BrowserShimScenarioError(
      "unsupported_action",
      "Unsupported BrowserShim action: " + action,
    );
  }
}

function inspectElement(element, selector) {
  return {
    selector,
    exists: true,
    text: element.textContent,
    value: element.value,
    hidden: Boolean(element.hidden),
  };
}

function parseExpectedState(value) {
  if (typeof value !== "string") {
    throw new BrowserShimScenarioError(
      "validation_failed",
      "Assert-state value must be a JSON object string",
    );
  }

  try {
    const expected = JSON.parse(value);
    if (!expected || Array.isArray(expected) || typeof expected !== "object") {
      throw new TypeError("not an object");
    }
    return expected;
  } catch {
    throw new BrowserShimScenarioError(
      "validation_failed",
      "Assert-state value must be a JSON object string",
    );
  }
}

function assertExpectedState(actual, expected) {
  for (const [key, value] of Object.entries(expected)) {
    if (!Object.hasOwn(actual, key)) {
      throw new BrowserShimScenarioError("validation_failed", "Unsupported state field: " + key);
    }
    if (actual[key] !== value) {
      throw new BrowserShimScenarioError(
        "assertion_failed",
        "Expected " + key + " to equal " + JSON.stringify(value),
      );
    }
  }
}

module.exports = { BrowserShimScenarioError, BrowserShimScenarioRunner };
