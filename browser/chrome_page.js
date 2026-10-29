/*
 * Logos browser page helper.
 *
 * Installed by browser/chrome.py into the isolated world "logos" of the main
 * frame (Page.createIsolatedWorld), so page scripts can neither see nor change
 * it; the DOM is shared, the JavaScript globals are not. Before installing,
 * Python replaces the three placeholder identifiers (the ref kinds, the
 * attribute names and the ref attribute) with JSON from browser/page.py, so
 * both engines name the same elements the same way. `node --check` validates
 * this file as it is.
 *
 * The function between the two serializer marker comments is also sent on its
 * own (Runtime.callFunctionOn in the page's main world) to turn the result of
 * browser_evaluate into JSON; it must stay a complete function expression that
 * uses nothing outside itself.
 *
 * All tree walks are iterative (explicit stacks), so deep documents and deep
 * values never overflow the call stack.
 */
(function () {
  "use strict";

  const SERIALIZER = /*__LOGOS_SERIALIZER__*/ function () {
    "use strict";
    const REF_ATTR = __LOGOS_REF_ATTR__;
    const tagOf = Object.prototype.toString;
    const ancestors = new Set();
    const stack = [];
    const holder = { value: null };
    const clean = (text) =>
      String(text == null ? "" : text)
        .replace(/\s+/g, " ")
        .trim();
    const failure = (error) => {
      let text;
      try {
        text = error && error.message ? String(error.message) : String(error);
      } catch (ignored) {
        text = "?";
      }
      return "[ошибка: " + text + "]";
    };
    const isWindow = (value) => {
      try {
        return value.window === value && value.self === value;
      } catch (ignored) {
        return false;
      }
    };
    const isNode = (value) => {
      try {
        return typeof value.nodeType === "number" && typeof value.nodeName === "string";
      } catch (ignored) {
        return false;
      }
    };
    const nodeSummary = (node) => {
      const out = { node: "", id: null, classes: [], text: "" };
      try {
        const name = node.nodeType === 1 ? node.localName || node.nodeName : node.nodeName;
        out.node = String(name).toLowerCase();
        if (node.nodeType === 1) {
          out.id = node.id || null;
          out.classes = node.classList ? Array.from(node.classList) : [];
          const ref = node.getAttribute(REF_ATTR);
          if (ref) {
            out.ref = ref;
          }
        }
        if (node.nodeType === 9) {
          out.text = clean(node.documentElement ? node.documentElement.textContent : "");
        } else {
          out.text = clean(node.textContent);
        }
      } catch (error) {
        out.text = failure(error);
      }
      return out;
    };
    const keysOf = (value) => {
      const keys = [];
      const seen = new Set();
      try {
        for (const key of Object.keys(value)) {
          seen.add(key);
          keys.push(key);
        }
      } catch (ignored) {
        // A proxy or exotic object without enumerable keys.
      }
      let proto = null;
      try {
        proto = Object.getPrototypeOf(value);
      } catch (ignored) {
        proto = null;
      }
      if (proto !== null && proto !== Object.prototype) {
        // Platform objects (navigator, events, rects) keep their data in inherited getters.
        try {
          for (const key in value) {
            if (seen.has(key)) {
              continue;
            }
            let item;
            try {
              item = value[key];
            } catch (ignored) {
              continue;
            }
            if (typeof item === "function") {
              continue;
            }
            seen.add(key);
            keys.push(key);
          }
        } catch (ignored) {
          // for…in itself may throw on exotic objects.
        }
      }
      return keys;
    };
    // A value becomes either a final JSON value ({done}) or a container to fill.
    const open = (value, allowJson) => {
      if (value === undefined || value === null) {
        return { done: null };
      }
      const type = typeof value;
      if (type === "string" || type === "boolean") {
        return { done: value };
      }
      if (type === "number") {
        if (Number.isNaN(value)) {
          return { done: "NaN" };
        }
        if (!Number.isFinite(value)) {
          return { done: value > 0 ? "Infinity" : "-Infinity" };
        }
        return { done: Object.is(value, -0) ? 0 : value };
      }
      if (type === "bigint" || type === "symbol") {
        return { done: String(value) };
      }
      if (type === "function") {
        let name = "";
        try {
          name = value.name;
        } catch (ignored) {
          name = "";
        }
        return { done: "[function " + (name || "anonymous") + "]" };
      }
      if (ancestors.has(value)) {
        return { done: "[цикл]" };
      }
      if (isWindow(value)) {
        let href;
        try {
          href = String(value.location.href);
        } catch (ignored) {
          href = "[другой источник]";
        }
        return { done: { window: href } };
      }
      if (isNode(value)) {
        return { done: nodeSummary(value) };
      }
      let tag;
      try {
        tag = tagOf.call(value);
      } catch (ignored) {
        tag = "[object Object]";
      }
      try {
        if (tag === "[object Date]") {
          const time = value.getTime();
          return { done: Number.isNaN(time) ? "Invalid Date" : value.toISOString() };
        }
        if (tag === "[object RegExp]") {
          return { done: String(value) };
        }
        if (tag === "[object Promise]") {
          return { done: "[Promise]" };
        }
        if (tag === "[object WeakMap]" || tag === "[object WeakSet]" || tag === "[object WeakRef]") {
          return { done: "[" + tag.slice(8, -1) + "]" };
        }
        if (tag === "[object Error]" || value instanceof Error) {
          return {
            done: {
              name: String(value.name),
              message: String(value.message),
              stack: value.stack == null ? null : String(value.stack),
            },
          };
        }
        if (tag === "[object ArrayBuffer]" || tag === "[object SharedArrayBuffer]") {
          return { container: [], source: value, list: Array.from(new Uint8Array(value)) };
        }
        if (ArrayBuffer.isView(value)) {
          const list =
            tag === "[object DataView]"
              ? Array.from(new Uint8Array(value.buffer, value.byteOffset, value.byteLength))
              : Array.from(value);
          return { container: [], source: value, list };
        }
        if (tag === "[object Map]") {
          return { container: [], source: value, list: Array.from(value.entries()) };
        }
        if (tag === "[object Set]") {
          return { container: [], source: value, list: Array.from(value.values()) };
        }
        if (Array.isArray(value)) {
          return { container: [], source: value, list: value };
        }
        if (allowJson && typeof value.toJSON === "function") {
          const replaced = value.toJSON();
          if (replaced !== value) {
            return { replace: replaced };
          }
        }
      } catch (error) {
        return { done: failure(error) };
      }
      return { container: Object.create(null), source: value, keys: keysOf(value) };
    };
    const place = (target, key, value) => {
      if (target === null) {
        holder.value = value;
      } else {
        target[key] = value;
      }
    };
    const visit = (target, key, value) => {
      let opened = open(value, true);
      if (Object.prototype.hasOwnProperty.call(opened, "replace")) {
        opened = open(opened.replace, false);
      }
      if (Object.prototype.hasOwnProperty.call(opened, "done")) {
        place(target, key, opened.done);
        return;
      }
      place(target, key, opened.container);
      ancestors.add(opened.source);
      stack.push({
        container: opened.container,
        source: opened.source,
        list: opened.list || null,
        keys: opened.keys || null,
        index: 0,
      });
    };
    visit(null, null, this);
    while (stack.length) {
      const frame = stack[stack.length - 1];
      const size = frame.list ? frame.list.length : frame.keys.length;
      if (frame.index >= size) {
        stack.pop();
        ancestors.delete(frame.source);
        continue;
      }
      const position = frame.index;
      frame.index += 1;
      if (frame.list) {
        let item;
        try {
          item = frame.list[position];
        } catch (error) {
          frame.container[position] = failure(error);
          continue;
        }
        visit(frame.container, position, item);
      } else {
        const key = frame.keys[position];
        let item;
        try {
          item = frame.source[key];
        } catch (error) {
          frame.container[key] = failure(error);
          continue;
        }
        visit(frame.container, key, item);
      }
    }
    return holder.value;
  } /*__LOGOS_SERIALIZER__*/;

  if (window.__logos) {
    return;
  }

  const KINDS = __LOGOS_REF_KINDS__;
  const ATTRS = __LOGOS_ATTRS__;
  const REF = ATTRS.REF_ATTR;
  const HIDDEN = ATTRS.HIDDEN_ATTR;
  const VALUE = ATTRS.VALUE_ATTR;
  const VALUE_LEN = ATTRS.VALUE_LEN_ATTR;
  const CHECKED = ATTRS.CHECKED_ATTR;
  const SELECTED = ATTRS.SELECTED_ATTR;
  const MATCH = ATTRS.MATCH_ATTR;
  const SRC = ATTRS.SRC_ATTR;

  const REF_PATTERN = /^([A-Z])(\d+)$/;
  // Subtrees that hold no refs and are never displayed.
  const SKIP_TAGS = new Set(["head", "script", "style", "template", "noscript", "meta", "link", "title", "base"]);
  // Elements whose box says nothing about visibility (rendered by their select or map).
  const NO_VISIBILITY = new Set(["option", "optgroup", "area"]);
  const NOT_POINTER_TARGETS = new Set(["html", "body"]);
  const SETTER_TYPES = new Set(["date", "time", "datetime-local", "month", "week", "color", "range"]);
  const NOT_TEXT_TYPES = new Set(["checkbox", "radio", "file", "submit", "button", "reset", "image", "hidden"]);
  const CHECK_ROLES = new Set(["checkbox", "radio", "switch", "menuitemcheckbox", "menuitemradio"]);
  const VISIBILITY_OPTIONS = { checkVisibilityCSS: true, visibilityProperty: true };
  const HAS_CHECK_VISIBILITY = typeof Element.prototype.checkVisibility === "function";

  const state = { counters: Object.create(null), refs: new Map(), stamps: 0 };
  for (const pair of KINDS) {
    state.counters[pair[0]] = 0;
  }
  // Forget refs of collected elements; numbers are never reused (counters only grow).
  const registry =
    typeof FinalizationRegistry === "function"
      ? new FinalizationRegistry((ref) => {
          const weak = state.refs.get(ref);
          if (weak && !weak.deref()) {
            state.refs.delete(ref);
          }
        })
      : null;

  const clean = (text) =>
    String(text == null ? "" : text)
      .replace(/\s+/g, " ")
      .trim();
  const errorText = (error) => {
    try {
      return error && error.message ? String(error.message) : String(error);
    } catch (ignored) {
      return "?";
    }
  };
  const setAttr = (el, name, value) => {
    if (el.getAttribute(name) !== value) {
      el.setAttribute(name, value);
    }
  };
  const dropAttr = (el, name) => {
    if (el.hasAttribute(name)) {
      el.removeAttribute(name);
    }
  };
  const remember = (el, ref) => {
    state.refs.set(ref, new WeakRef(el));
    if (registry) {
      registry.register(el, ref);
    }
  };
  const assign = (el, prefix) => {
    state.counters[prefix] = (state.counters[prefix] || 0) + 1;
    const ref = prefix + state.counters[prefix];
    el.setAttribute(REF, ref);
    remember(el, ref);
    return ref;
  };
  // A ref already in the DOM when this world starts (the world was recreated on the same
  // document): keep it, and keep the counter above it so numbers are never reused.
  const adopt = (el, ref) => {
    const match = REF_PATTERN.exec(ref);
    if (!match || !(match[1] in state.counters)) {
      return false;
    }
    state.counters[match[1]] = Math.max(state.counters[match[1]], Number(match[2]));
    remember(el, ref);
    return true;
  };
  const find = (ref) => {
    const key = String(ref);
    const weak = state.refs.get(key);
    let el = weak ? weak.deref() : undefined;
    if (!el && !weak && state.stamps === 0) {
      const found = document.querySelectorAll("[" + REF + '="' + CSS.escape(key) + '"]');
      if (found.length === 1 && adopt(found[0], key)) {
        el = found[0];
      }
    }
    return el && el.isConnected ? el : null;
  };
  const refOf = (el) => {
    if (!el || el.nodeType !== 1) {
      return null;
    }
    const ref = el.getAttribute(REF);
    return ref && find(ref) === el ? ref : null;
  };
  const composedParent = (node) => {
    if (node.parentNode) {
      return node.parentNode;
    }
    return node.host || null;
  };
  const composedContains = (outer, inner) => {
    for (let node = inner; node; node = composedParent(node)) {
      if (node === outer) {
        return true;
      }
    }
    return false;
  };
  const closestRef = (el) => {
    for (let node = el; node; node = composedParent(node)) {
      const ref = refOf(node);
      if (ref) {
        return ref;
      }
    }
    return null;
  };
  const describe = (el) => {
    if (!el) {
      return "";
    }
    let text = el.localName || String(el.nodeName || "").toLowerCase();
    if (el.id) {
      text += "#" + el.id;
    }
    if (el.classList && el.classList.length) {
      text += "." + Array.from(el.classList).join(".");
    }
    const ref = refOf(el);
    if (ref) {
      text = ref + " " + text;
    }
    const words = clean(el.innerText || el.textContent || "");
    if (words) {
      // A label for messages, not page content: long texts end with an ellipsis.
      text += " «" + (words.length > 80 ? words.slice(0, 80) + "…" : words) + "»";
    }
    return text;
  };
  const selfVisible = (el) => {
    let visible;
    if (HAS_CHECK_VISIBILITY) {
      visible = el.checkVisibility(VISIBILITY_OPTIONS);
    } else {
      visible = el.getClientRects().length > 0;
    }
    if (!visible && getComputedStyle(el).display === "contents") {
      // No box of its own; its children decide for themselves.
      return null;
    }
    return visible;
  };
  const isShown = (el) => {
    if (!el || !el.isConnected) {
      return false;
    }
    if (HAS_CHECK_VISIBILITY && !el.checkVisibility(VISIBILITY_OPTIONS)) {
      return false;
    }
    return Array.from(el.getClientRects()).some((r) => r.width > 0 || r.height > 0);
  };

  const liveState = (el, tag) => {
    if (tag === "input") {
      const type = String(el.type || "text").toLowerCase();
      if (type === "password") {
        setAttr(el, VALUE_LEN, String(el.value.length));
        dropAttr(el, VALUE);
        return;
      }
      if (type === "checkbox" || type === "radio") {
        setAttr(el, CHECKED, el.checked ? "1" : "0");
        return;
      }
      if (type === "file") {
        const names = el.files ? Array.from(el.files, (file) => file.name).join(", ") : "";
        setAttr(el, VALUE, names);
        return;
      }
      if (type === "submit" || type === "button" || type === "reset" || type === "image") {
        return;
      }
      setAttr(el, VALUE, String(el.value));
      return;
    }
    if (tag === "textarea") {
      setAttr(el, VALUE, String(el.value));
      return;
    }
    if (tag === "select") {
      setAttr(el, VALUE, String(el.value));
      for (const option of el.options) {
        setAttr(option, SELECTED, option.selected ? "1" : "0");
      }
      return;
    }
    if (tag === "img" || tag === "video" || tag === "audio") {
      const source = el.currentSrc;
      if (source) {
        setAttr(el, SRC, source);
      } else {
        dropAttr(el, SRC);
      }
    }
  };

  const escapeAttr = (text) =>
    String(text).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  const stamp = () => {
    const adopting = state.stamps === 0;
    state.stamps += 1;
    const root = document.documentElement;
    const openRoots = [];
    // Frames: element, parent visible, parent cursor, inside a ref'd element, inside a select.
    const stack = root ? [[root, true, "", false, false]] : [];
    while (stack.length) {
      const frame = stack.pop();
      const el = frame[0];
      const parentVisible = frame[1];
      const parentCursor = frame[2];
      const insideRef = frame[3];
      const inSelect = frame[4];
      const tag = el.localName;
      if (SKIP_TAGS.has(tag)) {
        continue;
      }

      let ref = el.getAttribute(REF);
      const hadAttribute = ref !== null;
      if (ref !== null) {
        const known = state.refs.get(ref);
        const owner = known ? known.deref() : undefined;
        if (owner !== el && !(adopting && !known && adopt(el, ref))) {
          ref = null;
        }
      }
      if (ref === null) {
        for (const pair of KINDS) {
          if (el.matches(pair[1])) {
            ref = assign(el, pair[0]);
            break;
          }
        }
      }

      let visible = parentVisible;
      if (!inSelect && !NO_VISIBILITY.has(tag)) {
        let own = false;
        if (parentVisible) {
          own = selfVisible(el);
        }
        visible = own === null ? parentVisible : own;
        if (!visible && parentVisible) {
          setAttr(el, HIDDEN, "1");
        } else {
          dropAttr(el, HIDDEN);
        }
      }

      let cursor = "";
      if (ref === null && visible && !insideRef) {
        cursor = getComputedStyle(el).cursor;
        if (cursor === "pointer" && parentCursor !== "pointer" && !NOT_POINTER_TARGETS.has(tag)) {
          ref = assign(el, "B");
        }
      }
      if (ref === null && hadAttribute) {
        // A ref attribute the page copied or invented: not ours.
        dropAttr(el, REF);
      }

      liveState(el, tag);

      const childInsideRef = insideRef || ref !== null;
      const childInSelect = inSelect || tag === "select";
      const children = el.children;
      for (let i = children.length - 1; i >= 0; i -= 1) {
        stack.push([children[i], visible, cursor, childInsideRef, childInSelect]);
      }
      const shadow = el.shadowRoot;
      if (shadow) {
        openRoots.push(shadow);
        const inner = shadow.children;
        for (let i = inner.length - 1; i >= 0; i -= 1) {
          stack.push([inner[i], visible, cursor, childInsideRef, childInSelect]);
        }
      }
    }

    let html = "";
    if (root) {
      if (root.localName === "html") {
        let inner;
        if (typeof root.getHTML === "function") {
          try {
            inner = root.getHTML({ serializableShadowRoots: true, shadowRoots: openRoots });
          } catch (ignored) {
            inner = root.innerHTML;
          }
        } else {
          inner = root.innerHTML;
        }
        let attributes = "";
        for (const attribute of root.attributes) {
          attributes += " " + attribute.name + '="' + escapeAttr(attribute.value) + '"';
        }
        html = "<!DOCTYPE html><html" + attributes + ">" + inner + "</html>";
      } else {
        html = "<!DOCTYPE html><html><body>" + root.outerHTML + "</body></html>";
      }
    }

    let active = document.activeElement;
    while (active && active.shadowRoot && active.shadowRoot.activeElement) {
      active = active.shadowRoot.activeElement;
    }
    let focused = null;
    if (active && active !== document.body && active !== document.documentElement) {
      focused = refOf(active);
    }
    const scrolling = document.scrollingElement || document.documentElement;
    return {
      html,
      url: location.href,
      title: document.title,
      focused,
      charset: document.characterSet,
      scroll: {
        x: Math.round(window.scrollX),
        y: Math.round(window.scrollY),
        height: scrolling ? scrolling.scrollHeight : 0,
        viewport: window.innerHeight,
      },
    };
  };

  const count = (selector) => {
    try {
      return { count: document.querySelectorAll(selector).length };
    } catch (error) {
      return { error: errorText(error) };
    }
  };
  const nth = (selector, index) => {
    const list = document.querySelectorAll(selector);
    return list[index] || null;
  };

  const deepHit = (x, y) => {
    let hit = document.elementFromPoint(x, y);
    while (hit && hit.shadowRoot) {
      const inner = hit.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === hit) {
        break;
      }
      hit = inner;
    }
    return hit;
  };
  const point = (el) => {
    if (!isShown(el)) {
      return { ok: false, reason: "hidden" };
    }
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const rectsOf = () => Array.from(el.getClientRects()).filter((r) => r.width > 0 && r.height > 0);
    const inView = (r) => r.right > 0 && r.bottom > 0 && r.left < viewportWidth && r.top < viewportHeight;
    let rects = rectsOf();
    if (!rects.length) {
      return { ok: false, reason: "hidden" };
    }
    if (!rects.some(inView)) {
      el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
      rects = rectsOf();
    }
    const rect = rects.find(inView);
    if (!rect) {
      return { ok: false, reason: "hidden" };
    }
    const left = Math.max(rect.left, 0);
    const right = Math.min(rect.right, viewportWidth);
    const top = Math.max(rect.top, 0);
    const bottom = Math.min(rect.bottom, viewportHeight);
    const width = right - left;
    const height = bottom - top;
    // The centre first, then the inner quarter points (a sticky bar may cover the centre).
    const candidates = [
      [left + width / 2, top + height / 2],
      [left + width / 4, top + height / 4],
      [left + (3 * width) / 4, top + height / 4],
      [left + width / 4, top + (3 * height) / 4],
      [left + (3 * width) / 4, top + (3 * height) / 4],
    ];
    let firstCover = null;
    for (const candidate of candidates) {
      const x = candidate[0];
      const y = candidate[1];
      const hit = deepHit(x, y);
      if (!hit) {
        continue;
      }
      if (hit === el || composedContains(el, hit)) {
        return { ok: true, x, y };
      }
      const label = hit.closest ? hit.closest("label") : null;
      if (label && label.control === el) {
        return { ok: true, x, y };
      }
      if (firstCover === null) {
        firstCover = hit;
      }
    }
    if (firstCover === null) {
      return { ok: false, reason: "hidden" };
    }
    return { ok: false, reason: "covered", cover: describe(firstCover), coverRef: closestRef(firstCover) };
  };

  const markMatches = (selector) => {
    let found;
    try {
      found = document.querySelectorAll(selector);
    } catch (error) {
      return { error: errorText(error) };
    }
    for (const old of document.querySelectorAll("[" + MATCH + "]")) {
      old.removeAttribute(MATCH);
    }
    for (const el of found) {
      el.setAttribute(MATCH, "1");
    }
    return { count: found.length };
  };

  const optionLabel = (option) => clean(option.label || option.text);
  const selectOptions = (el, values) => {
    if (!el || el.localName !== "select") {
      return { ok: false, error: "not_select" };
    }
    const options = Array.from(el.options);
    const chosen = [];
    for (const raw of values) {
      const value = String(raw);
      const normal = clean(value);
      const folded = normal.toLowerCase();
      const option =
        options.find((item) => item.value === value) ||
        options.find((item) => item.text === value || optionLabel(item) === normal) ||
        options.find((item) => optionLabel(item).toLowerCase() === folded);
      if (!option) {
        return {
          ok: false,
          missing: value,
          options: options.map((item) => {
            const label = optionLabel(item);
            return item.value !== label ? label + " (" + item.value + ")" : label;
          }),
        };
      }
      chosen.push(option);
    }
    if (el.multiple) {
      for (const option of options) {
        option.selected = chosen.includes(option);
      }
    } else if (chosen.length) {
      chosen[chosen.length - 1].selected = true;
    }
    el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return {
      ok: true,
      selected: Array.from(el.selectedOptions, optionLabel),
      disabled: el.disabled || chosen.some((option) => option.disabled),
    };
  };

  const scroll = (el, value) => {
    const text = value == null ? "" : String(value).trim().toLowerCase();
    const scroller = el || null;
    const height = scroller ? scroller.clientHeight : window.innerHeight;
    const width = scroller ? scroller.clientWidth : window.innerWidth;
    const by = (x, y) => {
      if (scroller) {
        scroller.scrollBy({ left: x, top: y, behavior: "instant" });
      } else {
        window.scrollBy({ left: x, top: y, behavior: "instant" });
      }
    };
    const to = (x, y) => {
      if (scroller) {
        scroller.scrollTo({ left: x, top: y, behavior: "instant" });
      } else {
        window.scrollTo({ left: x, top: y, behavior: "instant" });
      }
    };
    const area = scroller || document.scrollingElement || document.documentElement;
    if (el && !text) {
      el.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
    } else if (text === "" || text === "down") {
      by(0, height * 0.9);
    } else if (text === "up") {
      by(0, -height * 0.9);
    } else if (text === "top") {
      to(scroller ? scroller.scrollLeft : window.scrollX, 0);
    } else if (text === "bottom") {
      to(scroller ? scroller.scrollLeft : window.scrollX, area ? area.scrollHeight : 0);
    } else if (text === "left") {
      by(-width * 0.9, 0);
    } else if (text === "right") {
      by(width * 0.9, 0);
    } else {
      const amount = Number(text.replace(",", "."));
      if (!Number.isFinite(amount)) {
        return { error: "value" };
      }
      by(0, amount);
    }
    return {
      x: Math.round(scroller ? scroller.scrollLeft : window.scrollX),
      y: Math.round(scroller ? scroller.scrollTop : window.scrollY),
      height: area ? area.scrollHeight : 0,
      viewport: height,
    };
  };

  const bodyText = () => {
    const body = document.body || document.documentElement;
    return body ? clean(body.innerText || body.textContent || "").toLowerCase() : "";
  };
  // One check of a wait condition: true when met; a bad selector throws.
  const check = (spec) => {
    const value = spec.value == null ? "" : String(spec.value);
    if (spec.kind === "selector") {
      const el = document.querySelector(value);
      const wanted = spec.state || "visible";
      if (wanted === "attached") {
        return el !== null;
      }
      if (wanted === "detached") {
        return el === null;
      }
      if (wanted === "hidden") {
        return el === null || !isShown(el);
      }
      return el !== null && isShown(el);
    }
    if (spec.kind === "text") {
      return bodyText().includes(clean(value).toLowerCase());
    }
    if (spec.kind === "text_gone") {
      return !bodyText().includes(clean(value).toLowerCase());
    }
    return false;
  };
  const probe = (spec) => {
    try {
      return { ok: check(spec) };
    } catch (error) {
      return { ok: false, error: errorText(error) };
    }
  };
  const waitFor = (spec) =>
    new Promise((resolve) => {
      let done = false;
      let observer = null;
      let interval = null;
      let timer = null;
      const finish = (result) => {
        if (done) {
          return;
        }
        done = true;
        if (observer) {
          observer.disconnect();
        }
        if (interval !== null) {
          clearInterval(interval);
        }
        if (timer !== null) {
          clearTimeout(timer);
        }
        resolve(result);
      };
      const tick = () => {
        const result = probe(spec);
        if (result.ok || result.error) {
          finish(result);
        }
      };
      tick();
      if (done) {
        return;
      }
      observer = new MutationObserver(tick);
      observer.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
      interval = setInterval(tick, 100);
      if (typeof spec.timeout === "number" && spec.timeout >= 0) {
        timer = setTimeout(() => finish({ ok: false, timeout: true }), spec.timeout);
      }
    });

  const prepareInput = (el, replace) => {
    const tag = el.localName;
    if (tag === "select") {
      return { mode: "select" };
    }
    if (tag === "input" || tag === "textarea") {
      const type = tag === "textarea" ? "textarea" : String(el.type || "text").toLowerCase();
      if (NOT_TEXT_TYPES.has(type)) {
        return { mode: "unsupported", type };
      }
      const blocked = Boolean(el.disabled || el.readOnly);
      if (SETTER_TYPES.has(type) || blocked) {
        return { mode: "setter", type, blocked };
      }
      el.focus();
      if (replace) {
        try {
          el.select();
        } catch (ignored) {
          // Some input types have no selection; the value is checked afterwards.
        }
      } else {
        try {
          const end = el.value.length;
          el.setSelectionRange(end, end);
        } catch (ignored) {
          // Same as above.
        }
      }
      return { mode: "insert", type };
    }
    if (el.isContentEditable) {
      el.focus();
      const selection = window.getSelection();
      if (selection) {
        selection.selectAllChildren(el);
        if (!replace) {
          selection.collapseToEnd();
        }
      }
      return { mode: "insert", type: "contenteditable" };
    }
    return { mode: "unsupported", type: tag };
  };
  const setValue = (el, value) => {
    el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return String(el.value);
  };
  const valueOf = (el) => {
    if (el.localName === "input" || el.localName === "textarea" || el.localName === "select") {
      return String(el.value);
    }
    return el.isContentEditable ? String(el.innerText) : String(el.textContent || "");
  };
  const checkState = (el) => {
    if (el.localName === "input" && (el.type === "checkbox" || el.type === "radio")) {
      return { kind: el.type, checked: el.checked, disabled: el.disabled };
    }
    const role = String(el.getAttribute("role") || "").toLowerCase();
    if (CHECK_ROLES.has(role)) {
      return { kind: role, checked: el.getAttribute("aria-checked") === "true", disabled: false };
    }
    return { kind: "none", checked: false, disabled: false };
  };
  const labelOf = (el) => {
    if (el.labels && el.labels.length) {
      return el.labels[0];
    }
    return el.closest ? el.closest("label") : null;
  };
  const focus = (el) => {
    el.focus();
    let active = document.activeElement;
    while (active && active.shadowRoot && active.shadowRoot.activeElement) {
      active = active.shadowRoot.activeElement;
    }
    return active === el;
  };
  const hover = (el) => {
    const options = { bubbles: true, cancelable: true, composed: true, view: window };
    el.dispatchEvent(new MouseEvent("mouseover", options));
    el.dispatchEvent(new MouseEvent("mouseenter", { bubbles: false, composed: true, view: window }));
    el.dispatchEvent(new MouseEvent("mousemove", options));
    return true;
  };
  const linkOf = (el) => {
    const link = el.closest ? el.closest("a[href], area[href]") : null;
    return link ? String(link.href) : null;
  };
  const formOf = (el) => {
    if (!el) {
      return null;
    }
    if (el.localName === "form") {
      return el;
    }
    return el.form || (el.closest ? el.closest("form") : null);
  };
  const isSubmitter = (el) => {
    const tag = el.localName;
    const type = String(el.type || "").toLowerCase();
    return (tag === "button" || tag === "input") && (type === "submit" || type === "image");
  };
  const submitterOf = (form) => {
    if (!form || form.localName !== "form") {
      return null;
    }
    for (const control of form.elements) {
      if (isSubmitter(control) && !control.disabled) {
        return control;
      }
    }
    return null;
  };
  const invalidFields = (form) => {
    const out = [];
    if (!form || typeof form.checkValidity !== "function" || form.noValidate || form.checkValidity()) {
      return out;
    }
    for (const control of form.elements) {
      if (control.willValidate && !control.checkValidity()) {
        out.push({
          ref: refOf(control),
          name: control.name || "",
          message: control.validationMessage || "",
        });
      }
    }
    return out;
  };
  const requestSubmit = (form, submitter) => {
    if (typeof form.requestSubmit === "function") {
      if (submitter) {
        try {
          // The submitter's name and value go with the form, as with a real click.
          form.requestSubmit(submitter);
          return true;
        } catch (ignored) {
          // Not a submit button of this form: submit without one.
        }
      }
      form.requestSubmit();
    } else {
      form.submit();
    }
    return true;
  };
  const withTarget = (el, target) => {
    // Opens the result of a form submitter in a new window; returns the attribute to restore.
    const previous = el.getAttribute("formtarget");
    el.setAttribute("formtarget", target);
    return previous;
  };
  const restoreTarget = (el, previous) => {
    if (previous === null) {
      el.removeAttribute("formtarget");
    } else {
      el.setAttribute("formtarget", previous);
    }
  };
  const rect = (el) => {
    const box = el.getBoundingClientRect();
    return { x: box.left + window.scrollX, y: box.top + window.scrollY, width: box.width, height: box.height };
  };

  Object.defineProperty(window, "__logos", {
    value: Object.freeze({
      stamp,
      find,
      refOf,
      count,
      nth,
      describe,
      point,
      markMatches,
      selectOptions,
      scroll,
      check: probe,
      waitFor,
      prepareInput,
      setValue,
      valueOf,
      checkState,
      labelOf,
      focus,
      hover,
      linkOf,
      formOf,
      isSubmitter,
      submitterOf,
      invalidFields,
      requestSubmit,
      withTarget,
      restoreTarget,
      rect,
      serialize: (value) => SERIALIZER.call(value),
    }),
    configurable: false,
    enumerable: false,
    writable: false,
  });
})();
