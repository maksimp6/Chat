"use strict";

const dns = require("node:dns/promises");
const { DocumentShim } = require("./browser_dom");

const DEFAULT_MAX_BYTES = 2 * 1024 * 1024;
const DEFAULT_TEXT_LIMIT = 8000;
const DEFAULT_TIMEOUT_MS = 15000;
const MAX_REDIRECTS = 5;
const SECRET_INPUT_NAME = /token|secret|passw|api[_-]?key|session|auth|cookie|credential|csrf/i;

class EmulatorError extends Error {}

function collapse(text) {
  return String(text || "")
    .replace(/\s+/g, " ")
    .trim();
}

// Literal private, loopback, link-local and metadata hosts. Pages are fetched
// on behalf of the agent, so these must never be reachable through the emulator.
function isSecretInput(type, name) {
  return (type || "").toLowerCase() === "password" || SECRET_INPUT_NAME.test(name || "");
}

function isNonPublicHost(hostname) {
  const host = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local")) return true;
  if (host === "::1" || host === "::" || host.startsWith("fc") || host.startsWith("fd")) return true;
  if (host.startsWith("fe80:") || host.startsWith("::ffff:")) return true;
  const ipv4 = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (!ipv4) return false;
  const [a, b] = [Number(ipv4[1]), Number(ipv4[2])];
  return (
    a === 0 ||
    a === 10 ||
    a === 127 ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    (a === 100 && b >= 64 && b <= 127)
  );
}

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

  // Every hop is checked before it is requested: the host name, the address it resolves to, and
  // the redirect it may answer with. Following redirects inside fetch would check only the end.
  async _assertPublicUrl(url) {
    if (this.allowNonPublicHosts) return;
    if (isNonPublicHost(url.hostname)) throw new EmulatorError("address is not public: " + url.hostname);
    const { address } = await dns.lookup(url.hostname.replace(/^\[|\]$/g, ""));
    if (isNonPublicHost(address)) throw new EmulatorError("address is not public: " + address);
  }

  async navigate(target, init = {}) {
    let url = this._resolve(target);
    let requestInit = init;
    let response;
    for (let hop = 0; ; hop += 1) {
      await this._assertPublicUrl(url);
      response = await this.fetch(url.href, {
        ...requestInit,
        redirect: "manual",
        signal: AbortSignal.timeout(this.timeoutMs),
      });
      const location =
        response.status >= 300 && response.status < 400 ? response.headers.get("location") : null;
      if (!location) break;
      if (hop >= MAX_REDIRECTS) throw new EmulatorError("too many redirects");
      url = new URL(location, url);
      requestInit = {};
    }
    const html = await readCapped(response, this.maxBytes);
    this.document = new DocumentShim(html);
    this.url = url.href;
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
          value: isSecretInput(el.getAttribute("type"), el.getAttribute("name")) ? null : el.value,
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
