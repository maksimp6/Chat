"""Readable Markdown and plain text of a page, with element refs inline.

Links read as ``text [L5]``, buttons as ``[B3 «name»]``, fields as
``[I2 поле «label» = "value"]``, forms, images and frames get their own
markers, data tables become Markdown tables. The walk over the tree uses an
explicit stack; tables call a flat renderer for their cells, which never
renders tables itself, so nesting depth cannot grow the call stack.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from browser.dom import HEADING_TAGS, Element
from browser.forms import form_info
from browser.page import PageModel, option_label
from browser.paging import plural
from browser.redact import is_sensitive_name

logger = logging.getLogger("logos.browser.readable")

LONG_CELL_CHARS = 200
MAX_COLSPAN = 1000
MAX_ROWSPAN = 65534

KIND_WORDS = {
    "text": "поле",
    "search": "поиск",
    "email": "email",
    "tel": "телефон",
    "url": "адрес",
    "password": "пароль",
    "number": "число",
    "date": "дата",
    "datetime-local": "дата",
    "month": "дата",
    "week": "дата",
    "time": "время",
    "color": "цвет",
    "range": "ползунок",
    "checkbox": "флажок",
    "radio": "переключатель",
    "select": "список",
    "select-multiple": "список",
    "textarea": "текст",
    "file": "файл",
    "contenteditable": "редактор",
    "hidden": "скрытое",
}
_ROLE_KIND_WORDS = {
    "combobox": "список",
    "listbox": "список",
    "checkbox": "флажок",
    "switch": "переключатель",
    "radio": "переключатель",
    "slider": "ползунок",
    "spinbutton": "число",
    "searchbox": "поиск",
    "textbox": "поле",
}
_NEVER_RENDER = frozenset(
    {
        "script",
        "style",
        "head",
        "title",
        "meta",
        "link",
        "base",
        "param",
        "source",
        "track",
        "datalist",
        "noframes",
        "desc",
        "defs",
        "symbol",
        "clippath",
        "mask",
        "lineargradient",
        "radialgradient",
        "pattern",
        "filter",
        "metadata",
        "canvas",
        "embed",
        "noembed",
    }
)
_BLOCKS = frozenset(
    {
        "#document",
        "html",
        "body",
        "address",
        "article",
        "aside",
        "center",
        "details",
        "dialog",
        "div",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "header",
        "hgroup",
        "legend",
        "main",
        "nav",
        "noscript",
        "p",
        "section",
        "search",
        "caption",
        "table",
        "tbody",
        "thead",
        "tfoot",
        "tr",
        "td",
        "th",
        "frameset",
        "optgroup",
        "option",
        "object",
    }
)
_CONTAINER_BLOCKS = frozenset(
    {
        "p",
        "div",
        "ul",
        "ol",
        "li",
        "dl",
        "table",
        "section",
        "article",
        "header",
        "footer",
        "blockquote",
        "pre",
        "form",
        "figure",
        "main",
        "nav",
        "aside",
    }
    | HEADING_TAGS
)
_EMPHASIS = {
    "strong": "**",
    "b": "**",
    "em": "*",
    "i": "*",
    "s": "~~",
    "del": "~~",
    "strike": "~~",
    "code": "`",
    "kbd": "`",
    "samp": "`",
    "tt": "`",
    "q": "«»",
    "abbr": "",
}
_PRE_TAGS = frozenset({"pre", "listing", "xmp", "plaintext"})
_WHITESPACE = re.compile(r"\s+")
_SPACES = re.compile(r"[ \t\f\v\r]+")
_AROUND_BREAKS = re.compile(r" ?\n ?")
_HEADING_MARK = re.compile(r"\x00(\d+)\x00")
_LANGUAGE = re.compile(r"^(?:language|lang)-(.+)$", re.I)


@dataclass
class Rendered:
    text: str
    headings: list[tuple[int, str, int, str]] = field(default_factory=list)  # offset, number, level, title


def _collapse(text: str) -> str:
    """Collapse inline whitespace; keep hard line breaks; strip the ends."""
    text = _SPACES.sub(" ", text)
    text = _AROUND_BREAKS.sub("\n", text)
    return text.strip(" \n")


def _role(element: Element) -> str:
    return (element.attrs.get("role") or "").lower().split(" ")[0]


def _heading_level(element: Element) -> int | None:
    if element.tag in HEADING_TAGS:
        return int(element.tag[1])
    if _role(element) == "heading":
        raw = (element.attrs.get("aria-level") or "").strip()
        if raw.isdigit():
            return max(1, int(raw))
    return None


def _quote_value(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def field_inline(model: PageModel, element: Element) -> str:
    """"[I2 {kind word} «label» (name=x) = "value"]" for a field."""
    ref = model.ref_of(element)
    kind = model.field_kind(element)
    role = model.role(element) or ""
    word = KIND_WORDS.get(kind) if kind != "custom" else _ROLE_KIND_WORDS.get(role, "поле")
    word = word or "поле"
    checkable = kind in ("checkbox", "radio") or (
        kind == "custom" and role in ("checkbox", "radio", "switch")
    )
    if checkable:
        word += " ✓" if model.field_checked(element) else " ✗"
    parts = [ref] if ref else []
    parts.append(word)
    label = model.accessible_name(element)
    if label:
        parts.append(f"«{label}»")
    name = element.attrs.get("name")
    if name and name != label:
        parts.append(f"(name={name})")
    value_part = ""
    if kind in ("select", "select-multiple"):
        options = model.options(element)
        chosen = [option_label(option) for option in model.selected_options(element)]
        count = len(options)
        variants = f"{count} {plural(count, 'вариант', 'варианта', 'вариантов')}"
        if chosen:
            value_part = "= " + ", ".join(f"«{label}»" for label in chosen) + f", {variants}"
        else:
            value_part = f"= ничего не выбрано, {variants}"
    elif checkable:
        value_part = ""
    elif kind == "file":
        files = model.state.files.get(element.index) or ()
        if files:
            value_part = "= " + ", ".join(f"«{os.path.basename(path)}»" for path in files)
    elif kind == "password":
        length = model.value_length(element)
        value_part = f"= (заполнено, {length} симв.)" if length else "= (пусто)"
    else:
        value = model.field_value(element)
        if value:
            if is_sensitive_name(name):
                value_part = f"= (скрыто, {len(value)} симв.)"
            else:
                value_part = "= " + _quote_value(value)
    if value_part:
        parts.append(value_part)
    if "disabled" in model.element_states(element):
        parts.append("(недоступно)")
    return "[" + " ".join(parts) + "]"


class _Frame:
    __slots__ = ("element", "kind", "start", "generation", "data")

    def __init__(self, element: Element, kind: str, start: int, generation: int, data: Any = None):
        self.element = element
        self.kind = kind
        self.start = start
        self.generation = generation
        self.data = data


class _Renderer:
    def __init__(
        self,
        model: PageModel,
        *,
        inline_urls: bool = False,
        refs: bool = True,
        include_hidden: bool = False,
        skip_landmarks: bool = False,
        flat: bool = False,
    ) -> None:
        self.model = model
        self.inline_urls = inline_urls
        self.refs = refs
        self.include_hidden = include_hidden
        self.skip_landmarks = skip_landmarks
        self.flat = flat
        self.lines: list[str] = []
        self.inline: list[str] = []
        self.frames: list[_Frame] = []
        self.containers: list[list[Any]] = []  # ["li", marker, used] | ["quote", "> ", True]
        self.lists: list[dict[str, Any]] = []
        self.dls: list[int] = []
        self.generation = 0
        self.tight = False
        self.inline_only = 0
        self.dt_pending = False
        self.marks: list[tuple[str, int, str]] = []
        self.root: Element | None = None
        self._heading_info: dict[int, Any] | None = None

    @property
    def heading_info(self) -> dict[int, Any]:
        if self._heading_info is None:
            self._heading_info = {
                heading.element.index: heading for heading in self.model.headings()
            }
        return self._heading_info

    # -- output -----------------------------------------------------------

    def add_text(self, text: str) -> None:
        if text:
            self.inline.append(_WHITESPACE.sub(" ", text))

    def flat_break(self) -> None:
        joined = "".join(self.inline).rstrip(" ")
        if joined and not joined.endswith("\n"):
            self.inline.append("\n")

    def flush(self) -> None:
        if self.flat:
            self.flat_break()
            return
        if self.inline_only:
            self.inline.append(" ")
            return
        content = _collapse("".join(self.inline))
        if not content:
            return
        self.inline = []
        self.emit(content)

    def prefix(self, *, blank: bool = False) -> str:
        parts: list[str] = []
        for container in self.containers:
            if container[0] == "quote":
                parts.append("> ")
            elif blank or container[2]:
                parts.append("  ")
            else:
                parts.append(container[1])
                container[2] = True
        text = "".join(parts)
        return text.rstrip() if blank else text

    def emit(self, text: str) -> None:
        """Write a block (already collapsed) with blank-line separation and prefixes."""
        if self.lines and not self.tight:
            self.lines.append(self.prefix(blank=True))
        self.tight = False
        self.dt_pending = False
        for line in text.split("\n"):
            self.lines.append(self.prefix() + line)
        self.generation += 1

    def emit_block(self, text: str) -> None:
        if not text:
            return
        if self.flat:
            self.flat_break()
            self.inline.append(text)
            self.inline.append("\n")
            return
        if self.inline_only:
            self.inline.append(" " + text + " ")
            return
        self.flush()
        self.emit(text)

    def attach(self, marker: str) -> None:
        """Append a marker after content whose inline frame was split by blocks."""
        if not marker:
            return
        if self.flat or _collapse("".join(self.inline)) or not self.lines:
            self.inline.append(" " + marker + " ")
            return
        self.lines[-1] = self.lines[-1].rstrip() + " " + marker

    def push(self, element: Element, kind: str, data: Any = None) -> bool:
        self.frames.append(_Frame(element, kind, len(self.inline), self.generation, data))
        return True

    def take_since(self, frame: _Frame) -> tuple[str, bool, bool]:
        raw = "".join(self.inline[frame.start :])
        del self.inline[frame.start :]
        return _collapse(raw), raw[:1].isspace(), raw[-1:].isspace()

    def put(self, text: str, lead: bool, trail: bool) -> None:
        if text:
            self.inline.append((" " if lead else "") + text + (" " if trail else ""))
        elif lead or trail:
            self.inline.append(" ")

    # -- walk -------------------------------------------------------------

    def should_render(self, element: Element) -> bool:
        if element is self.root:
            return True
        tag = element.tag
        if tag in _NEVER_RENDER:
            return False
        if tag == "template" and "shadowrootmode" not in element.attrs:
            return False
        if tag == "input" and (element.attrs.get("type") or "").strip().lower() == "hidden":
            return False
        # noscript visibility is part of is_hidden (render_noscript, or Chrome's own state).
        if not self.include_hidden and self.model.is_hidden(element):
            return False
        if self.skip_landmarks and (
            tag in ("nav", "aside") or _role(element) in ("navigation", "complementary")
        ):
            return False
        return True

    def run(self, root: Element) -> None:
        self.root = root
        if (
            not self.include_hidden
            and root.tag != "#document"
            and self.model.is_hidden(root)
        ):
            # The caller asked for this element: show it although it is hidden.
            self.include_hidden = True
        stack: list[tuple[str, Any]] = [("enter", root)]
        while stack:
            op, node = stack.pop()
            if op == "text":
                self.add_text(node)
                continue
            if op == "exit":
                self.exit(node)
                continue
            if not self.should_render(node):
                continue
            if self.enter(node):
                stack.append(("exit", node))
                for child in reversed(node.children):
                    stack.append(("text" if isinstance(child, str) else "enter", child))
        self.inline_only = 0

    def is_container(self, element: Element) -> bool:
        for node in element.descendants():
            if node.tag in _CONTAINER_BLOCKS or self.model.ref_of(node):
                return True
        return False

    def enter(self, element: Element) -> bool:
        model = self.model
        tag = element.tag
        ref = model.ref_of(element)
        prefix = ref[0] if ref else ""
        if prefix == "T":
            if self.flat or is_layout_table(model, element):
                return self.block(element)
            self.emit_block(render_table(model, element, refs=self.refs))
            return False
        if prefix == "F":
            self.flush() if not self.flat else self.flat_break()
            if self.refs:
                self.emit_block(self.form_marker(element, ref or ""))
            return self.block(element)
        if prefix == "I":
            return self.field(element, ref or "")
        if prefix == "B":
            return self.button(element, ref or "")
        if prefix == "L":
            return self.link(element, ref or "")
        if prefix == "M":
            return self.media(element, ref or "")
        level = _heading_level(element)
        if level is not None:
            return self.heading(element, level)
        if tag in _PRE_TAGS:
            self.pre(element)
            return False
        if tag == "br":
            self.inline.append("\n")
            return False
        if tag == "hr":
            if self.flat:
                self.flat_break()
            elif self.refs:
                self.emit_block("---")
            else:
                self.flush()
            return False
        if tag == "img":
            if self.refs:
                alt = model.accessible_name(element)
                self.inline.append(f" [изображение «{alt}»] " if alt else " [изображение] ")
            return False
        if tag in ("input", "select", "textarea", "video", "audio", "iframe", "frame"):
            return False
        if tag in ("ul", "ol", "menu", "dir"):
            return self.list_start(element)
        if tag == "li":
            return self.list_item(element)
        if tag == "blockquote":
            if self.flat:
                return self.block(element)
            self.flush()
            if self.refs:
                self.containers.append(["quote", "> ", True])
                return self.push(element, "quote")
            return self.push(element, "block")
        if tag == "dl":
            if self.flat:
                return self.block(element)
            self.flush()
            self.dls.append(0)
            return self.push(element, "dl")
        if tag in ("dt", "dd"):
            return self.definition(element)
        if tag in _EMPHASIS:
            return self.push(element, "emph", tag)
        if self.flat and tag in ("td", "th"):
            return self.push(element, "cell")
        if tag in _BLOCKS:
            return self.block(element)
        return True

    def exit(self, element: Element) -> None:
        if not self.frames or self.frames[-1].element is not element:
            return
        frame = self.frames.pop()
        kind = frame.kind
        if kind == "block":
            self.flush()
        elif kind == "quote":
            self.flush()
            if self.containers and self.containers[-1][0] == "quote":
                self.containers.pop()
        elif kind == "list":
            self.flush()
            if self.lists:
                self.lists.pop()
            self.tight = False
        elif kind == "li":
            self.flush()
            if self.containers and self.containers[-1][0] == "li":
                self.containers.pop()
        elif kind == "dl":
            self.flush()
            if self.dls:
                self.dls.pop()
        elif kind == "dt":
            self.inline_only = max(0, self.inline_only - 1)
            content, lead, _ = self.take_since(frame)
            if content:
                term = f"**{content}**:" if self.refs else f"{content}:"
                self.put(term, lead, True)
                self.dt_pending = True
        elif kind == "heading":
            self.end_heading(frame)
        elif kind == "link":
            self.end_link(frame)
        elif kind == "container":
            marker = frame.data[0]
            self.attach(marker)
            if frame.data[1]:
                self.flush()
        elif kind == "emph":
            self.end_emphasis(frame)
        elif kind == "cell":
            self.inline.append(" ")

    # -- element kinds ----------------------------------------------------

    def block(self, element: Element) -> bool:
        self.flush()
        return self.push(element, "block")

    def form_marker(self, form: Element, ref: str) -> str:
        info = form_info(self.model, form)
        return f"[{ref} форма → {info.method.upper()} {info.action}]"

    def field(self, element: Element, ref: str) -> bool:
        native = element.tag in ("input", "select", "textarea")
        if native or not self.is_container(element):
            if self.refs:
                self.inline.append(" " + field_inline(self.model, element) + " ")
            return False
        marker = field_inline(self.model, element) if self.refs else ""
        return self.push(element, "container", (marker, False))

    def button(self, element: Element, ref: str) -> bool:
        model = self.model
        summary = element.tag == "summary"
        parent = element.parent
        closed = (
            summary
            and parent is not None
            and parent.tag == "details"
            and "open" not in parent.attrs
        )
        suffix = " (свёрнуто)" if closed and self.refs else ""
        if element.tag in ("button", "input") or not self.is_container(element):
            name = model.accessible_name(element)
            if self.refs:
                text = (f"[{ref} «{name}»]" if name else f"[{ref}]") + suffix
            else:
                text = name
            if summary:
                self.flush()
                self.inline.append(text)
                self.flush()
            else:
                self.inline.append(" " + text + " ")
            return False
        if summary:
            self.flush()
        marker = f"[{ref}]{suffix}" if self.refs else ""
        return self.push(element, "container", (marker, summary))

    def link_marker(self, element: Element, ref: str, content: str) -> str:
        url = self.model.href(element)
        if self.refs:
            if self.inline_urls and url:
                return f"{content} [{ref} → {url}]"
            return f"{content} [{ref}]"
        if self.inline_urls and url:
            return f"{content} ({url})"
        return content

    def link(self, element: Element, ref: str) -> bool:
        model = self.model
        if element.tag in ("iframe", "frame"):
            if self.refs:
                title = model.accessible_name(element)
                source = model.href(element)
                text = f"[{ref} фрейм" + (f" «{title}»" if title else "")
                text += (f" → {source}" if source else "") + "]"
                self.inline.append(" " + text + " ")
            return False
        if element.tag == "area":
            name = model.accessible_name(element) or "область"
            self.inline.append(" " + self.link_marker(element, ref, name) + " ")
            return False
        return self.push(element, "link", ref)

    def end_link(self, frame: _Frame) -> None:
        element, ref = frame.element, frame.data
        if frame.generation != self.generation:
            if self.refs:
                url = self.model.href(element)
                marker = f"[{ref} → {url}]" if self.inline_urls and url else f"[{ref}]"
            else:
                marker = ""
            self.attach(marker)
            return
        content, lead, trail = self.take_since(frame)
        if not content:
            content = self.model.accessible_name(element) or ("ссылка" if self.refs else "")
        if not content:
            self.put("", lead, trail)
            return
        self.put(self.link_marker(element, ref, content), lead, trail)

    def media(self, element: Element, ref: str) -> bool:
        if not self.refs:
            return False
        name = self.model.accessible_name(element)
        if element.tag in ("video", "audio"):
            word = "видео" if element.tag == "video" else "аудио"
        else:
            word = "изображение"
        text = f"[{ref} {word}" + (f" «{name}»" if name else "") + "]"
        self.inline.append(" " + text + " ")
        return False

    def heading(self, element: Element, level: int) -> bool:
        if self.flat:
            return self.block(element)
        self.flush()
        self.inline_only += 1
        return self.push(element, "heading", level)

    def end_heading(self, frame: _Frame) -> None:
        self.inline_only = max(0, self.inline_only - 1)
        content, _, _ = self.take_since(frame)
        if self.inline_only:
            self.put(content, True, True)
            return
        self.flush()
        if not content:
            return
        level = frame.data
        info = self.heading_info.get(frame.element.index)
        number = info.number if info is not None else ""
        title = info.text if info is not None else content
        mark = len(self.marks)
        self.marks.append((number, level, title))
        text = ("#" * min(level, 6) + " " + content) if self.refs else content
        self.emit(f"\x00{mark}\x00{text}")

    def end_emphasis(self, frame: _Frame) -> None:
        if frame.generation != self.generation:
            return
        tag = frame.data
        content, lead, trail = self.take_since(frame)
        if not content:
            self.put("", lead, trail)
            return
        marker = _EMPHASIS[tag]
        if tag == "q":
            wrapped = f"«{content}»"
        elif tag == "abbr":
            title = " ".join((frame.element.attrs.get("title") or "").split())
            wrapped = f"{content} ({title})" if title and self.refs and title != content else content
        elif not self.refs or "\n" in content:
            wrapped = content
        elif marker == "`":
            ticks = "``" if "`" in content else "`"
            pad = " " if content.startswith("`") or content.endswith("`") else ""
            wrapped = f"{ticks}{pad}{content}{pad}{ticks}"
        else:
            wrapped = f"{marker}{content}{marker}"
        self.put(wrapped, lead, trail)

    def pre_text(self, element: Element) -> str:
        model = self.model
        parts: list[str] = []
        stack: list[Element | str] = [element]
        while stack:
            node = stack.pop()
            if isinstance(node, str):
                parts.append(node)
                continue
            if node is not element:
                if node.tag in _NEVER_RENDER or (
                    node.tag == "template" and "shadowrootmode" not in node.attrs
                ):
                    continue
                if not self.include_hidden and model.is_hidden(node):
                    continue
                if node.tag == "br":
                    parts.append("\n")
                    continue
                ref = model.ref_of(node) if self.refs else None
                if ref and ref[0] == "I":
                    parts.append(field_inline(model, node))
                    continue
                if ref and ref[0] == "M":
                    name = model.accessible_name(node)
                    parts.append(f"[{ref} изображение" + (f" «{name}»" if name else "") + "]")
                    continue
                if ref and ref[0] in "LB":
                    stack.append(f" [{ref}]")
            stack.extend(reversed(node.children))
        text = "".join(parts)
        if text.startswith("\r\n"):
            text = text[2:]
        elif text.startswith("\n"):
            text = text[1:]
        return text.rstrip("\n").replace("\r\n", "\n")

    def pre(self, element: Element) -> None:
        text = self.pre_text(element)
        if self.flat:
            self.flat_break()
            self.inline.append(text)
            self.inline.append("\n")
            return
        if self.inline_only:
            self.inline.append(" " + " ".join(text.split()) + " ")
            return
        self.flush()
        if not text.strip():
            return
        if not self.refs:
            self.emit(text)
            return
        language = ""
        holders = [element] + [kid for kid in element.element_children() if kid.tag == "code"][:1]
        for holder in holders:
            for name in holder.attrs.get("class", "").split():
                match = _LANGUAGE.match(name)
                if match:
                    language = match.group(1)
                    break
            if language:
                break
        fence = "```"
        while fence in text:
            fence += "`"
        self.emit(f"{fence}{language}\n{text}\n{fence}")

    def list_start(self, element: Element) -> bool:
        ordered = element.tag == "ol"
        backwards = "reversed" in element.attrs
        start_raw = (element.attrs.get("start") or "").strip()
        if re.fullmatch(r"[+-]?\d+", start_raw):
            start = int(start_raw)
        elif backwards:
            start = sum(1 for kid in element.element_children() if kid.tag == "li")
        else:
            start = 1
        nested = any(container[0] == "li" for container in self.containers)
        self.lists.append(
            {"ordered": ordered, "next": start, "step": -1 if backwards else 1, "count": 0, "nested": nested}
        )
        if self.flat:
            self.flat_break()
        else:
            self.flush()
        return self.push(element, "list")

    def list_item(self, element: Element) -> bool:
        state = self.lists[-1] if self.lists else None
        marker = "- "
        if state is not None and state["ordered"]:
            value_raw = (element.attrs.get("value") or "").strip()
            number = int(value_raw) if re.fullmatch(r"[+-]?\d+", value_raw) else state["next"]
            state["next"] = number + state["step"]
            marker = f"{number}. "
        if self.flat:
            self.flat_break()
            self.inline.append(marker)
            return self.push(element, "block")
        self.flush()
        first = state is None or state["count"] == 0
        if state is not None:
            state["count"] += 1
        if not first or (state is not None and state["nested"]):
            self.tight = True
        self.containers.append(["li", marker, False])
        return self.push(element, "li")

    def definition(self, element: Element) -> bool:
        if self.flat:
            return self.block(element)
        if element.tag == "dd" and self.dt_pending:
            self.dt_pending = False
            return self.push(element, "block")
        self.flush()
        if self.dls:
            if self.dls[-1] > 0:
                self.tight = True
            self.dls[-1] += 1
        if element.tag == "dt":
            self.inline_only += 1
            return self.push(element, "dt")
        return self.push(element, "block")

    # -- results ----------------------------------------------------------

    def result(self) -> Rendered:
        self.inline_only = 0
        self.flush()
        text = "\n".join(self.lines)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
        headings: list[tuple[int, str, int, str]] = []
        out: list[str] = []
        last = 0
        removed = 0
        for match in _HEADING_MARK.finditer(text):
            out.append(text[last : match.start()])
            index = int(match.group(1))
            if index < len(self.marks):
                number, level, title = self.marks[index]
                headings.append((match.start() - removed, number, level, title))
            removed += len(match.group(0))
            last = match.end()
        out.append(text[last:])
        return Rendered("".join(out).rstrip(), headings)

    def flat_result(self) -> str:
        lines = []
        for line in "".join(self.inline).split("\n"):
            line = _SPACES.sub(" ", line).strip()
            if line:
                lines.append(line)
        return "\n".join(lines)


def render_markdown(
    model: PageModel,
    root: Element | None = None,
    *,
    inline_urls: bool = False,
    refs: bool = True,
    include_hidden: bool = False,
    skip_landmarks: bool = False,
) -> Rendered:
    """Markdown of the subtree (body by default) with refs and recorded headings."""
    renderer = _Renderer(
        model,
        inline_urls=inline_urls,
        refs=refs,
        include_hidden=include_hidden,
        skip_landmarks=skip_landmarks,
    )
    renderer.run(root if root is not None else model.body)
    return renderer.result()


def render_text(
    model: PageModel, root: Element | None = None, *, include_hidden: bool = False
) -> Rendered:
    """Plain text: no refs, no emphasis; tables as tab-separated rows."""
    return render_markdown(model, root, refs=False, include_hidden=include_hidden)


def render_flat(model: PageModel, element: Element, *, refs: bool = True) -> str:
    """The element's content as inline text; block boundaries become line breaks."""
    renderer = _Renderer(model, refs=refs, flat=True)
    renderer.run(element)
    return renderer.flat_result()


# ---------------------------------------------------------------------------
# Tables


def _table_rows(model: PageModel, table: Element) -> list[Element]:
    """Rows of this table (not of nested tables) in display order, tfoot last."""
    rows: list[Element] = []
    footer: list[Element] = []
    stack: list[tuple[Element, bool]] = [
        (kid, False) for kid in reversed(table.element_children())
    ]
    while stack:
        node, in_footer = stack.pop()
        if node.tag == "table":
            continue
        if node.tag == "tr":
            (footer if in_footer else rows).append(node)
            continue
        if node.tag in ("td", "th", "caption"):
            continue
        footer_here = in_footer or node.tag == "tfoot"
        for kid in reversed(node.element_children()):
            stack.append((kid, footer_here))
    return rows + footer


def _row_cells(row: Element) -> list[Element]:
    cells: list[Element] = []
    stack = list(reversed(row.element_children()))
    while stack:
        node = stack.pop()
        if node.tag in ("td", "th"):
            cells.append(node)
            continue
        if node.tag in ("table", "tr"):
            continue
        stack.extend(reversed(node.element_children()))
    return cells


def _span(value: str | None, low: int, high: int, default: int) -> int:
    match = re.match(r"\s*(\d+)", value or "")
    if match is None:
        return default
    return min(max(int(match.group(1)), low), high)


def _has_block(element: Element) -> bool:
    return any(node.tag in _CONTAINER_BLOCKS for node in element.descendants())


def is_layout_table(model: PageModel, table: Element) -> bool:
    """A table used for page layout: one column, a nested table or mostly long blocks."""
    if _role(table) in ("presentation", "none"):
        return True
    if any(node.tag == "table" for node in table.descendants()):
        return True
    rows = [row for row in _table_rows(model, table) if not model.is_hidden(row)]
    per_row = [_row_cells(row) for row in rows]
    cells = [cell for row_cells in per_row for cell in row_cells]
    if not cells:
        return False
    if max(len(row_cells) for row_cells in per_row) <= 1:
        return True
    long_cells = sum(
        1
        for cell in cells
        if model.text_length(cell) >= LONG_CELL_CHARS and _has_block(cell)
    )
    return long_cells * 2 >= len(cells)


def _cell_markdown(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def render_table(model: PageModel, table: Element, *, refs: bool = True) -> str:
    """"[T2] caption" and a Markdown table (tab-separated rows when refs is False)."""
    ref = model.ref_of(table)
    if is_layout_table(model, table):
        body = render_markdown(model, table, refs=refs).text
        if refs and ref:
            return f"[{ref}] таблица-раскладка:\n\n{body}" if body else f"[{ref}] таблица-раскладка"
        return body
    caption_element = next((kid for kid in table.element_children() if kid.tag == "caption"), None)
    caption = render_flat(model, caption_element, refs=refs).replace("\n", " ") if caption_element is not None else ""
    head_line = " ".join(part for part in ((f"[{ref}]" if refs and ref else ""), caption) if part)
    rows = [row for row in _table_rows(model, table) if not model.is_hidden(row)]
    grid: dict[tuple[int, int], tuple[str, bool]] = {}
    header_rows: set[int] = set()
    width = 0
    for row_index, row in enumerate(rows):
        column = 0
        cells = [cell for cell in _row_cells(row) if not model.is_hidden(cell)]
        parent = row.parent
        if parent is not None and parent.tag == "thead":
            header_rows.add(row_index)
        for cell in cells:
            while (row_index, column) in grid:
                column += 1
            colspan = _span(cell.attrs.get("colspan"), 1, MAX_COLSPAN, 1)
            rowspan = _span(cell.attrs.get("rowspan"), 0, MAX_ROWSPAN, 1)
            if rowspan == 0:
                rowspan = len(rows) - row_index
            text = render_flat(model, cell, refs=refs)
            for offset in range(colspan):
                for down in range(rowspan):
                    target = row_index + down
                    if target >= len(rows):
                        break
                    key = (target, column + offset)
                    if key not in grid:
                        grid[key] = (text if offset == 0 else "", offset == 0 and down == 0)
            column += colspan
            width = max(width, column)
    if not header_rows and rows:
        first = [cell for cell in _row_cells(rows[0]) if not model.is_hidden(cell)]
        if first and all(cell.tag == "th" for cell in first):
            header_rows.add(0)
    origin_columns = sorted({column for (_, column), (_, origin) in grid.items() if origin})
    if not rows or not origin_columns:
        empty = "(пустая таблица)"
        return f"{head_line} {empty}".strip() if head_line else empty

    def row_values(index: int) -> list[str]:
        return [grid.get((index, column), ("", False))[0] for column in origin_columns]

    body_rows = [index for index in range(len(rows)) if index not in header_rows]
    lines: list[str] = []
    if head_line:
        lines.append(head_line)
    if not refs:
        for index in sorted(header_rows) + body_rows:
            lines.append("\t".join(value.replace("\n", " ") for value in row_values(index)))
        return "\n".join(lines)
    if header_rows:
        headers = []
        for position in range(len(origin_columns)):
            seen: list[str] = []
            for index in sorted(header_rows):
                value = row_values(index)[position]
                if value and value not in seen:
                    seen.append(value)
            headers.append(" / ".join(seen))
    else:
        headers = [str(number) for number in range(1, len(origin_columns) + 1)]
    lines.append("| " + " | ".join(_cell_markdown(value) for value in headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for index in body_rows:
        lines.append("| " + " | ".join(_cell_markdown(value) for value in row_values(index)) + " |")
    return "\n".join(lines)


__all__ = [
    "KIND_WORDS",
    "Rendered",
    "field_inline",
    "is_layout_table",
    "render_flat",
    "render_markdown",
    "render_table",
    "render_text",
]
