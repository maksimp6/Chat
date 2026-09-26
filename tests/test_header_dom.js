const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const {
    BrowserShim,
    applyHeaderFlexLayout,
} = require("./browser_dom");

async function flush() {
    await Promise.resolve();
    await new Promise((resolve) => setImmediate(resolve));
}

(async () => {
    const html = fs.readFileSync("templates/index.html", "utf8");
    const css = fs.readFileSync("static/style.css", "utf8");
    let requestCalls = 0;
    const browser = new BrowserShim(html);
    const {document, window, context} = browser.load(["static/project_tree.js"], {
        fetch: async () => {
            requestCalls += 1;
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    root: "/alice-pro",
                    nodes: [{
                        name: "src",
                        path: "src",
                        kind: "directory",
                        icon: "📁",
                        children: [{
                            name: "app.py",
                            path: "src/app.py",
                            kind: "file",
                            icon: "📄",
                        }],
                    }],
                }),
            };
        },
    });
    applyHeaderFlexLayout(document, css);

    const header = document.getElementById("header");
    const rows = header.querySelectorAll(".header-row");
    assert.equal(rows.length, 2);

    const buttons = header.querySelectorAll(".header-btn");
    const expectedIds = [
        "menu-btn", "system-status-btn", "model-btn", "tools-btn", "ssh-runtime-btn",
        "mcp-btn", "settings-btn", "file-manager-btn", "treasury-btn",
        "dozzle-btn", "project-tree-btn", "departments-btn", "update-app-btn",
        "provider-credentials-btn", "memory-btn", "theme-toggle",
    ];
    assert.deepEqual(buttons.map((button) => button.id), expectedIds);

    for (const button of buttons) {
        assert.equal(button.parentNode.parentNode, header, button.id + " is not in #header");
        assert.equal(button.getBoundingClientRect().top, 0, button.id + " wrapped to another line");
    }
    assert.equal(new Set(buttons.map((button) => button.getBoundingClientRect().top)).size, 1);
    assert.equal(new Set(rows.map((row) => row.getBoundingClientRect().top)).size, 1);

    const projectTreeButton = document.getElementById("project-tree-btn");
    assert.ok(projectTreeButton);
    projectTreeButton.click();
    await flush();

    assert.equal(requestCalls, 1);
    const modal = document.getElementById("project-tree-modal");
    assert.ok(modal);
    assert.equal(modal.classList.contains("visible"), true);
    assert.equal(modal.querySelector("h3").textContent, "🌳 Структура проекта");
    assert.equal(modal.querySelectorAll(".project-tree-row").length, 2);
    assert.equal(modal.querySelector(".project-tree-row").querySelector(".project-tree-icon").textContent, "📁");

    window.AliceDispatcher.request = async () => ({ok: false, status: 503});
    modal.remove();
    projectTreeButton.click();
    await flush();

    const errorModal = document.getElementById("project-tree-modal");
    assert.match(
        errorModal.textContent,
        /Ошибка структуры проекта: Project tree request failed: HTTP 503/
    );
    assert.ok(errorModal.querySelectorAll("button").length >= 2);

    console.log("browser DOM regression checks passed");
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
