"use strict";

const { DocumentShim } = require("./browser_dom");
const { isIP } = require("node:net");
// Vendored ipaddr.js 2.2.0; see vendor/ipaddr.LICENSE.
const ipaddr = require("./vendor/ipaddr");

const DEFAULT_MAX_BYTES = 2 * 1024 * 1024;
const DEFAULT_TEXT_LIMIT = 8000;
const DEFAULT_TIMEOUT_MS = 15000;

class EmulatorError extends Error {}

function collapse(text) {
  return String(text || "")
    .replace(/\s+/g, " ")
    .trim();
}

// Literal private, loopback, link-local and metadata hosts. Pages are fetched
// on behalf of the agent, so these must never be reachable through the emulator.
function isNonPublicHost(hostname) {
  const host = hostname.replace(/^\[|\]$/g, "").replace(/\.$/, "").toLowerCase();
  if (host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local")) return true;
  // DNS labels such as fc-example.com are not IPv6 literals. URL parsing
  // canonicalizes IP notation before this boundary; net.isIP checks its type.
  if (isIP(host) === 0) return false;
  const range = ipaddr.parse(host).range();
  return NON_PUBLIC_IP_RANGES.has(range);
}

const NON_PUBLIC_IP_RANGES = new Set([
  "unspecified", "broadcast", "multicast", "linkLocal", "loopback",
  "carrierGradeNat", "private", "reserved", "uniqueLocal", "ipv4Mapped",
  "discard", "benchmarking", "deprecated", "orchid2", "droneRemoteIdProtocolEntityTags",
]);

// Read a response body without buffering more than maxBytes of it.
async function readCapped(response, maxBytes) {
  if (!response.body || typeof response.body.getReader !== "function") {
    const text = await response.text();
    if (Buffer.byteLength(text) > maxBytes) {
      throw new EmulatorError("page exceeds emulator size limit");
    }
    return text;
  }
  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel();
      throw new EmulatorError("page exceeds emulator size limit");
    }
    chunks.push(Buffer.from(value));
  }
  return Buffer.concat(chunks).toString("utf8");
}

class EmulatorSession {
  constructor(options = {}) {
    this.fetch = options.fetch || globalThis.fetch;
    this.maxBytes = options.maxBytes || DEFAULT_MAX_BYTES;
    this.textLimit = options.textLimit || DEFAULT_TEXT_LIMIT;
    this.timeoutMs = options.timeoutMs || DEFAULT_TIMEOUT_MS;
    this.allowNonPublicHosts = options.allowNonPublicHosts === true;
    this.document = null;
    this.url = null;
    this.status = null;
  }

  async execute(action, target, value) {
    switch (action) {
      case "navigate":
        return this.navigate(target);
      case "inspect":
        return this.inspect(target);
      case "click":
        return this.click(target);
      case "fill":
        return this.fill(target, value);
      case "assert_state":
        return this.assertState(target, value);
      case "screenshot":
        throw new EmulatorError("screenshot is not supported by the emulator; use inspect");
      default:
        throw new EmulatorError("unsupported action: " + action);
    }
  }

  async navigate(target, init = {}) {
    const url = this._resolve(target);
    if (!this.allowNonPublicHosts && isNonPublicHost(url.hostname)) {
      throw new EmulatorError("address is not public: " + url.hostname);
    }
    const response = await this.fetch(url.href, {
      redirect: "follow",
      signal: AbortSignal.timeout(this.timeoutMs),
      ...init,
    });
    // Redirects were followed by fetch, so the host that actually answered is checked too.
    if (!this.allowNonPublicHosts && response.url && isNonPublicHost(new URL(response.url).hostname)) {
      throw new EmulatorError("redirected to a non-public address");
    }
    const html = await readCapped(response, this.maxBytes);
    this.document = new DocumentShim(html);
    this.url = response.url || url.href;
    this.status = response.status;
    return { url: this.url, status: this.status, title: this._title() };
  }

  inspect(selector) {
    const root = this._root(selector);
    return {
      url: this.url,
      status: this.status,
      title: this._title(),
      text: collapse(this._visibleText(root)).slice(0, this.textLimit),
      links: root.querySelectorAll("a").map((a) => ({
        text: collapse(a.textContent),
        href: this._absolute(a.getAttribute("href")),
      })),
      inputs: [...root.querySelectorAll("input"), ...root.querySelectorAll("textarea")].map(
        (el) => ({
          name: el.getAttribute("name"),
          type: el.getAttribute("type") || el.tagName.toLowerCase(),
          value: (el.getAttribute("type") || "") === "password" ? null : el.value,
        }),
      ),
      buttons: root.querySelectorAll("button").map((b) => collapse(b.textContent)),
    };
  }

  async click(selector) {
    const element = this._find(selector);
    if (element.tagName === "A" && element.getAttribute("href")) {
      return this.navigate(this._absolute(element.getAttribute("href")));
    }
    const type = (element.getAttribute("type") || "submit").toLowerCase();
    const isSubmit =
      (element.tagName === "BUTTON" && type === "submit") ||
      (element.tagName === "INPUT" && type === "submit");
    const form = isSubmit ? element.closest("form") : null;
    if (form) return this._submit(form);
    element.click();
    return { url: this.url, clicked: selector };
  }

  fill(selector, value) {
    const element = this._find(selector);
    if (!["INPUT", "TEXTAREA", "SELECT"].includes(element.tagName)) {
      throw new EmulatorError("element is not fillable: " + selector);
    }
    element.value = String(value ?? "");
    return { filled: selector };
  }

  assertState(selector, expected) {
    const element = this.document && this.document.querySelector(selector);
    const text = element ? collapse(element.textContent) : "";
    const ok = Boolean(element) && (expected == null || text.includes(String(expected)));
    return { ok, found: Boolean(element), text: text.slice(0, 500) };
  }

  async _submit(form) {
    const params = new URLSearchParams();
    for (const el of [
      ...form.querySelectorAll("input"),
      ...form.querySelectorAll("textarea"),
      ...form.querySelectorAll("select"),
    ]) {
      const name = el.getAttribute("name");
      const type = (el.getAttribute("type") || "").toLowerCase();
      if (!name || ["submit", "button", "reset"].includes(type)) continue;
      if (["checkbox", "radio"].includes(type) && !el.checked) continue;
      params.append(name, el.value);
    }
    const action = this._absolute(form.getAttribute("action") || this.url);
    if ((form.getAttribute("method") || "get").toLowerCase() === "post") {
      return this.navigate(action, {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: params.toString(),
      });
    }
    const url = new URL(action);
    url.search = params.toString();
    return this.navigate(url.href);
  }

  _resolve(target) {
    let url;
    try {
      url = new URL(target, this.url || undefined);
    } catch {
      throw new EmulatorError("invalid URL: " + target);
    }
    if (!["http:", "https:"].includes(url.protocol)) {
      throw new EmulatorError("only http(s) URLs are allowed");
    }
    return url;
  }

  _absolute(href) {
    if (!href) return null;
    try {
      return new URL(href, this.url || undefined).href;
    } catch {
      return href;
    }
  }

  _requireDocument() {
    if (!this.document) throw new EmulatorError("no page loaded; navigate first");
    return this.document;
  }

  _root(selector) {
    const document = this._requireDocument();
    if (!selector || selector === "body" || selector === "page") return document.body;
    return this._find(selector);
  }

  _find(selector) {
    const element = this._requireDocument().querySelector(selector);
    if (!element) throw new EmulatorError("element not found: " + selector);
    return element;
  }

  _title() {
    const title = this.document && this.document.querySelector("title");
    return title ? collapse(title.textContent) : "";
  }

  _visibleText(node) {
    if (["SCRIPT", "STYLE", "TITLE", "HEAD"].includes(node.tagName)) return "";
    return (
      node._text + " " + node.children.map((child) => this._visibleText(child)).join(" ")
    );
  }
}

module.exports = { EmulatorSession, EmulatorError };
