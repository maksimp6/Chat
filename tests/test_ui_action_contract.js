const fs = require("fs");
const vm = require("vm");
const {BrowserShim} = require("./browser_dom");

const shim = new BrowserShim();
const context = shim.createContext();
vm.runInContext(fs.readFileSync("static/core_api.js", "utf8"), context);

const UI = context.window.AliceCoreAPI.ui;
const root = context.document.createElement("div");
context.document.body.appendChild(root);

let calls = 0;
const unregister = UI.actions.register("test.click", function(payload) {
    calls += 1;
    if (!payload || payload.element.id !== "contract-btn") {
        throw new Error("dispatcher must pass the clicked button");
    }
});

const button = UI.button({
    id: "contract-btn",
    className: "header-btn",
    label: "Contract button",
    action: "test.click",
    text: "Run"
});

if (button.type !== "button") throw new Error("button factory must force type=button");
if (!button.classList.contains("alice-btn")) throw new Error("button factory must add alice-btn");
if (button.dataset.action !== "test.click") throw new Error("button factory must bind data-action");
if (button.getAttribute("aria-label") !== "Contract button") throw new Error("button must expose accessible label");

root.appendChild(button);
const unmount = UI.events.mountClicks(root);
UI.events.mountClicks(root);
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

unregister();
let unknownFailed = false;
try {
    UI.actions.dispatch("test.click");
} catch (error) {
    unknownFailed = /Unknown UI action/.test(error.message);
}
if (!unknownFailed) throw new Error("unregistered action must not remain callable");

console.log("UI action contract tests passed");
