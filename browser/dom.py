"""A tolerant document tree built with the standard HTMLParser (HTML and XML modes).

The builder applies the common implicit-close rules of the HTML parsing
algorithm (paragraphs, list items, table rows and cells, options, headings),
keeps going after malformed markup and never recurses: every walk over the
tree uses an explicit stack, so deeply nested documents cannot overflow.
Elements carry their document-order ``index``, which stays the same when the
same text is parsed again; field state is keyed by it.
"""

from __future__ import annotations

import html
import logging
import re
from html.parser import HTMLParser
from typing import Any, Callable, Iterator

logger = logging.getLogger("logos.browser.dom")

VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
        "keygen",
        "frame",
    }
)
HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_P_CLOSERS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "details",
        "dialog",
        "div",
        "dl",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "header",
        "hgroup",
        "hr",
        "main",
        "menu",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "ul",
        "li",
        "dd",
        "dt",
    }
    | HEADING_TAGS
)
# Elements an end tag does not cross while looking for its open element.
_END_SCOPE = frozenset({"html", "body", "table", "td", "th", "caption", "template"})
_TABLE_SCOPE = frozenset({"html", "table", "template"})
_TABLE_TAGS = frozenset(
    {"table", "tbody", "thead", "tfoot", "tr", "td", "th", "caption", "colgroup"}
)
# Scope for closing an open <p> or <button> when a new block starts.
_BUTTON_SCOPE = frozenset(
    {
        "applet",
        "caption",
        "html",
        "table",
        "td",
        "th",
        "marquee",
        "object",
        "template",
        "button",
    }
)
_LIST_STOP = frozenset({"table", "td", "th", "caption", "template", "html", "body"})
_HEAD_CONTENT = frozenset(
    {
        "title",
        "meta",
        "link",
        "base",
        "style",
        "script",
        "noscript",
        "template",
        "basefont",
        "bgsound",
    }
)
# Raw-text elements whose text is never shown (fallback or style content).
_DROPPED_TEXT = frozenset({"style", "iframe", "noembed", "noframes"})
# Raw-text elements serialized without escaping.
_RAW_TEXT = frozenset(
    {"script", "style", "xmp", "iframe", "noembed", "noframes", "plaintext"}
)
_JS_TYPES = frozenset(
    {
        "",
        "module",
        "text/javascript",
        "application/javascript",
        "application/ecmascript",
        "text/ecmascript",
        "application/x-javascript",
        "text/jscript",
        "text/livescript",
        "text/x-javascript",
        "text/x-ecmascript",
        "text/babel",
        "text/jsx",
    }
)
_CHUNK = 64 * 1024
_CDATA_SECTION = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)


def normalize_space(text: str | None) -> str:
    """Runs of whitespace (including U+00A0) become one space; ends are stripped."""
    if not text:
        return ""
    return " ".join(text.split())


class Element:
    """One element: lowercase tag, attributes, children (elements and strings)."""

    __slots__ = ("tag", "attrs", "children", "parent", "index", "_cache")

    def __init__(
        self,
        tag: str,
        attrs: dict[str, str] | None = None,
        parent: Element | None = None,
        index: int = -1,
    ) -> None:
        self.tag = tag
        self.attrs: dict[str, str] = attrs if attrs is not None else {}
        self.children: list[Element | str] = []
        self.parent = parent
        self.index = index
        self._cache: dict[str, Any] | None = None

    def __repr__(self) -> str:
        return f"<Element {self.tag} #{self.index}>"

    def _cache_dict(self) -> dict[str, Any]:
        if self._cache is None:
            self._cache = {}
        return self._cache

    def get(self, name: str, default: Any = None) -> Any:
        return self.attrs.get(name, default)

    def has(self, name: str) -> bool:
        return name in self.attrs

    @property
    def id(self) -> str | None:
        return self.attrs.get("id")

    @property
    def classes(self) -> frozenset[str]:
        cache = self._cache_dict()
        value = cache.get("classes")
        if value is None:
            value = frozenset(self.attrs.get("class", "").split())
            cache["classes"] = value
        return value

    def iter(self) -> Iterator[Element]:
        """This element and its descendants in document order."""
        stack: list[Element] = [self]
        while stack:
            node = stack.pop()
            yield node
            kids = [child for child in node.children if isinstance(child, Element)]
            kids.reverse()
            stack.extend(kids)

    def descendants(self) -> Iterator[Element]:
        walker = self.iter()
        next(walker, None)
        return walker

    def element_children(self) -> list[Element]:
        return [child for child in self.children if isinstance(child, Element)]

    def ancestors(self) -> Iterator[Element]:
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def closest(self, pred: Callable[[Element], bool]) -> Element | None:
        """This element or its nearest ancestor for which ``pred`` is true."""
        node: Element | None = self
        while node is not None:
            if node.tag != "#document" and pred(node):
                return node
            node = node.parent
        return None

    def text(self, *, skip: tuple[str, ...] = ("script", "style", "template")) -> str:
        """The raw concatenated text of the subtree, without ``skip`` descendants."""
        parts: list[str] = []
        stack: list[Element | str] = [self]
        while stack:
            node = stack.pop()
            if isinstance(node, str):
                parts.append(node)
                continue
            if node is not self and node.tag in skip:
                if not (node.tag == "template" and "shadowrootmode" in node.attrs):
                    continue
            stack.extend(reversed(node.children))
        return "".join(parts)

    def _positions(self, key: str) -> tuple[int, int]:
        parent = self.parent
        if parent is None:
            return (1, 1)
        cache = self._cache_dict()
        value = cache.get(key)
        if value is None:
            kids = parent.element_children()
            total = len(kids)
            by_tag: dict[str, list[Element]] = {}
            for position, kid in enumerate(kids, 1):
                kid._cache_dict()["sibling_pos"] = (position, total)
                by_tag.setdefault(kid.tag, []).append(kid)
            for group in by_tag.values():
                size = len(group)
                for position, kid in enumerate(group, 1):
                    kid._cache_dict()["type_pos"] = (position, size)
            value = cache[key]
        return value

    def sibling_position(self) -> tuple[int, int]:
        """1-based position among the parent's element children, and their count."""
        return self._positions("sibling_pos")

    def of_type_position(self) -> tuple[int, int]:
        """1-based position among same-tag siblings, and their count."""
        return self._positions("type_pos")


class Document:
    """A parsed document: the root element and every element in document order."""

    def __init__(self, xml_mode: bool = False) -> None:
        self.root = Element("#document", index=0)
        self.root._cache = {"xml_mode": xml_mode}
        self.xml_mode = xml_mode
        self.doctype: str | None = None
        self.elements: list[Element] = [self.root]
        self.parse_warnings: list[str] = []
        self.script_count = 0
        self.script_bytes = 0
        self._found: dict[str, Element | None] = {}
        self._ids: dict[str, Element] | None = None

    def _first(self, tag: str) -> Element | None:
        if tag not in self._found:
            self._found[tag] = next(
                (el for el in self.elements if el.tag == tag), None
            )
        return self._found[tag]

    @property
    def html(self) -> Element | None:
        return next(
            (el for el in self.root.element_children() if el.tag == "html"), None
        )

    @property
    def head(self) -> Element | None:
        return None if self.xml_mode else self._first("head")

    @property
    def body(self) -> Element:
        if self.xml_mode:
            return self.root
        return self._first("body") or self.root

    @property
    def title(self) -> str:
        """The document title; an SVG <title> (an icon's name) does not count in HTML."""
        if "#title" not in self._found:
            found = None
            for element in self.elements:
                if element.tag != "title":
                    continue
                if not self.xml_mode and any(
                    ancestor.tag in ("svg", "math") for ancestor in element.ancestors()
                ):
                    continue
                found = element
                break
            self._found["#title"] = found
        element = self._found["#title"]
        return normalize_space(element.text()) if element is not None else ""

    @property
    def base_href(self) -> str | None:
        if "#base_href" not in self._found:
            found = next(
                (
                    el
                    for el in self.elements
                    if el.tag == "base" and el.attrs.get("href", "").strip()
                ),
                None,
            )
            self._found["#base_href"] = found
        element = self._found["#base_href"]
        return element.attrs["href"].strip() if element is not None else None

    def by_id(self, element_id: str | None) -> Element | None:
        if not element_id:
            return None
        if self._ids is None:
            ids: dict[str, Element] = {}
            for el in self.elements:
                value = el.attrs.get("id")
                if value and value not in ids:
                    ids[value] = el
            self._ids = ids
        return self._ids.get(element_id)


def _is_js(type_value: str) -> bool:
    return type_value.split(";", 1)[0].strip().lower() in _JS_TYPES


class _Builder(HTMLParser):
    """HTMLParser that builds a Document with the browser's implicit closes."""

    def __init__(self, document: Document, xml_mode: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.doc = document
        self.xml = xml_mode
        if xml_mode:
            # Feed and sitemap elements named script, style, title or textarea
            # hold markup, not raw text.
            self.CDATA_CONTENT_ELEMENTS = ()
            self.RCDATA_CONTENT_ELEMENTS = ()
        self.stack: list[Element] = [document.root]
        self.pending: list[str] = []
        self.special: dict[str, Element] = {}

    # -- helpers ----------------------------------------------------------

    def flush_text(self) -> None:
        if not self.pending:
            return
        text = "".join(self.pending)
        self.pending = []
        if not text:
            return
        target = self.stack[-1]
        if target.children and isinstance(target.children[-1], str):
            target.children[-1] = target.children[-1] + text
        else:
            target.children.append(text)

    def append(self, tag: str, attrs: dict[str, str]) -> Element:
        parent = self.stack[-1]
        element = Element(tag, attrs, parent, len(self.doc.elements))
        self.doc.elements.append(element)
        parent.children.append(element)
        return element

    def close_in_scope(self, tag: str, boundaries: frozenset[str]) -> bool:
        """Pop up to and including the nearest open ``tag`` inside the scope."""
        stack = self.stack
        for position in range(len(stack) - 1, 0, -1):
            current = stack[position].tag
            if current == tag:
                del stack[position:]
                return True
            if current in boundaries:
                return False
        return False

    def close_lowest(self, targets: frozenset[str], stop: frozenset[str]) -> None:
        """Pop up to the deepest-in-stack ``targets`` element above a ``stop`` one."""
        stack = self.stack
        lowest = None
        for position in range(len(stack) - 1, 0, -1):
            current = stack[position].tag
            if current in stop:
                break
            if current in targets:
                lowest = position
        if lowest is not None:
            del stack[lowest:]

    def has_open(self, tag: str, stop: frozenset[str] = frozenset()) -> bool:
        for element in reversed(self.stack):
            if element.tag == tag:
                return True
            if element.tag in stop:
                return False
        return False

    def close_head(self) -> None:
        if self.has_open("head"):
            self.close_in_scope("head", frozenset({"html"}))

    def implicit_closes(self, tag: str) -> None:
        if tag in _P_CLOSERS:
            self.close_in_scope("p", _BUTTON_SCOPE)
        if tag == "li":
            self.close_lowest(frozenset({"li"}), frozenset({"ul", "ol", "menu"}) | _LIST_STOP)
        elif tag in ("dt", "dd"):
            self.close_lowest(frozenset({"dt", "dd"}), frozenset({"dl"}) | _LIST_STOP)
        elif tag == "tr":
            self.close_lowest(
                frozenset({"td", "th", "tr"}),
                frozenset({"table", "tbody", "thead", "tfoot", "template", "html"}),
            )
        elif tag in ("td", "th"):
            self.close_lowest(
                frozenset({"td", "th"}),
                frozenset({"tr", "table", "tbody", "thead", "tfoot", "template", "html"}),
            )
        elif tag in ("thead", "tbody", "tfoot"):
            self.close_lowest(
                frozenset({"thead", "tbody", "tfoot", "tr", "td", "th"}),
                frozenset({"table", "template", "html"}),
            )
        elif tag == "option":
            self.close_lowest(
                frozenset({"option"}),
                frozenset({"select", "datalist", "optgroup", "html", "body", "template"}),
            )
        elif tag == "optgroup":
            self.close_lowest(
                frozenset({"option", "optgroup"}),
                frozenset({"select", "datalist", "html", "body", "template"}),
            )
        elif tag in HEADING_TAGS:
            if self.stack[-1].tag in HEADING_TAGS:
                self.stack.pop()
        elif tag == "a":
            self.close_in_scope(
                "a", frozenset({"table", "td", "th", "caption", "template", "html"})
            )
        elif tag == "button":
            self.close_in_scope("button", _BUTTON_SCOPE - {"button"})
        elif tag in ("rb", "rtc"):
            self.close_lowest(
                frozenset({"rb", "rt", "rp", "rtc"}), frozenset({"ruby", "html", "body"})
            )
        elif tag in ("rt", "rp"):
            self.close_lowest(
                frozenset({"rb", "rt", "rp"}), frozenset({"ruby", "rtc", "html", "body"})
            )
        elif tag in ("input", "textarea", "keygen"):
            if self.has_open("select", frozenset({"template", "html"})):
                self.close_in_scope("select", frozenset({"template", "html"}))

    # -- HTMLParser callbacks ---------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.flush_text()
        attributes: dict[str, str] = {}
        for name, value in attrs:
            if name not in attributes:
                attributes[name] = "" if value is None else value
        if self.xml:
            self.stack.append(self.append(tag, attributes))
            return
        if tag == "image":
            tag = "img"
        if tag in ("html", "head", "body"):
            existing = self.special.get(tag)
            if existing is not None:
                for name, value in attributes.items():
                    existing.attrs.setdefault(name, value)
                return
            if tag == "head" and "body" in self.special:
                return
            if tag == "body":
                self.close_head()
        elif self.stack[-1].tag == "head" and tag not in _HEAD_CONTENT:
            self.close_head()
        if tag == "form" and self.has_open("form"):
            return
        if tag == "select" and self.has_open("select", frozenset({"template", "html"})):
            self.close_in_scope("select", frozenset({"template", "html"}))
            return
        self.implicit_closes(tag)
        element = self.append(tag, attributes)
        if tag in ("html", "head", "body"):
            self.special[tag] = element
        if tag == "script" and _is_js(attributes.get("type", "")):
            self.doc.script_count += 1
        if tag in VOID_ELEMENTS:
            return
        self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.xml:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)
            return
        # HTML ignores the self-closing flag on non-void elements.
        self.handle_starttag(tag, attrs)
        if tag in self.CDATA_CONTENT_ELEMENTS:
            self.set_cdata_mode(tag)
        elif tag in getattr(self, "RCDATA_CONTENT_ELEMENTS", ()):
            try:
                self.set_cdata_mode(tag, escapable=True)  # type: ignore[call-arg]
            except TypeError:
                pass

    def handle_endtag(self, tag: str) -> None:
        self.flush_text()
        if self.xml:
            stack = self.stack
            for position in range(len(stack) - 1, 0, -1):
                if stack[position].tag == tag:
                    del stack[position:]
                    return
            return
        if tag == "image":
            tag = "img"
        if tag == "br":
            self.append("br", {})
            return
        if tag in VOID_ELEMENTS or tag in ("html", "body"):
            # Content after </body> still belongs to the body.
            return
        if tag == "head":
            self.close_head()
            return
        if tag in _TABLE_TAGS:
            boundaries = _TABLE_SCOPE
        elif tag == "li":
            boundaries = _END_SCOPE | {"ul", "ol"}
        elif tag == "p":
            boundaries = _END_SCOPE | {"button"}
        else:
            boundaries = _END_SCOPE
        self.close_in_scope(tag, boundaries)

    def handle_data(self, data: str) -> None:
        if not data:
            return
        top = self.stack[-1]
        if not self.xml:
            if top.tag == "script":
                script_type = top.attrs.get("type", "")
                if "json" in script_type.lower():
                    self.pending.append(data)
                elif _is_js(script_type):
                    self.doc.script_bytes += len(data)
                return
            if top.tag in _DROPPED_TEXT:
                return
            if top.tag == "head" and data.strip():
                self.flush_text()
                self.close_head()
        self.pending.append(data)

    def unknown_decl(self, data: str) -> None:
        if data.startswith("CDATA["):
            self.handle_data(data[6:])

    def handle_decl(self, decl: str) -> None:
        if decl[:7].lower() == "doctype":
            self.doc.doctype = decl[7:].strip() or "html"

    def handle_comment(self, data: str) -> None:
        return

    def handle_pi(self, data: str) -> None:
        return


def parse_html(text: str, *, xml_mode: bool = False) -> Document:
    """Parse HTML (or XML with ``xml_mode``) into a Document; never raises on markup."""
    document = Document(xml_mode)
    source = text or ""
    if "\x00" in source:
        source = source.replace("\x00", "\ufffd")
    if xml_mode:
        source = _CDATA_SECTION.sub(
            lambda match: html.escape(match.group(1), quote=False), source
        )
    builder = _Builder(document, xml_mode)
    position = 0
    try:
        for position in range(0, len(source), _CHUNK):
            builder.feed(source[position : position + _CHUNK])
        position = len(source)
        builder.close()
    except Exception as exc:  # a broken document keeps the tree built so far
        document.parse_warnings.append(
            f"Разбор остановлен около символа {position}: {type(exc).__name__}"
        )
        logger.debug("parse stopped at %s: %s", position, type(exc).__name__)
    builder.flush_text()
    return document


def _xml_mode_of(node: Element) -> bool:
    root = node
    while root.parent is not None:
        root = root.parent
    if root.tag == "#document" and root._cache:
        return bool(root._cache.get("xml_mode"))
    return False


def _open_tag(element: Element, drop_prefix: str | None, *, self_close: bool = False) -> str:
    parts = [element.tag]
    for name, value in element.attrs.items():
        if drop_prefix and name.startswith(drop_prefix):
            continue
        parts.append(f'{name}="{html.escape(value, quote=True)}"')
    return "<" + " ".join(parts) + ("/>" if self_close else ">")


def serialize(
    node: Document | Element,
    *,
    drop_attr_prefix: str | None = "data-logos-",
    indent: bool = False,
) -> str:
    """Markup of a document or element; ``data-logos-*`` attributes are dropped.

    ``indent`` pretty-prints one element per line (used for XML documents).
    """
    if isinstance(node, Document):
        start = node.root
        xml_mode = node.xml_mode
        doctype = node.doctype
    else:
        start = node
        xml_mode = _xml_mode_of(node)
        doctype = None
    if indent:
        return _serialize_indented(start, drop_attr_prefix, xml_mode, doctype)
    out: list[str] = []
    if doctype:
        out.append(f"<!DOCTYPE {doctype}>")
    # Items: (kind, payload) where kind is "open", "close", "raw" or "text".
    stack: list[tuple[str, Any]] = [("open", start)]
    while stack:
        kind, payload = stack.pop()
        if kind == "text":
            out.append(html.escape(payload, quote=False))
            continue
        if kind == "raw":
            out.append(payload)
            continue
        if kind == "close":
            out.append(f"</{payload}>")
            continue
        element: Element = payload
        if element.tag != "#document":
            if not element.children and (
                xml_mode or element.tag in VOID_ELEMENTS
            ):
                out.append(_open_tag(element, drop_attr_prefix, self_close=xml_mode))
                continue
            out.append(_open_tag(element, drop_attr_prefix))
            stack.append(("close", element.tag))
        raw = not xml_mode and element.tag in _RAW_TEXT
        for child in reversed(element.children):
            if isinstance(child, Element):
                stack.append(("open", child))
            else:
                stack.append(("raw" if raw else "text", child))
    return "".join(out)


def _serialize_indented(
    start: Element, drop_prefix: str | None, xml_mode: bool, doctype: str | None
) -> str:
    lines: list[str] = []
    if doctype:
        lines.append(f"<!DOCTYPE {doctype}>")
    base_depth = -1 if start.tag == "#document" else 0
    stack: list[tuple[str, Any, int]] = [("open", start, base_depth)]
    while stack:
        kind, payload, depth = stack.pop()
        pad = "  " * max(depth, 0)
        if kind == "close":
            lines.append(pad + payload)
            continue
        if kind == "text":
            lines.append(pad + payload)
            continue
        element: Element = payload
        raw = not xml_mode and element.tag in _RAW_TEXT
        if element.tag != "#document":
            has_elements = any(isinstance(child, Element) for child in element.children)
            if not has_elements:
                text = "".join(element.children).strip()
                if not text and (xml_mode or element.tag in VOID_ELEMENTS):
                    lines.append(pad + _open_tag(element, drop_prefix, self_close=xml_mode))
                    continue
                body = text if raw else html.escape(text, quote=False)
                lines.append(
                    pad + _open_tag(element, drop_prefix) + body + f"</{element.tag}>"
                )
                continue
            lines.append(pad + _open_tag(element, drop_prefix))
            stack.append(("close", f"</{element.tag}>", depth))
        for child in reversed(element.children):
            if isinstance(child, Element):
                stack.append(("open", child, depth + 1))
            else:
                text = child.strip()
                if text:
                    stack.append(
                        ("text", text if raw else html.escape(text, quote=False), depth + 1)
                    )
    return "\n".join(lines)


__all__ = [
    "Document",
    "Element",
    "HEADING_TAGS",
    "VOID_ELEMENTS",
    "normalize_space",
    "parse_html",
    "serialize",
]
