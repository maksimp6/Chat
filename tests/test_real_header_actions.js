const fs = require("node:fs");
const {BrowserShim} = require("./browser_dom");

const template = fs.readFileSync("templates/index.html", "utf8");
const headerMatch = template.match(/<header id="header"[\s\S]*?<\/header>/);
if (!headerMatch) throw new Error("real header not found in index.html");

const shim = new BrowserShim(headerMatch[0]);
const {window, document} = shim.load(["static/core_api.js", "static/ui_runtime.js"]);

const UI = window.AliceCoreAPI.ui;
const buttons = document.querySelectorAll("#header button[data-action]");
if (!buttons.length) throw new Error("real header action inventory is empty");

const seen = Object.create(null);
const actions = [...new Set(buttons.map((button) => button.dataset.action))];
actions.forEach((action) => {
    if (action === "modal.open" || action === "modal.close") return;
    UI.actions.register(action, function(payload) {
        seen[action] = (seen[action] || 0) + 1;
        if (payload.element.dataset.action !== action) {
            throw new Error("dispatcher payload/action mismatch for " + action);
        }
    });
});

buttons.forEach((button) => {
    const action = button.dataset.action;
    if (action === "modal.open" || action === "modal.close") return;
    const before = seen[action] || 0;
    button.click();
    if ((seen[action] || 0) !== before + 1) {
        throw new Error(button.id + " must dispatch " + action + " exactly once");
    }
});

let recovered = 0;
UI.actions.register("contract.recoverable", function(payload) {
    recovered += 1;
    if (recovered === 1) throw new Error("expected first-click failure");
    if (payload.params.marker !== "still-alive") {
        throw new Error("recovered click must preserve normalized params");
    }
});
const recoverable = UI.button({
    id: "recoverable-real-click",
    text: "Recover",
    action: "contract.recoverable",
    params: {marker: "still-alive"}
});
document.body.appendChild(recoverable);
const originalError = console.error;
let logged = 0;
console.error = function() { logged += 1; };
recoverable.click();
recoverable.click();
console.error = originalError;
if (logged !== 1) throw new Error("first failed click must be logged exactly once");
if (recovered !== 2) throw new Error("failed click must not disable later clicks");

console.log("Real header runtime action tests passed");
