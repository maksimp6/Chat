const { BrowserShim } = require("./browser_dom");

const shim = new BrowserShim();
const { window, document } = shim.load(["static/core_api.js"]);

const UI = window.AliceCoreAPI.ui;
const root = document.createElement("div");
document.body.appendChild(root);

let calls = 0;
const unregister = UI.actions.register("test.click", function (payload) {
  calls += 1;
  if (!payload || payload.element.id !== "contract-btn") {
    throw new Error("dispatcher must pass the clicked button");
  }
  if (payload.action !== "test.click") throw new Error("dispatcher must expose the action name");
  if (payload.event.type !== "click") throw new Error("dispatcher must expose the click event");
  if (payload.params.itemId !== "42")
    throw new Error("dispatcher must normalize data-* into params");
});

const button = UI.button({
  id: "contract-btn",
  className: "header-btn",
  label: "Contract button",
  action: "test.click",
  params: { itemId: 42 },
  text: "Run",
});

if (button.type !== "button") throw new Error("button factory must force type=button");
if (!button.classList.contains("alice-btn")) throw new Error("button factory must add alice-btn");
if (button.dataset.action !== "test.click") throw new Error("button factory must bind data-action");
if (button.dataset.itemId !== "42") throw new Error("button factory must bind action params");
if (button.getAttribute("aria-label") !== "Contract button")
  throw new Error("button must expose accessible label");

root.appendChild(button);
const unmount = UI.events.mountClicks(root);
const duplicateUnmount = UI.events.mountClicks(root);
if (duplicateUnmount !== unmount)
  throw new Error("duplicate mount must return the active cleanup handle");
button.click();
if (calls !== 1) throw new Error("one click must dispatch exactly one action");

button.disabled = true;
button.click();
if (calls !== 1) throw new Error("disabled button must not dispatch an action");
button.disabled = false;

unmount();
button.click();
if (calls !== 1) throw new Error("unmounted dispatcher must not receive clicks");

const unmountAgain = UI.events.mountClicks(root);
button.click();
if (calls !== 2) throw new Error("remount must restore exactly one click handler");
unmountAgain();

let healthyCalls = 0;
const unregisterBroken = UI.actions.register("test.broken", function () {
  throw new Error("expected action failure");
});
const unregisterHealthy = UI.actions.register("test.healthy", function () {
  healthyCalls += 1;
});
const brokenButton = UI.button({ text: "Broken", action: "test.broken" });
const healthyButton = UI.button({ text: "Healthy", action: "test.healthy" });
root.appendChild(brokenButton);
root.appendChild(healthyButton);
const originalError = console.error;
console.error = function () {};
const unmountFailureTest = UI.events.mountClicks(root);
brokenButton.click();
healthyButton.click();
console.error = originalError;
if (healthyCalls !== 1)
  throw new Error("handler failure must not break the shared click dispatcher");
unmountFailureTest();
unregisterBroken();
unregisterHealthy();

unregister();
let unknownFailed = false;
try {
  UI.actions.dispatch("test.click");
} catch (error) {
  unknownFailed = /Unknown UI action/.test(error.message);
}
if (!unknownFailed) throw new Error("unregistered action must not remain callable");

console.log("UI action contract tests passed");
