"use strict";

// WCAG 2.2 AA structural checks on templates/index.html (BrowserShim).
// Violation counts per rule may only go down; lower BASELINE with each fix.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { BrowserShim } = require("./browser_dom");

const ROOT = path.resolve(__dirname, "..");
const BASELINE = {
  "4.1.2 control without accessible name": 0,
  "1.3.1 form field without label": 2,
  "4.1.2 dialog without role/aria-modal/aria-labelledby": 1,
  "4.1.1 duplicate id": 0,
  "1.1.1 image without alt": 0,
  "3.1.1 document without lang": 0,
};

function text(node) {
  return (node.textContent || "").replace(/\s+/g, " ").trim();
}

function isTemplated(value) {
  return typeof value === "string" && value.includes("{{");
}

function audit(html) {
  const { document } = new BrowserShim(html);
  const all = document.querySelectorAll("*");
  const ids = new Map();
  for (const node of all) {
    const id = node.getAttribute("id");
    if (id && !isTemplated(id)) ids.set(id, (ids.get(id) || 0) + 1);
  }
  const hasId = (id) => ids.has(id);
  const violations = Object.fromEntries(Object.keys(BASELINE).map((rule) => [rule, []]));
  const report = (rule, node) => violations[rule].push(node.getAttribute("id") || node.tagName);

  for (const node of [...document.querySelectorAll("button"), ...document.querySelectorAll("a")]) {
    const labelledBy = node.getAttribute("aria-labelledby");
    const named =
      text(node) ||
      node.getAttribute("aria-label") ||
      (labelledBy && hasId(labelledBy)) ||
      node.getAttribute("title");
    if (!named) report("4.1.2 control without accessible name", node);
  }

  const labelFor = new Set(document.querySelectorAll("label").map((l) => l.getAttribute("for")));
  const fields = [
    ...document.querySelectorAll("input"),
    ...document.querySelectorAll("textarea"),
    ...document.querySelectorAll("select"),
  ];
  for (const node of fields) {
    if (["hidden", "submit", "button"].includes(node.getAttribute("type"))) continue;
    const labelled =
      node.getAttribute("aria-label") ||
      node.getAttribute("aria-labelledby") ||
      labelFor.has(node.getAttribute("id")) ||
      node.closest("label");
    if (!labelled) report("1.3.1 form field without label", node);
  }

  const dialogs = new Set([
    ...document.querySelectorAll(".modal"),
    ...document.querySelectorAll("[role='dialog']"),
  ]);
  for (const node of dialogs) {
    const labelledBy = node.getAttribute("aria-labelledby");
    const ok =
      node.getAttribute("role") === "dialog" &&
      node.getAttribute("aria-modal") === "true" &&
      labelledBy &&
      hasId(labelledBy);
    if (!ok) report("4.1.2 dialog without role/aria-modal/aria-labelledby", node);
  }

  for (const [id, count] of ids) {
    if (count > 1) violations["4.1.1 duplicate id"].push(id);
  }
  for (const node of document.querySelectorAll("img")) {
    if (node.getAttribute("alt") === null) report("1.1.1 image without alt", node);
  }
  const htmlNode = document.querySelector("html");
  if (!htmlNode || !htmlNode.getAttribute("lang")) {
    violations["3.1.1 document without lang"].push("html");
  }
  return violations;
}

function main() {
  const probe = audit(
    '<html><body><button></button><input id="x"><div class="modal"></div>' +
      '<img src="a.png"><p id="d"></p><p id="d"></p></body></html>',
  );
  for (const rule of Object.keys(BASELINE)) {
    assert.ok(probe[rule].length > 0, "detector must catch: " + rule);
  }

  const html = fs.readFileSync(path.join(ROOT, "templates", "index.html"), "utf8");
  const violations = audit(html);
  for (const [rule, allowed] of Object.entries(BASELINE)) {
    const found = violations[rule];
    assert.ok(found.length <= allowed, `${rule}: ${found.length} > ${allowed}: ${found}`);
    assert.equal(found.length, allowed, `${rule}: lower BASELINE to ${found.length}`);
  }
  console.log("accessibility tests passed");
}

main();
