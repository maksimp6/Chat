"""PageModel: element refs, semantics and field state of one parsed page.

Refs (L5, B2, I3, F1, T1, M4) name links, buttons, fields, forms, tables and
media. The http engine numbers them in document order with ``REF_KINDS``; a
Chrome page arrives already stamped (``data-logos-ref`` and live state
attributes written by the page helper with the same ``REF_KINDS``), so both
engines name the same elements the same way. Field values typed in the http
engine live in a ``FieldState`` keyed by element index.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Iterable
from urllib.parse import urljoin

from browser import css
from browser.dom import HEADING_TAGS, Document, Element, normalize_space
from browser.errors import (
    E_NTH_RANGE,
    E_OPTION_NOT_FOUND,
    E_REF_NOT_FOUND,
    E_SELECTOR_NONE,
    E_TARGET_REQUIRED,
    E_TEXT_AMBIGUOUS,
    E_TEXT_NOT_FOUND,
    BrowserError,
    BrowserNotFound,
)
from browser.paging import normalize_ref, parse_ref, plural
from browser.redact import display_url, is_sensitive_name

if TYPE_CHECKING:
    from browser.engine import ElementTarget

logger = logging.getLogger("logos.browser.page")

REF_ATTR = "data-logos-ref"
HIDDEN_ATTR = "data-logos-hidden"
VALUE_ATTR = "data-logos-value"
VALUE_LEN_ATTR = "data-logos-value-len"
CHECKED_ATTR = "data-logos-checked"
SELECTED_ATTR = "data-logos-selected"
MATCH_ATTR = "data-logos-match"
SRC_ATTR = "data-logos-src"
ATTRIBUTE_NAMES = {
    "REF_ATTR": REF_ATTR,
    "HIDDEN_ATTR": HIDDEN_ATTR,
    "VALUE_ATTR": VALUE_ATTR,
    "VALUE_LEN_ATTR": VALUE_LEN_ATTR,
    "CHECKED_ATTR": CHECKED_ATTR,
    "SELECTED_ATTR": SELECTED_ATTR,
    "MATCH_ATTR": MATCH_ATTR,
    "SRC_ATTR": SRC_ATTR,
}

REF_KINDS = (
    ("F", "form"),
    ("L", "a[href], area[href], iframe[src], frame[src]"),
    (
        "B",
        "button, input[type=submit], input[type=button], input[type=reset], "
        "input[type=image], summary, [role=button], [role=link], [role=menuitem], "
        "[role=menuitemcheckbox], [role=menuitemradio], [role=tab], [role=option], "
        "[role=treeitem], [role=switch], [onclick]",
    ),
    (
        "I",
        "input:not([type=hidden]):not([type=submit]):not([type=button])"
        ":not([type=reset]):not([type=image]), select, textarea, "
        "[contenteditable=''], [contenteditable=true], "
        "[contenteditable=plaintext-only], [role=textbox], [role=searchbox], "
        "[role=combobox], [role=checkbox], [role=radio], [role=slider], "
        "[role=spinbutton]",
    ),
    ("T", "table"),
    ("M", "img, video, audio, [role=img]"),
)
REF_COUNT_NAMES = {
    "L": "links",
    "B": "buttons",
    "I": "fields",
    "F": "forms",
    "T": "tables",
    "M": "media",
}
REF_PREFIX_ORDER = "".join(prefix for prefix, _ in REF_KINDS)

# Main-content detection thresholds (shares of the body's visible text).
MAIN_SHARE = 0.25
ARTICLE_SHARE = 0.40
ARTICLE_ONLY_SHARE = 0.25
SCORED_MIN_SHARE = 0.20
SCORED_MIN_CHARS = 200
PARAGRAPH_MIN_CHARS = 25
MAX_LENGTH_BONUS = 3.0
NEGATIVE_WEIGHT = 0.5
POSITIVE_WEIGHT = 1.25
NEGATIVE = re.compile(
    r"comment|meta|footer|footnote|nav|menu|sidebar|sponsor|\bad-|\bads\b|advert|"
    r"banner|cookie|consent|share|social|related|promo|breadcrumb|pagination|pager|"
    r"popup|modal|subscribe|newsletter|masthead|widget|outbrain|taboola",
    re.I,
)
POSITIVE = re.compile(
    r"article|body|content|entry|hentry|main|page|post|text|blog|story|news|"
    r"статья|текст|материал",
    re.I,
)
# Script-rendered page detection thresholds.
SCRIPT_PAGE_MAX_TEXT = 200
EMPTY_MOUNT_MAX_TEXT = 50
EMPTY_MOUNT_PAGE_TEXT = 1500
NOSCRIPT_PAGE_TEXT = 1000
APP_MOUNTS = (
    "#root",
    "#app",
    "#__next",
    "#__nuxt",
    "#___gatsby",
    "#svelte",
    "[data-reactroot]",
    "app-root",
    "[ng-app]",
    "[id^=ember]",
)
_NOSCRIPT_JS = re.compile(r"(enable|включите|требуется|requires?|нужен).{0,40}javascript", re.I)

INPUT_TYPES = frozenset(
    {
        "text",
        "search",
        "email",
        "url",
        "tel",
        "password",
        "number",
        "date",
        "time",
        "datetime-local",
        "month",
        "week",
        "color",
        "range",
        "checkbox",
        "radio",
        "file",
        "hidden",
        "submit",
        "button",
        "reset",
        "image",
    }
)
BUTTON_INPUT_TYPES = frozenset({"submit", "button", "reset", "image"})
_TEXT_LIKE = frozenset({"text", "search", "email", "url", "tel", "password"})
_FORM_ASSOCIATED = frozenset(
    {"button", "fieldset", "input", "object", "output", "select", "textarea"}
)
_ALWAYS_HIDDEN = frozenset(
    {
        "head",
        "script",
        "style",
        "title",
        "meta",
        "link",
        "base",
        "param",
        "source",
        "track",
        "datalist",
        "noframes",
    }
)
_NAME_SKIP = frozenset({"script", "style", "head", "select", "datalist", "textarea"})
_NAME_BLOCKS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)
_LEAF_DIV_BLOCKERS = frozenset(
    {
        "div",
        "p",
        "table",
        "ul",
        "ol",
        "dl",
        "section",
        "article",
        "blockquote",
        "pre",
        "form",
        "header",
        "footer",
        "nav",
        "aside",
        "main",
        "figure",
    }
    | HEADING_TAGS
)
_SCORE_TAGS = frozenset(
    {"p", "pre", "blockquote", "li", "td", "dd", "h2", "h3", "h4", "h5", "h6"}
)
_NEVER_MAIN = frozenset({"nav", "aside", "footer", "header", "form", "html", "#document"})
_LANDMARK_ROLES = frozenset(
    {"navigation", "complementary", "contentinfo", "banner", "search", "form"}
)
_STYLE_HIDDEN = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse))\b", re.I
)
_URL_WHITESPACE = re.compile(r"[\t\n\r]")
_COMMAS = re.compile(r"[,，、]")


@lru_cache(maxsize=1)
def _ref_selectors() -> tuple[tuple[str, css.SelectorList], ...]:
    return tuple((prefix, css.compile_selector(sel)) for prefix, sel in REF_KINDS)


@lru_cache(maxsize=1)
def _mount_selectors() -> tuple[tuple[str, css.SelectorList], ...]:
    return tuple((sel, css.compile_selector(sel)) for sel in APP_MOUNTS)


@dataclass
class FieldState:
    """Field values typed in the http engine, keyed by ``Element.index``."""

    values: dict[int, str] = field(default_factory=dict)
    checked: dict[int, bool] = field(default_factory=dict)
    selected: dict[int, tuple[int, ...]] = field(default_factory=dict)  # select → options
    files: dict[int, tuple[str, ...]] = field(default_factory=dict)
    focused: int | None = None

    def copy(self) -> FieldState:
        return FieldState(
            dict(self.values),
            dict(self.checked),
            dict(self.selected),
            dict(self.files),
            self.focused,
        )


@dataclass
class Heading:
    level: int
    text: str
    element: Element
    number: str


def option_value(option: Element) -> str:
    """An option's submitted value: its value attribute, else its text."""
    value = option.attrs.get("value")
    if value is not None:
        return value
    return normalize_space(option.text())


def option_label(option: Element) -> str:
    """An option's visible label: the label attribute, else its text."""
    label = option.attrs.get("label")
    if label:
        return normalize_space(label)
    return normalize_space(option.text())


def element_descriptor(element: Element) -> str:
    """"div.price#id" style description of an element."""
    text = element.tag
    classes = element.attrs.get("class", "").split()
    if classes:
        text += "." + ".".join(classes)
    element_id = element.attrs.get("id")
    if element_id:
        text += "#" + element_id
    return text


def _best_srcset(srcset: str) -> str | None:
    """The largest candidate of a srcset (by w, then by x)."""
    candidates: list[tuple[str, str]] = []
    text = srcset
    position, length = 0, len(text)
    while position < length:
        while position < length and (text[position].isspace() or text[position] == ","):
            position += 1
        if position >= length:
            break
        start = position
        while position < length and not text[position].isspace():
            position += 1
        url = text[start:position]
        descriptor = ""
        if url.endswith(","):
            url = url.rstrip(",")
        else:
            start, depth = position, 0
            while position < length:
                char = text[position]
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth = max(0, depth - 1)
                elif char == "," and depth == 0:
                    break
                position += 1
            descriptor = text[start:position].strip()
            position += 1
        if url:
            candidates.append((url, descriptor))
    if not candidates:
        return None
    best_w: tuple[float, str] | None = None
    best_x: tuple[float, str] | None = None
    for url, descriptor in candidates:
        token = descriptor.split()[0].lower() if descriptor.split() else "1x"
        try:
            if token.endswith("w"):
                width = float(token[:-1])
                if best_w is None or width > best_w[0]:
                    best_w = (width, url)
                continue
            density = float(token[:-1]) if token.endswith("x") else 1.0
        except ValueError:
            density = 1.0
        if best_x is None or density > best_x[0]:
            best_x = (density, url)
    if best_w is not None:
        return best_w[1]
    return best_x[1] if best_x is not None else candidates[0][0]


class PageModel:
    """Refs, semantics, visibility, main content and field state of one page."""

    def __init__(
        self,
        document: Document,
        *,
        url: str,
        engine: str,
        stamped: bool = False,
        render_noscript: bool | None = None,
        state: FieldState | None = None,
        charset: str = "utf-8",
        focused_ref: str | None = None,
    ) -> None:
        self.document = document
        self.url = url or ""
        self.engine = engine
        self.stamped = stamped
        self.render_noscript = (not stamped) if render_noscript is None else bool(render_noscript)
        self.state = state if state is not None else FieldState()
        self.charset = charset or "utf-8"
        self.focused_ref = normalize_ref(focused_ref) if focused_ref else None
        self.body = document.body
        self.base_url = self._compute_base_url()
        # Notes left by the mutators (disabled fields and the like).
        self.notes: list[str] = []
        self._refs: dict[str, Element] | None = None
        self._ref_by_index: dict[int, str] = {}
        self._hidden: dict[int, bool] = {}
        self._title: str | None = None
        self._main: Element | None = None
        self._main_how = "body"
        self._headings: list[Heading] | None = None
        self._heading_numbers: dict[int, str] = {}
        self._text_len: list[int] | None = None
        self._link_len: list[int] = []
        self._labels: dict[str, list[Element]] | None = None
        self._names: dict[int, str] = {}

    # -- basics -----------------------------------------------------------

    def _compute_base_url(self) -> str:
        base = self.document.base_href
        if not base:
            return self.url
        try:
            joined = urljoin(self.url, _URL_WHITESPACE.sub("", base))
        except ValueError:
            return self.url
        if joined.split(":", 1)[0].lower() in ("javascript", "data"):
            return self.url
        return joined

    @property
    def title(self) -> str:
        if self._title is None:
            title = self.document.title
            if not title:
                for element in self.document.elements:
                    if element.tag == "meta" and element.attrs.get("property", "").lower() == "og:title":
                        title = normalize_space(element.attrs.get("content"))
                        if title:
                            break
            if not title:
                for element in self.document.elements:
                    if element.tag == "h1" and not self.is_hidden(element):
                        title = self._name_text(element)
                        if title:
                            break
            self._title = title or ""
        return self._title

    @property
    def lang(self) -> str:
        root = self.document.html
        return (root.attrs.get("lang") or "").strip() if root is not None else ""

    def take_notes(self) -> list[str]:
        """Notes left by the mutators since the last call."""
        notes, self.notes = self.notes, []
        return notes

    # -- refs -------------------------------------------------------------

    @property
    def refs(self) -> dict[str, Element]:
        if self._refs is None:
            self._assign_refs()
        assert self._refs is not None
        return self._refs

    def _assign_refs(self) -> None:
        refs: dict[str, Element] = {}
        by_index: dict[int, str] = {}
        if self.stamped:
            for element in self.document.elements:
                parsed = parse_ref(element.attrs.get(REF_ATTR))
                if parsed is None or parsed[0] == "N":
                    continue
                ref = f"{parsed[0]}{parsed[1]}"
                if ref not in refs:
                    refs[ref] = element
                    by_index[element.index] = ref
        else:
            selectors = _ref_selectors()
            counters = {prefix: 0 for prefix, _ in REF_KINDS}
            stack: list[Element] = [self.document.root]
            while stack:
                element = stack.pop()
                if element.tag != "#document":
                    for prefix, selector in selectors:
                        if selector.matches(element):
                            counters[prefix] += 1
                            ref = f"{prefix}{counters[prefix]}"
                            refs[ref] = element
                            by_index[element.index] = ref
                            break
                if element.tag == "template" and "shadowrootmode" not in element.attrs:
                    continue
                kids = element.element_children()
                kids.reverse()
                stack.extend(kids)
        self._refs = refs
        self._ref_by_index = by_index

    def ref_of(self, element: Element | None) -> str | None:
        if element is None:
            return None
        self.refs  # noqa: B018 — makes sure refs are assigned
        return self._ref_by_index.get(element.index)

    def refs_of_kind(self, prefix: str) -> list[str]:
        prefix = prefix.upper()
        return [ref for ref in self.refs if ref[0] == prefix]

    def ref_counts(self) -> dict[str, int]:
        counts = {name: 0 for name in REF_COUNT_NAMES.values()}
        for ref in self.refs:
            name = REF_COUNT_NAMES.get(ref[0])
            if name:
                counts[name] += 1
        return counts

    def element(self, ref: str) -> Element:
        """The element of a ref; BrowserNotFound(E_REF_NOT_FOUND) when absent."""
        key = normalize_ref(ref)
        found = self.refs.get(key)
        if found is None:
            raise BrowserNotFound(
                E_REF_NOT_FOUND.format(ref=key or ref, url=display_url(self.url))
            )
        return found

    def ref_label(self, element: Element) -> str:
        """The element's ref, or a tag description when it has none."""
        return self.ref_of(element) or f"<{element_descriptor(element)}>"

    # -- URLs -------------------------------------------------------------

    def resolve(self, href: str | None) -> str | None:
        """An absolute URL for ``href`` against the base URL; None when empty."""
        if href is None:
            return None
        cleaned = _URL_WHITESPACE.sub("", str(href)).strip()
        if not cleaned:
            return None
        try:
            return urljoin(self.base_url, cleaned)
        except ValueError:
            return cleaned

    def href(self, element: Element) -> str | None:
        if element.tag in ("a", "area", "link"):
            return self.resolve(element.attrs.get("href"))
        if element.tag in ("iframe", "frame"):
            return self.resolve(element.attrs.get("src"))
        return None

    def media_url(self, element: Element) -> str | None:
        """The media address: Chrome's currentSrc, lazy-load attributes, srcset, src."""
        if self.stamped:
            current = element.attrs.get(SRC_ATTR)
            if current:
                return self.resolve(current)
        for name in ("data-src", "data-lazy-src", "data-original"):
            value = element.attrs.get(name)
            if value and value.strip():
                return self.resolve(value)
        if element.tag in ("video", "audio"):
            source = element.attrs.get("src")
            if source and source.strip():
                return self.resolve(source)
            for child in element.element_children():
                if child.tag == "source" and (child.attrs.get("src") or "").strip():
                    return self.resolve(child.attrs["src"])
            return None
        srcset = element.attrs.get("srcset") or element.attrs.get("data-srcset")
        if srcset:
            best = _best_srcset(srcset)
            if best:
                return self.resolve(best)
        source = element.attrs.get("src")
        if source and source.strip():
            return self.resolve(source)
        return None

    def media_poster(self, element: Element) -> str | None:
        poster = element.attrs.get("poster")
        return self.resolve(poster) if poster and poster.strip() else None

    # -- semantics --------------------------------------------------------

    def field_kind(self, element: Element) -> str:
        tag = element.tag
        if tag == "input":
            kind = (element.attrs.get("type") or "text").strip().lower()
            return kind if kind in INPUT_TYPES else "text"
        if tag == "select":
            return "select-multiple" if "multiple" in element.attrs else "select"
        if tag == "textarea":
            return "textarea"
        editable = element.attrs.get("contenteditable")
        if editable is not None and editable.strip().lower() in ("", "true", "plaintext-only"):
            return "contenteditable"
        return "custom"

    def role(self, element: Element) -> str | None:
        explicit = (element.attrs.get("role") or "").split()
        if explicit:
            return explicit[0].lower()
        tag = element.tag
        attrs = element.attrs
        if tag in ("a", "area"):
            return "link" if "href" in attrs else None
        if tag in ("button", "summary"):
            return "button"
        if tag == "input":
            kind = self.field_kind(element)
            if kind in BUTTON_INPUT_TYPES or kind == "file":
                return "button"
            if kind == "hidden":
                return None
            if kind in ("checkbox", "radio"):
                return kind
            if kind == "range":
                return "slider"
            if kind == "number":
                return "spinbutton"
            if "list" in attrs and kind in ("text", "search", "email", "url", "tel"):
                return "combobox"
            if kind == "search":
                return "searchbox"
            return "textbox"
        if tag == "select":
            size = attrs.get("size", "").strip()
            many = "multiple" in attrs or (size.isdigit() and int(size) > 1)
            return "listbox" if many else "combobox"
        if tag == "textarea" or self.field_kind(element) == "contenteditable":
            return "textbox"
        if tag in HEADING_TAGS:
            return "heading"
        return {
            "img": "img",
            "option": "option",
            "table": "table",
            "form": "form",
            "iframe": "document",
            "frame": "document",
            "nav": "navigation",
            "main": "main",
            "aside": "complementary",
            "ul": "list",
            "ol": "list",
            "menu": "list",
            "li": "listitem",
            "dialog": "dialog",
            "progress": "progressbar",
            "meter": "meter",
            "details": "group",
            "fieldset": "group",
            "article": "article",
            "search": "search",
        }.get(tag)

    def _own_hidden(self, element: Element) -> bool:
        tag = element.tag
        if tag == "#document":
            return False
        if tag in _ALWAYS_HIDDEN:
            return True
        if tag == "template" and "shadowrootmode" not in element.attrs:
            return True
        attrs = element.attrs
        if self.stamped:
            return attrs.get(HIDDEN_ATTR) == "1"
        if "hidden" in attrs:
            return True
        if (attrs.get("aria-hidden") or "").strip().lower() == "true":
            return True
        style = attrs.get("style")
        if style and _STYLE_HIDDEN.search(style):
            return True
        if tag == "input" and (attrs.get("type") or "").strip().lower() == "hidden":
            return True
        if tag == "dialog" and "open" not in attrs:
            return True
        if tag == "noscript" and not self.render_noscript:
            return True
        return False

    def is_hidden(self, element: Element) -> bool:
        """True when the element or one of its ancestors is not displayed."""
        cache = self._hidden
        cached = cache.get(element.index)
        if cached is not None:
            return cached
        chain: list[Element] = []
        node: Element | None = element
        inherited = False
        while node is not None and node.tag != "#document":
            value = cache.get(node.index)
            if value is not None:
                inherited = value
                break
            chain.append(node)
            node = node.parent
        for item in reversed(chain):
            inherited = inherited or self._own_hidden(item)
            cache[item.index] = inherited
        return cache.get(element.index, False)

    def _name_text(self, element: Element, exclude: Element | None = None) -> str:
        """Visible text of a subtree for names: image alts included, controls left out."""
        parts: list[str] = []
        stack: list[Element | str] = [element]
        while stack:
            node = stack.pop()
            if isinstance(node, str):
                parts.append(node)
                continue
            if node is not element:
                if node is exclude or node.tag in _NAME_SKIP:
                    continue
                if node.tag == "title":
                    parent = node.parent
                    if parent is not None and parent.tag in ("svg", "g", "symbol"):
                        parts.append(" " + node.text() + " ")
                    continue
                if self._own_hidden(node):
                    continue
                if node.tag in ("img", "area"):
                    alt = node.attrs.get("alt") or ""
                    if alt:
                        parts.append(" " + alt + " ")
                    continue
                if node.tag == "input":
                    continue
                if node.tag in _NAME_BLOCKS:
                    parts.append(" ")
                    stack.append(" ")
            stack.extend(reversed(node.children))
        return normalize_space("".join(parts))

    def _labels_for(self, element_id: str) -> list[Element]:
        if self._labels is None:
            labels: dict[str, list[Element]] = {}
            for node in self.document.elements:
                if node.tag == "label":
                    target = (node.attrs.get("for") or "").strip()
                    if target:
                        labels.setdefault(target, []).append(node)
            self._labels = labels
        return self._labels.get(element_id, [])

    def label_text(self, element: Element) -> str:
        """Text of the field's <label for=id> and wrapping <label>, without the field."""
        parts: list[str] = []
        element_id = (element.attrs.get("id") or "").strip()
        if element_id:
            for label in self._labels_for(element_id):
                parts.append(self._name_text(label, exclude=element))
        parent = element.parent
        wrapper = parent.closest(lambda node: node.tag == "label") if parent is not None else None
        if wrapper is not None:
            target = (wrapper.attrs.get("for") or "").strip()
            if not target or target == element_id:
                parts.append(self._name_text(wrapper, exclude=element))
        return normalize_space(" ".join(part for part in parts if part))

    def is_field(self, element: Element) -> bool:
        if element.tag in ("select", "textarea"):
            return True
        if element.tag == "input":
            return self.field_kind(element) not in BUTTON_INPUT_TYPES
        ref = self.ref_of(element)
        return bool(ref) and ref[0] == "I"

    def accessible_name(self, element: Element) -> str:
        cached = self._names.get(element.index)
        if cached is None:
            cached = self._compute_name(element)
            self._names[element.index] = cached
        return cached

    def _compute_name(self, element: Element) -> str:
        attrs = element.attrs
        labelledby = (attrs.get("aria-labelledby") or "").split()
        if labelledby:
            texts = []
            for target_id in labelledby:
                target = self.document.by_id(target_id)
                if target is not None:
                    texts.append(self._name_text(target))
            joined = normalize_space(" ".join(texts))
            if joined:
                return joined
        label = normalize_space(attrs.get("aria-label"))
        if label:
            return label
        tag = element.tag
        if tag == "input":
            kind = self.field_kind(element)
            if kind in BUTTON_INPUT_TYPES:
                candidates = (
                    [attrs.get("alt"), attrs.get("value")]
                    if kind == "image"
                    else [attrs.get("value")]
                )
                for candidate in candidates:
                    text = normalize_space(candidate)
                    if text:
                        return text
                if kind in ("submit", "image"):
                    return "Отправить"
                if kind == "reset":
                    return "Сбросить"
                return normalize_space(attrs.get("title"))
        if self.is_field(element):
            text = self.label_text(element)
            if text:
                return text
            for name in ("placeholder", "aria-placeholder", "title", "name"):
                text = normalize_space(attrs.get(name))
                if text:
                    return text
            if tag not in ("input", "select", "textarea") and self.role(element) in (
                "checkbox",
                "radio",
                "switch",
                "option",
            ):
                # ARIA takes the name of these roles from their content.
                return self._name_text(element)
            return ""
        if tag in ("img", "area"):
            return normalize_space(attrs.get("alt")) or normalize_space(attrs.get("title"))
        if tag in ("iframe", "frame"):
            return normalize_space(attrs.get("title")) or normalize_space(attrs.get("name"))
        if tag in ("video", "audio"):
            return normalize_space(attrs.get("title"))
        if tag == "table":
            caption = next((kid for kid in element.element_children() if kid.tag == "caption"), None)
            if caption is not None:
                text = self._name_text(caption)
                if text:
                    return text
            return normalize_space(attrs.get("title"))
        text = self._name_text(element)
        if text:
            return text
        return normalize_space(attrs.get("title"))

    # -- field state ------------------------------------------------------

    def form_owner(self, element: Element) -> Element | None:
        """The form a control belongs to: form="id", else the ancestor form."""
        if element.tag == "form":
            return element
        if element.tag in _FORM_ASSOCIATED and "form" in element.attrs:
            target = self.document.by_id((element.attrs.get("form") or "").strip())
            return target if target is not None and target.tag == "form" else None
        parent = element.parent
        return parent.closest(lambda node: node.tag == "form") if parent is not None else None

    def options(self, element: Element) -> list[Element]:
        """Options of a select or datalist in tree order (optgroup children included)."""
        return [node for node in element.descendants() if node.tag == "option"]

    def selected_options(self, element: Element) -> list[Element]:
        options = self.options(element)
        multiple = "multiple" in element.attrs
        if self.stamped:
            if any(SELECTED_ATTR in option.attrs for option in options):
                return [option for option in options if option.attrs.get(SELECTED_ATTR) == "1"]
        else:
            chosen = self.state.selected.get(element.index)
            if chosen is not None:
                wanted = set(chosen)
                return [option for option in options if option.index in wanted]
        marked = [option for option in options if "selected" in option.attrs]
        if multiple:
            return marked
        if marked:
            return [marked[-1]]
        first = next((option for option in options if not css.is_disabled(option)), None)
        return [first] if first is not None else []

    def field_value(self, element: Element) -> str:
        """The field's current value (typed state, live Chrome state or markup default)."""
        kind = self.field_kind(element)
        if self.stamped:
            live = element.attrs.get(VALUE_ATTR)
            if live is not None:
                return live
            if kind == "password":
                return ""
        else:
            typed = self.state.values.get(element.index)
            if typed is not None:
                return typed
        tag = element.tag
        if tag == "textarea":
            text = element.text(skip=())
            if text.startswith("\r\n"):
                return text[2:]
            if text.startswith("\n"):
                return text[1:]
            return text
        if tag == "select":
            chosen = self.selected_options(element)
            return option_value(chosen[0]) if chosen else ""
        if tag == "input":
            if kind in ("checkbox", "radio"):
                return element.attrs.get("value", "on")
            return element.attrs.get("value", "")
        for name in ("aria-valuetext", "aria-valuenow"):
            value = element.attrs.get(name)
            if value is not None:
                return value
        if kind == "contenteditable" or self.role(element) in ("textbox", "searchbox", "combobox"):
            return normalize_space(element.text())
        return ""

    def value_length(self, element: Element) -> int:
        if self.stamped and VALUE_ATTR not in element.attrs:
            length = element.attrs.get(VALUE_LEN_ATTR)
            if length is not None and length.strip().isdigit():
                return int(length.strip())
        return len(self.field_value(element))

    def field_checked(self, element: Element) -> bool:
        if self.stamped:
            live = element.attrs.get(CHECKED_ATTR)
            if live is not None:
                return live == "1"
        else:
            typed = self.state.checked.get(element.index)
            if typed is not None:
                return typed
        if element.tag == "input":
            return "checked" in element.attrs
        return (element.attrs.get("aria-checked") or "").strip().lower() == "true"

    def display_value(self, element: Element) -> str | None:
        """The field value as shown to the model: passwords and secrets masked."""
        kind = self.field_kind(element)
        if kind in ("checkbox", "radio"):
            return None
        if kind in ("select", "select-multiple"):
            labels = [option_label(option) for option in self.selected_options(element)]
            return ", ".join(f"«{label}»" for label in labels) or None
        if kind == "file":
            files = self.state.files.get(element.index) or ()
            return ", ".join(os.path.basename(path) for path in files) or None
        if kind == "password":
            length = self.value_length(element)
            return f"(заполнено, {length} симв.)" if length else "(пусто)"
        value = self.field_value(element)
        if is_sensitive_name(element.attrs.get("name")):
            return f"(скрыто, {len(value)} симв.)" if value else None
        return value or None

    def is_focused(self, element: Element) -> bool:
        if self.stamped:
            return bool(self.focused_ref) and self.ref_of(element) == self.focused_ref
        return self.state.focused == element.index

    def element_states(self, element: Element) -> list[str]:
        attrs = element.attrs
        states: list[str] = []
        if css.is_disabled(element) or (attrs.get("aria-disabled") or "").lower() == "true":
            states.append("disabled")
        if "required" in attrs or (attrs.get("aria-required") or "").lower() == "true":
            states.append("required")
        if "readonly" in attrs or (attrs.get("aria-readonly") or "").lower() == "true":
            states.append("readonly")
        role = self.role(element)
        if (
            self.field_kind(element) in ("checkbox", "radio")
            or role in ("checkbox", "radio", "switch", "menuitemcheckbox", "menuitemradio")
        ) and self.field_checked(element):
            states.append("checked")
        if (attrs.get("aria-selected") or "").lower() == "true":
            states.append("selected")
        expanded = (attrs.get("aria-expanded") or "").lower() == "true"
        if element.tag == "summary":
            parent = element.parent
            expanded = expanded or (
                parent is not None and parent.tag == "details" and "open" in parent.attrs
            )
        if expanded:
            states.append("expanded")
        if self.is_hidden(element):
            states.append("hidden")
        if self.is_focused(element):
            states.append("focused")
        return states

    # -- mutators (http engine) -------------------------------------------

    def _input_notes(self, element: Element) -> list[str]:
        if css.is_disabled(element) or (
            "readonly" in element.attrs and element.tag in ("input", "textarea")
        ):
            return [
                f"поле {self.ref_label(element)} недоступно для ввода на странице "
                "(disabled/readonly) — значение всё равно записано"
            ]
        return []

    def set_value(self, element: Element, value: str | None) -> list[str]:
        """Type a value into a field; returns notes (also kept in ``notes``)."""
        if element.tag == "select":
            before = len(self.notes)
            self.select_options(element, [value or ""])
            return self.notes[before:]
        notes = self._input_notes(element)
        self.state.values[element.index] = "" if value is None else str(value)
        self.notes.extend(notes)
        return notes

    def set_checked(self, element: Element, checked: bool) -> list[str]:
        """Check or uncheck; checking a radio unchecks its group in the same form."""
        notes = self._input_notes(element)
        self.state.checked[element.index] = bool(checked)
        if checked and element.tag == "input" and self.field_kind(element) == "radio":
            name = element.attrs.get("name")
            if name:
                owner = self.form_owner(element)
                for other in self.document.elements:
                    if (
                        other is not element
                        and other.tag == "input"
                        and self.field_kind(other) == "radio"
                        and other.attrs.get("name") == name
                        and self.form_owner(other) is owner
                    ):
                        self.state.checked[other.index] = False
        self.notes.extend(notes)
        return notes

    def _options_text(self, options: list[Element]) -> str:
        items = []
        for option in options:
            label, value = option_label(option), option_value(option)
            items.append(f"«{label}»" if value == label else f"«{label}» ({value})")
        return ", ".join(items) if items else "(список пуст)"

    def select_options(self, element: Element, values: Iterable[Any]) -> list[str]:
        """Select options by value, then exact text, then casefold text.

        A single select takes the last value. Returns the selected labels;
        notes go to ``notes``.
        """
        options = self.options(element)
        multiple = "multiple" in element.attrs
        wanted = ["" if value is None else str(value) for value in values]
        if not multiple and wanted:
            wanted = wanted[-1:]
        if not wanted and not multiple:
            raise BrowserError(
                E_OPTION_NOT_FOUND.format(
                    ref=self.ref_label(element), value="", options=self._options_text(options)
                )
            )
        chosen: list[Element] = []
        for value in wanted:
            text = normalize_space(value)
            match = next((option for option in options if option_value(option) == value), None)
            if match is None:
                match = next(
                    (
                        option
                        for option in options
                        if normalize_space(option.text()) == text or option_label(option) == text
                    ),
                    None,
                )
            if match is None:
                folded = text.casefold()
                match = next(
                    (
                        option
                        for option in options
                        if normalize_space(option.text()).casefold() == folded
                        or option_label(option).casefold() == folded
                    ),
                    None,
                )
            if match is None:
                raise BrowserError(
                    E_OPTION_NOT_FOUND.format(
                        ref=self.ref_label(element),
                        value=value,
                        options=self._options_text(options),
                    )
                )
            if match not in chosen:
                chosen.append(match)
        notes = self._input_notes(element)
        for option in chosen:
            if css.is_disabled(option):
                notes.append(
                    f"вариант «{option_label(option)}» списка {self.ref_label(element)} "
                    "недоступен на странице — всё равно выбран"
                )
        self.state.selected[element.index] = tuple(option.index for option in chosen)
        self.notes.extend(notes)
        return [option_label(option) for option in chosen]

    def set_files(self, element: Element, paths: Iterable[str]) -> list[str]:
        """Attach local files (absolute paths) to a file input."""
        files = tuple(str(path) for path in paths)
        notes = self._input_notes(element)
        if len(files) > 1 and "multiple" not in element.attrs:
            notes.append(
                f"поле {self.ref_label(element)} без multiple: страница ждёт один файл, "
                f"переданы все {len(files)}"
            )
        self.state.files[element.index] = files
        self.notes.extend(notes)
        return notes

    def focus(self, element: Element) -> list[str]:
        self.state.focused = element.index
        return []

    # -- targets ----------------------------------------------------------

    def find_by_text(self, text: str, prefixes: str = "LBI") -> list[Element]:
        """Elements of the ref kinds whose name or text matches, best tier first.

        Tiers: exact, then starts-with, then contains (casefold). Within the
        best tier, visible elements are used when there are any; links and
        buttons rank before fields.
        """
        query = normalize_space(text).casefold()
        if not query:
            return []
        allowed = prefixes.upper()
        tiers: tuple[list[Element], list[Element], list[Element]] = ([], [], [])
        for ref, element in self.refs.items():
            if ref[0] not in allowed:
                continue
            keys = {
                normalize_space(self.accessible_name(element)).casefold(),
                self._name_text(element).casefold(),
            }
            keys.discard("")
            if not keys:
                continue
            if query in keys:
                tiers[0].append(element)
            elif any(key.startswith(query) for key in keys):
                tiers[1].append(element)
            elif any(query in key for key in keys):
                tiers[2].append(element)
        for tier in tiers:
            if not tier:
                continue
            visible = [element for element in tier if not self.is_hidden(element)]
            chosen = visible or tier

            def rank(element: Element) -> tuple[int, int, int]:
                ref = self.ref_of(element) or "I"
                return (0 if ref[0] in "LB" else 1, int(self.is_hidden(element)), element.index)

            return sorted(chosen, key=rank)
        return []

    def resolve_target(
        self, target: ElementTarget, prefixes: str = "LBI"
    ) -> tuple[Element, list[str]]:
        """The element a target names (ref, selector or text) and notes."""
        notes: list[str] = []
        ref = getattr(target, "ref", None)
        nth = getattr(target, "nth", None)
        if ref:
            return self.element(ref), notes
        selector = getattr(target, "selector", None)
        if selector:
            found = css.select(self.document, selector)
            if not found:
                raise BrowserNotFound(E_SELECTOR_NONE.format(sel=selector))
            if nth:
                if nth < 1 or nth > len(found):
                    raise BrowserNotFound(
                        E_NTH_RANGE.format(
                            nth=nth, target=f"селектор «{selector}»", count=len(found)
                        )
                    )
                return found[nth - 1], notes
            if len(found) > 1:
                count = len(found)
                notes.append(
                    f"селектор нашёл {count} "
                    f"{plural(count, 'элемент', 'элемента', 'элементов')}, "
                    "использован первый (уточните nth)"
                )
            return found[0], notes
        text = getattr(target, "text", None)
        if text:
            found = self.find_by_text(text, prefixes)
            if not found:
                raise BrowserNotFound(E_TEXT_NOT_FOUND.format(text=text))
            if nth:
                if nth < 1 or nth > len(found):
                    raise BrowserNotFound(
                        E_NTH_RANGE.format(nth=nth, target=f"текст «{text}»", count=len(found))
                    )
                return found[nth - 1], notes
            if len(found) > 1:
                listing = ", ".join(
                    f"{self.ref_label(element)} «{self.accessible_name(element)}»"
                    for element in found
                )
                raise BrowserError(E_TEXT_AMBIGUOUS.format(text=text, list=listing))
            return found[0], notes
        raise BrowserError(E_TARGET_REQUIRED)

    def clickable_ancestor(self, element: Element) -> Element | None:
        """The element or nearest ancestor a click acts on."""

        def clickable(node: Element) -> bool:
            if node.tag == "a" and "href" in node.attrs:
                return True
            if node.tag in ("button", "summary", "label"):
                return True
            if node.tag == "input" and self.field_kind(node) in BUTTON_INPUT_TYPES:
                return True
            return (node.attrs.get("role") or "").lower().split()[:1] == ["button"]

        return element.closest(clickable)

    # -- structure --------------------------------------------------------

    def _ensure_stats(self) -> None:
        if self._text_len is not None:
            return
        elements = self.document.elements
        text_len = [0] * len(elements)
        link_len = [0] * len(elements)
        for element in reversed(elements):
            if element.tag != "#document" and self.is_hidden(element):
                continue
            total = 0
            links = 0
            for child in element.children:
                if isinstance(child, str):
                    if child and not child.isspace():
                        total += len(normalize_space(child))
                else:
                    total += text_len[child.index]
                    links += link_len[child.index]
            text_len[element.index] = total
            if element.tag == "a" and "href" in element.attrs:
                links = total
            link_len[element.index] = links
        self._text_len = text_len
        self._link_len = link_len

    def text_length(self, element: Element) -> int:
        """Visible text characters in the element's subtree."""
        self._ensure_stats()
        assert self._text_len is not None
        return self._text_len[element.index]

    def link_text_length(self, element: Element) -> int:
        self._ensure_stats()
        return self._link_len[element.index]

    def visible_text_length(self) -> int:
        return self.text_length(self.body)

    @property
    def main(self) -> Element:
        if self._main is None:
            self._main, self._main_how = self._find_main()
        return self._main

    @property
    def main_how(self) -> str:
        """How main was found: "main", "article", "scored" or "body"."""
        self.main  # noqa: B018 — computes main and main_how
        return self._main_how

    def _in_body(self, element: Element) -> bool:
        body = self.body
        if body.tag == "#document":
            return True
        node: Element | None = element
        while node is not None:
            if node is body:
                return True
            node = node.parent
        return False

    def _can_be_main(self, element: Element) -> bool:
        if element.tag in _NEVER_MAIN or element is self.body:
            return False
        role = (element.attrs.get("role") or "").split()[:1]
        return not (role and role[0].lower() in _LANDMARK_ROLES)

    def _find_main(self) -> tuple[Element, str]:
        body = self.body
        body_len = self.text_length(body)
        if body_len == 0:
            return body, "body"
        elements = self.document.elements
        text_len = self._text_len
        assert text_len is not None
        mains = [
            element
            for element in elements
            if (
                element.tag == "main"
                or (element.attrs.get("role") or "").lower().split()[:1] == ["main"]
            )
            and not self.is_hidden(element)
            and self._in_body(element)
        ]
        if len(mains) == 1:
            return mains[0], "main"
        for element in mains:
            if text_len[element.index] >= MAIN_SHARE * body_len:
                return element, "main"
        articles = [
            element
            for element in elements
            if element.tag == "article"
            and not self.is_hidden(element)
            and self._in_body(element)
            and element.parent is not None
            and element.parent.closest(lambda node: node.tag == "article") is None
        ]
        for element in articles:
            if text_len[element.index] >= ARTICLE_SHARE * body_len:
                return element, "article"
        fair = [
            element
            for element in articles
            if text_len[element.index] >= ARTICLE_ONLY_SHARE * body_len
        ]
        if len(fair) == 1:
            return fair[0], "article"
        scores: dict[int, float] = {}
        for element in elements:
            tag = element.tag
            if tag not in _SCORE_TAGS:
                if tag != "div" or any(
                    kid.tag in _LEAF_DIV_BLOCKERS for kid in element.element_children()
                ):
                    continue
            length = text_len[element.index]
            if length < PARAGRAPH_MIN_CHARS or self.is_hidden(element):
                continue
            commas = len(_COMMAS.findall(element.text()))
            score = 1.0 + commas + min(length / 100.0, MAX_LENGTH_BONUS)
            ancestor = element.parent
            for weight in (1.0, 0.5, 1.0 / 3.0):
                if ancestor is None or ancestor.tag == "#document":
                    break
                if self._can_be_main(ancestor):
                    scores[ancestor.index] = scores.get(ancestor.index, 0.0) + score * weight
                ancestor = ancestor.parent
        best: Element | None = None
        best_score = 0.0
        for index, raw in scores.items():
            element = elements[index]
            length = text_len[index]
            if not length or not self._in_body(element):
                continue
            density = self._link_len[index] / length
            identity = f"{element.attrs.get('class', '')} {element.attrs.get('id', '')}"
            weight = 1.0
            if NEGATIVE.search(identity):
                weight *= NEGATIVE_WEIGHT
            if POSITIVE.search(identity):
                weight *= POSITIVE_WEIGHT
            final = raw * (1.0 - density) * weight
            if final > best_score:
                best, best_score = element, final
        if best is not None:
            length = text_len[best.index]
            if length >= SCORED_MIN_SHARE * body_len and length >= SCORED_MIN_CHARS:
                return best, "scored"
        return body, "body"

    def headings(self) -> list[Heading]:
        """Visible headings (h1–h6, role=heading with aria-level) with section numbers."""
        if self._headings is not None:
            return self._headings
        result: list[Heading] = []
        stack: list[list[int]] = []  # [level, count]
        for element in self.document.elements:
            level: int | None = None
            if element.tag in HEADING_TAGS:
                level = int(element.tag[1])
            elif (element.attrs.get("role") or "").lower().split()[:1] == ["heading"]:
                raw = (element.attrs.get("aria-level") or "").strip()
                if raw.isdigit():
                    level = max(1, int(raw))
            if level is None or self.is_hidden(element):
                continue
            text = self._name_text(element)
            if not text:
                continue
            popped: list[int] | None = None
            while stack and stack[-1][0] > level:
                popped = stack.pop()
            if stack and stack[-1][0] == level:
                stack[-1][1] += 1
            else:
                stack.append([level, popped[1] + 1 if popped is not None else 1])
            number = ".".join(str(count) for _, count in stack)
            result.append(Heading(level, text, element, number))
            self._heading_numbers[element.index] = number
        self._headings = result
        return result

    def heading_number(self, element: Element) -> str | None:
        self.headings()
        return self._heading_numbers.get(element.index)

    def _landmark_indices(self, root: Element) -> set[int]:
        skipped: set[int] = set()
        for element in root.descendants():
            if element.index in skipped:
                continue
            role = (element.attrs.get("role") or "").lower().split()[:1]
            if element.tag in ("nav", "aside") or role in (["navigation"], ["complementary"]):
                skipped.update(node.index for node in element.iter())
        return skipped

    def outside_main_counts(self, *, include_landmarks: bool = False) -> dict[str, int]:
        """Visible refs per kind outside main (and, optionally, in skipped landmarks)."""
        main = self.main
        inside = {element.index for element in main.iter()}
        skipped = self._landmark_indices(main) if include_landmarks else set()
        counts = {name: 0 for name in REF_COUNT_NAMES.values()}
        for ref, element in self.refs.items():
            if self.is_hidden(element):
                continue
            if element.index not in inside or element.index in skipped:
                counts[REF_COUNT_NAMES[ref[0]]] += 1
        return counts

    def snapshot_nodes(self) -> list[dict[str, Any]]:
        """L/B/I refs as semantic nodes (role, name, masked value, states)."""
        defaults = {"L": "link", "B": "button", "I": "textbox"}
        nodes: list[dict[str, Any]] = []
        for ref, element in self.refs.items():
            prefix = ref[0]
            if prefix not in defaults:
                continue
            nodes.append(
                {
                    "id": ref,
                    "role": self.role(element) or defaults[prefix],
                    "name": self.accessible_name(element) or None,
                    "value": self.display_value(element) if prefix == "I" else None,
                    "states": self.element_states(element),
                }
            )
        return nodes

    def script_rendered_reason(self) -> str | None:
        """Why the http page looks assembled by JavaScript, or None."""
        if self.stamped:
            return None
        length = self.visible_text_length()
        scripts = self.document.script_count
        if length < SCRIPT_PAGE_MAX_TEXT and scripts >= 1:
            word = plural(scripts, "скрипте", "скриптах", "скриптах")
            return f"почти нет текста ({length} симв.) при {scripts} {word}"
        if length < EMPTY_MOUNT_PAGE_TEXT:
            for text, selector in _mount_selectors():
                for element in css.select(self.document, selector):
                    if self.text_length(element) < EMPTY_MOUNT_MAX_TEXT:
                        return f"пустой контейнер приложения {text}"
        if length < NOSCRIPT_PAGE_TEXT:
            for element in self.document.elements:
                if element.tag == "noscript" and _NOSCRIPT_JS.search(
                    normalize_space(element.text())
                ):
                    return "страница просит включить JavaScript"
        return None


__all__ = [
    "ATTRIBUTE_NAMES",
    "CHECKED_ATTR",
    "FieldState",
    "HIDDEN_ATTR",
    "Heading",
    "MATCH_ATTR",
    "PageModel",
    "REF_ATTR",
    "REF_COUNT_NAMES",
    "REF_KINDS",
    "SELECTED_ATTR",
    "SRC_ATTR",
    "VALUE_ATTR",
    "VALUE_LEN_ATTR",
    "element_descriptor",
    "option_label",
    "option_value",
]
