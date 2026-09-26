"use strict";

const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const {BrowserShim} = require("./browser_dom");

const ROOT = require("path").resolve(__dirname, "..");

async function main() {
    const browser = new BrowserShim();
    const button = browser.document.createElement("button");
    button.id = "system-status-btn";
    button.setAttribute("type", "button");
    browser.document.body.appendChild(button);

    const panel = browser.document.createElement("div");
    panel.id = "alice-system-status";
    panel.setAttribute("hidden", "");
    panel.setAttribute("role", "dialog");
    browser.document.body.appendChild(panel);

    const content = browser.document.createElement("div");
    content.className = "alice-system-status-content";
    panel.appendChild(content);

    const close = browser.document.createElement("button");
    close.className = "alice-btn alice-system-status-close";
    close.setAttribute("type", "button");
    content.appendChild(close);

    const title = browser.document.createElement("h2");
    title.className = "alice-system-status-title";
    content.appendChild(title);

    const list = browser.document.createElement("div");
    list.className = "alice-system-status-list";
    content.appendChild(list);

    const context = browser.context();
    const load = (name) => {
        const source = fs.readFileSync(require("path").join(ROOT, "static", name), "utf8");
        vm.runInContext(source, context, {filename: name});
    };

    load("core_api.js");
    load("dispatcher.js");
    load("system_status.js");
    browser.document.dispatchEvent(new browser.BrowserEvent("DOMContentLoaded"));

    const core = browser.window.AliceCoreAPI;
    assert.strictEqual(core.apiVersion, "1");

    const moduleContext = core.module.register({
        id: "test-module",
        version: "1.0.0",
        apiVersion: "1",
        capabilities: ["storage.read", "network"]
    });

    moduleContext.status.set("running", "Тестовая операция token=visible-secret", {
        revocable: true,
        authorization: "do-not-leak"
    });
    const statusSnapshot = core.status.snapshot();
    assert.ok(!JSON.stringify(statusSnapshot).includes("visible-secret"));

    const trace = moduleContext.trace.begin("secret-operation", {
        api_key: "top-secret",
        safe: "visible"
    });
    trace.toolCall("test-tool", {
        token: "secret-token",
        value: "safe"
    });
    trace.response({status: 200, password: "hidden"});
    trace.response("Bearer plain-secret");
    const snapshot = trace.end("completed");

    const serialized = JSON.stringify(snapshot);
    assert.ok(!serialized.includes("top-secret"));
    assert.ok(!serialized.includes("secret-token"));
    assert.ok(!serialized.includes("hidden"));
    assert.ok(!serialized.includes("plain-secret"));
    assert.ok(serialized.includes("visible"));

    assert.strictEqual(moduleContext.security.require("storage.read"), true);
    assert.strictEqual(core.revocation.isRevoked("module", "test-module"), false);

    core.revocation.revokeAll("test-module", "user_requested");
    assert.strictEqual(core.revocation.isRevoked("module", "test-module"), true);
    assert.throws(
        () => moduleContext.security.require("storage.read"),
        /Capability revoked/
    );

    button.click();
    assert.strictEqual(panel.hasAttribute("hidden"), false);
    assert.strictEqual(button.getAttribute("aria-expanded"), "true");

    close.click();
    assert.strictEqual(panel.hasAttribute("hidden"), true);
    assert.strictEqual(button.getAttribute("aria-expanded"), "false");

    const networkTrace = core.trace.begin("network-test", {authorization: "secret"});
    networkTrace.event("response", {path: "/api/test", status: 200});
    const networkSnapshot = networkTrace.end("completed");
    assert.strictEqual(networkSnapshot.metadata.authorization, "[REDACTED]");

    console.log("system status/core API contract tests passed");
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
