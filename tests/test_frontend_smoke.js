"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const {DocumentShim, WindowShim} = require("./browser_dom");

function storage() {
    const data = Object.create(null);
    return {
        getItem: (key) => Object.prototype.hasOwnProperty.call(data, key) ? data[key] : null,
        setItem: (key, value) => { data[key] = String(value); },
        removeItem: (key) => { delete data[key]; },
        clear: () => { Object.keys(data).forEach((key) => delete data[key]); }
    };
}

function load(sources, html, extra = {}) {
    const document = new DocumentShim(html);
    const window = new WindowShim(document);
    window.localStorage = storage();
    const context = {
        console,
        document,
        window,
        localStorage: window.localStorage,
        URLSearchParams,
        AbortController,
        setTimeout,
        clearTimeout,
        fetch: async () => ({ok: true, json: async () => ({conversations: [], text: {}, voice: {}})}),
        ...extra
    };
    vm.createContext(context);
    for (const source of sources) {
        vm.runInContext(fs.readFileSync(source, "utf8"), context, {filename: source});
    }
    return {document, window, context};
}

function click(element) {
    assert.ok(element, "expected element");
    element.click();
}

{
    const {document, window} = load(
        ["static/core.js"],
        '<button id="theme-toggle"></button>'
    );
    assert.equal(window.AliceTheme.getStored(), "light");
    assert.equal(window.AliceTheme.apply("dark", true), "dark");
    assert.equal(document.documentElement.getAttribute("data-theme"), "dark");
    assert.equal(window.localStorage.getItem("theme"), "dark");

    click(document.getElementById("theme-toggle"));
    assert.equal(window.AliceTheme.getStored(), "dim");
    assert.equal(document.getElementById("theme-toggle").getAttribute("aria-label"), "Цветовая схема: Приглушённая");

    assert.equal(window.AliceTheme.apply("not-a-theme", false), "light");
}

{
    const {document, window, context} = load(
        ["static/core.js", "static/models.js"],
        '<button id="model-btn"></button><div id="model-modal" class="modal"><div class="modal-content"><button id="close-modal"></button><div id="model-list"></div></div></div>'
    );
    context.modelsData = {
        text: {
            lite: {name: "Lite", input: "1", output: "2", multimodal: true},
            full: {name: "Full", input: "3", output: "4"}
        },
        voice: {
            voice: {name: "Voice", input: "5", output: "6"}
        }
    };
    context.currentModel = "lite";
    window.changeModel = (model) => { context.currentModel = model; };

    context.renderModelModal();
    const options = document.getElementById("model-list").querySelectorAll(".model-option");
    assert.equal(options.length, 3);
    assert.ok(options[0].classList.contains("active"));
    assert.match(options[0].textContent, /Lite/);
    assert.match(options[0].textContent, /📷/);
    assert.match(options[2].textContent, /🎤/);

    click(document.getElementById("close-modal"));
    assert.equal(document.getElementById("model-modal").classList.contains("visible"), false);

    document.getElementById("model-modal").classList.add("visible");
    click(options[1]);
    assert.equal(context.currentModel, "full");
    assert.equal(document.getElementById("model-modal").classList.contains("visible"), false);
}

{
    const {document, window, context} = load(
        ["static/core.js", "static/sidebar.js"],
        '<button id="menu-btn"></button><button id="close-sidebar-btn"></button><div id="overlay"></div><aside id="sidebar"></aside><button id="new-chat-btn"></button><div id="conv-list"></div>'
    );
    context.conversations = [{id: "c1", title: "Первый чат", model: "lite"}];
    context.currentConvId = "c1";
    context.renderSidebar();

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
    const {document, window} = load(
        ["static/memory_panel.js"],
        '<div id="memoryModal" hidden><button id="memoryCloseBtn"></button><button id="memoryClearBtn"></button><input id="memEnabled"><input id="memLimit"><span id="memCount"></span><div id="memoryFactsList"></div>'
    );
    window.fetch = async (url, init) => {
        if (url === "/api/memory/manage") {
            return {ok: true, json: async () => ({config: {enabled: true, max_context_facts: 7}, facts: [{category: "work", fact: "Test fact"}]})};
        }
        if (url === "/api/memory/config") {
            assert.equal(init.method, "PUT");
            return {ok: true, json: async () => ({})};
        }
        throw new Error("unexpected fetch: " + url);
    };
    window.openMemoryModal();
}
window.dispatchEvent({type: "test"});
console.log("frontend smoke tests passed");
