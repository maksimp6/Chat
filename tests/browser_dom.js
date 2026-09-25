"use strict";

class EventTargetShim {
    constructor() { this._listeners = Object.create(null); }
    addEventListener(type, handler) {
        if (typeof handler !== "function") return;
        (this._listeners[type] ||= []).push(handler);
    }
    removeEventListener(type, handler) {
        const list = this._listeners[type];
        if (!list) return;
        this._listeners[type] = list.filter((item) => item !== handler);
    }
    dispatchEvent(event) {
        event.target ||= this;
        event.currentTarget = this;
        for (const handler of [...(this._listeners[event.type] || [])]) handler.call(this, event);
        return true;
    }
}

class ClassList {
    constructor(element) { this.element = element; }
    _set() { return new Set((this.element.className || "").split(/\s+/).filter(Boolean)); }
    _write(values) { this.element.className = [...values].join(" "); }
    contains(value) { return this._set().has(value); }
    add(...values) { const set = this._set(); values.forEach((value) => set.add(value)); this._write(set); }
    remove(...values) { const set = this._set(); values.forEach((value) => set.delete(value)); this._write(set); }
}

class ElementShim extends EventTargetShim {
    constructor(tagName, attributes = {}) {
        super();
        this.tagName = tagName.toUpperCase();
        this.attributes = {...attributes};
        this.children = [];
        this.parentNode = null;
        this.style = {removeProperty() {}};
        this.dataset = Object.create(null);
        this.classList = new ClassList(this);
        this._text = "";
        this._rect = {left: 0, top: 0, width: 0, height: 0};

        for (const [name, value] of Object.entries(attributes)) {
            if (name.startsWith("data-")) {
                const key = name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
                this.dataset[key] = value;
            }
        }
    }

    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }\n    setAttribute(name, value) { this.attributes[name] = String(value); }\n    removeAttribute(name) { delete this.attributes[name]; }\n    hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name); }\n\n    get id() { return this.attributes.id || ""; }
    set id(value) { this.attributes.id = String(value); }
    get className() { return this.attributes.class || ""; }
    set className(value) { this.attributes.class = String(value); }
    get textContent() {
        if (this.children.length) return this._text + this.children.map((child) => child.textContent).join("");
        return this._text;
    }
    set textContent(value) { this._text = String(value ?? ""); this.children = []; }
    get innerHTML() { return this.children.map((child) => serialize(child)).join("") || this._text; }
    set innerHTML(value) { this.children = []; this._text = ""; parseHTML(String(value), this); }

    appendChild(child) {
        if (child.parentNode) child.parentNode.removeChild(child);
        child.parentNode = this;
        this.children.push(child);
        return child;
    }

    removeChild(child) {
        const index = this.children.indexOf(child);
        if (index >= 0) { this.children.splice(index, 1); child.parentNode = null; }
        return child;
    }

    remove() { if (this.parentNode) this.parentNode.removeChild(this); }

    replaceWith(replacement) {
        if (!this.parentNode) return;
        const parent = this.parentNode;
        const index = parent.children.indexOf(this);
        this.parentNode = null;
        if (index >= 0) { replacement.parentNode = parent; parent.children[index] = replacement; }
    }

    click() { this.dispatchEvent({type: "click", target: this}); }
    getBoundingClientRect() { return {...this._rect}; }

    querySelectorAll(selector) {
        const parts = selector.trim().split(/\s+/).filter(Boolean);
        let current = [this];
        for (const part of parts) {
            const next = [];
            for (const root of current) {
                walk(root, (node) => { if (node !== root && matches(node, part)) next.push(node); });
            }
            current = unique(next);
        }
        return current;
    }

    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

class DocumentShim extends ElementShim {
    constructor(html) {
        super("#document");
        this.readyState = "loading";
        this.body = new ElementShim("body");
        this.appendChild(this.body);
        parseHTML(html, this.body);
    }

    createElement(tagName) { return new ElementShim(tagName); }
    getElementById(id) { return this.querySelector("#" + id); }
}

class WindowShim extends EventTargetShim {
    constructor(document) {
        super();
        this.document = document;
        this.setTimeout = setTimeout;
        this.clearTimeout = clearTimeout;
        this.Promise = Promise;
    }
}

function walk(node, callback) {
    for (const child of node.children || []) {
        callback(child);
        walk(child, callback);
    }
}

function unique(nodes) { return [...new Set(nodes)]; }

function matches(element, selector) {
    const tag = selector.match(/^[a-zA-Z][\w-]*/);
    if (tag && element.tagName !== tag[0].toUpperCase()) return false;
    const id = selector.match(/#([\w-]+)/);
    if (id && element.id !== id[1]) return false;
    for (const className of selector.matchAll(/\.([\w-]+)/g)) {
        if (!element.classList.contains(className[1])) return false;
    }
    for (const attr of selector.matchAll(/\[([\w-]+)(?:=["']([^"']*)["'])?\]/g)) {
        const actual = element.attributes[attr[1]];
        if (actual === undefined) return false;
        if (attr[2] !== undefined && actual !== attr[2]) return false;
    }
    return true;
}

function parseAttributes(source) {
    const attributes = {};
    const attrRe = /([:\w-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>]+)))?/g;
    let match;
    while ((match = attrRe.exec(source))) {
        const name = match[1];
        const value = match[2] ?? match[3] ?? match[4] ?? "";
        if (name !== "DOCTYPE") attributes[name] = value;
    }
    return attributes;
}

function parseHTML(html, root) {
    const tokenRe = /<!--[\s\S]*?-->|<\/?[a-zA-Z][^>]*>/g;
    const stack = [root];
    let cursor = 0;
    let token;
    while ((token = tokenRe.exec(html))) {
        const text = html.slice(cursor, token.index);
        if (text) stack[stack.length - 1]._text += decodeEntities(text);
        const raw = token[0];
        cursor = tokenRe.lastIndex;
        if (raw.startsWith("<!--") || /^<!DOCTYPE/i.test(raw)) continue;
        if (/^<\//.test(raw)) { if (stack.length > 1) stack.pop(); continue; }
        const nameMatch = raw.match(/^<([a-zA-Z][\w-]*)/);
        if (!nameMatch) continue;
        const element = new ElementShim(nameMatch[1], parseAttributes(raw.slice(nameMatch[0].length, -1)));
        stack[stack.length - 1].appendChild(element);
        const voidElement = /^(area|base|br|col|embed|hr|img|input|link|meta|param|source|track|wbr)$/i.test(element.tagName);
        if (!voidElement && !/\/>$/.test(raw)) stack.push(element);
    }
    const tail = html.slice(cursor);
    if (tail) stack[stack.length - 1]._text += decodeEntities(tail);
}

function decodeEntities(value) {
    return value
        .replace(/&times;/g, "×")
        .replace(/&amp;/g, "&")
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'")
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">");
}

function serialize(element) {
    const attrs = Object.entries(element.attributes)
        .map(([key, value]) => value === "" ? key : key + '="' + value + '"')
        .join(" ");
    return "<" + element.tagName.toLowerCase() + (attrs ? " " + attrs : "") + ">" +
        element._text + element.children.map(serialize).join("") +
        "</" + element.tagName.toLowerCase() + ">";
}

function applyHeaderFlexLayout(document, cssText) {
    const header = document.getElementById("header");
    if (!header) throw new Error("header not found");

    const ruleMatches = [...cssText.matchAll(/\.alice-pro-app\s+#header\s*\{([\s\S]*?)\}/g)];
    if (!ruleMatches.length) throw new Error("browser-layout rule for #header not found");

    const ruleBody = ruleMatches.at(-1)[1];
    const declarations = Object.fromEntries(
        ruleBody.split(";").map((part) => part.trim()).filter(Boolean).map((part) => {
            const index = part.indexOf(":");
            return [part.slice(0, index).trim(), part.slice(index + 1).trim()];
        })
    );

    assertLayoutRule(declarations, "flex-direction", "row");
    assertLayoutRule(declarations, "flex-wrap", "nowrap");

    const rows = header.querySelectorAll(".header-row");
    rows.forEach((row, index) => {
        row._rect = {left: index * 100, top: 0, width: 100, height: 40};
        row.children.forEach((child, childIndex) => {
            child._rect = {left: row._rect.left + childIndex * 40, top: 0, width: 32, height: 32};
        });
    });
}

function assertLayoutRule(declarations, property, expected) {
    if (declarations[property] !== expected) {
        throw new Error("Expected " + property + ": " + expected + ", got " + (declarations[property] || "<missing>"));
    }
}

module.exports = {DocumentShim, WindowShim, applyHeaderFlexLayout};
