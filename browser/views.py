"""Views of a page for the model: content, page, links, forms, tables, JSON and more.

``ViewPage`` wraps one ``PageSnapshot`` (HTML, feed, XML, JSON, text, binary
or empty) and builds its ``PageModel`` lazily; ``render_view`` renders one
view of it. The module also renders find results, CSS extraction results and
the change list after an action that did not navigate.
"""

from __future__ import annotations

import bisect
import html
import json
import logging
import re
import sys
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from browser.dom import Element, normalize_space, parse_html, serialize
from browser.engine import BinaryInfo, HeadersInfo, PageSnapshot, content_version
from browser.errors import (
    E_BINARY_VIEW,
    E_JSON_PATH,
    E_NOT_JSON,
    E_REGEX,
    E_SECTION,
    E_SECTION_AMBIGUOUS,
    E_VIEW_PARAM,
    BrowserError,
    BrowserNotFound,
)
from browser.feeds import (
    Feed,
    detect_feed,
    feed_to_html,
    parse_feed,
    parse_json_feed,
    pretty_xml,
)
from browser.forms import form_charset, form_fields, form_info, is_submit_button
from browser.metadata import inline_json_scripts, json_ld, microdata, page_metadata
from browser.page import (
    BUTTON_INPUT_TYPES,
    REF_COUNT_NAMES,
    REF_PREFIX_ORDER,
    PageModel,
    element_descriptor,
    option_label,
    option_value,
)
from browser.paging import compile_filter, human_bytes, parse_ref, plural
from browser.readable import (
    KIND_WORDS,
    Rendered,
    render_markdown,
    render_table,
    render_text,
)
from browser.redact import display_url, is_sensitive_name
from browser.semantic import (
    SEMANTIC_NAME_LIMIT,
    SEMANTIC_URL_LIMIT,
    SEMANTIC_VALUE_LIMIT,
    SemanticSnapshot,
    build_semantic_snapshot,
    diff_semantic_snapshots,
)

logger = logging.getLogger("logos.browser.views")

VIEWS = (
    "content",
    "page",
    "text",
    "html",
    "outline",
    "links",
    "interactive",
    "forms",
    "tables",
    "images",
    "meta",
    "structured",
    "feed",
    "headers",
    "json",
    "network",
    "console",
)
NAV_VIEWS = VIEWS + ("none",)
DOM_VIEWS = frozenset(
    {"content", "page", "text", "html", "links", "interactive", "forms", "tables", "images"}
)
FILTER_VIEWS = frozenset({"links", "images", "interactive", "network", "console"})
SECTION_VIEWS = frozenset({"content", "page", "text"})
JSON_PATH_VIEWS = frozenset({"json"})

SCOPE_SEPARATOR = "\n\n---\n\n"
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'`\\]+")
_URL_TRAILING = ".,;:!?)]}'\""
_REF_MARK = re.compile(r"\[([LBIFTM]\d+)")

_ROLE_WORDS = {
    "link": "ссылка",
    "button": "кнопка",
    "textbox": "поле",
    "searchbox": "поиск",
    "checkbox": "флажок",
    "radio": "переключатель",
    "combobox": "список",
    "listbox": "список",
    "slider": "ползунок",
    "spinbutton": "число",
    "menuitem": "пункт меню",
    "menuitemcheckbox": "пункт меню (флажок)",
    "menuitemradio": "пункт меню (переключатель)",
    "tab": "вкладка",
    "option": "вариант",
    "switch": "переключатель",
    "treeitem": "элемент дерева",
    "document": "фрейм",
    "img": "изображение",
}
_STATE_WORDS = {
    "disabled": "недоступно",
    "required": "обязательное",
    "readonly": "только чтение",
    "checked": "отмечено",
    "selected": "выбрано",
    "expanded": "раскрыто",
    "focused": "в фокусе",
}
_COUNT_WORDS = {
    "links": ("ссылка", "ссылки", "ссылок"),
    "buttons": ("кнопка", "кнопки", "кнопок"),
    "fields": ("поле", "поля", "полей"),
    "forms": ("форма", "формы", "форм"),
    "tables": ("таблица", "таблицы", "таблиц"),
    "media": ("изображение", "изображения", "изображений"),
}
_FEED_KINDS = {"rss": "RSS", "atom": "Atom", "rdf": "RDF", "json": "JSON Feed"}


def BINARY_DESCRIPTION(tab: str, info: BinaryInfo | None) -> str:  # noqa: N802 — catalog name
    """What a file opened in a tab is and how to download it."""
    mime = (info.mime if info is not None else "") or "application/octet-stream"
    size = human_bytes(info.length if info is not None else None)
    name = f", «{info.filename}»" if info is not None and info.filename else ""
    return (
        f"Это файл: {mime}, {size}{name}. В страницу не загружается. Скачать: "
        f"browser_download(tab='{tab}', path='…', overwrite=false)"
    )


def _linkified_html(title: str, text: str) -> str:
    """A synthetic <pre> page whose URLs become links (L refs)."""
    parts = [
        "<!DOCTYPE html><html><head><title>",
        html.escape(title),
        "</title></head><body><pre>",
    ]
    last = 0
    for match in _URL_IN_TEXT.finditer(text):
        url = match.group(0).rstrip(_URL_TRAILING)
        if len(url) <= len("https://"):
            continue
        end = match.start() + len(url)
        parts.append(html.escape(text[last : match.start()], quote=False))
        parts.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(url, quote=False)}</a>')
        last = end
    parts.append(html.escape(text[last:], quote=False))
    parts.append("</pre></body></html>")
    return "".join(parts)


class ViewPage:
    """One page as the views see it; the model and renderings are built lazily."""

    def __init__(self, snapshot: PageSnapshot) -> None:
        self.snapshot = snapshot
        self._kind: str | None = None
        self._model: PageModel | None = None
        self._model_built = False
        self._xml_doc: Any = None
        self._feed: Feed | None = None
        self._feed_done = False
        self._json_done = False
        self._json_value: Any = None
        self._json_error: str | None = None
        self._content_text: str | None = None
        self._rendered_page: Rendered | None = None
        self._rendered_content: Rendered | None = None
        self._rendered_text: Rendered | None = None

    # -- identity ---------------------------------------------------------

    @property
    def url(self) -> str:
        return self.snapshot.url

    @property
    def display_url(self) -> str:
        return display_url(self.snapshot.url)

    @property
    def version(self) -> str:
        snapshot = self.snapshot
        return snapshot.version or content_version(snapshot.html, snapshot.text, snapshot.url)

    @property
    def kind(self) -> str:
        if self._kind is None:
            raw = self.snapshot.kind
            if raw == "html":
                kind = "html"
            elif raw in ("xml", "json"):
                kind = "feed" if self.feed is not None else raw
            elif raw in ("text", "binary", "empty"):
                kind = raw
            else:
                kind = "text" if self.snapshot.text else "empty"
            self._kind = kind
        return self._kind

    @property
    def title(self) -> str:
        kind = self.kind
        if kind == "html" and self.model is not None:
            return self.model.title or self.snapshot.title_hint or ""
        if kind == "feed" and self.feed is not None and self.feed.title:
            return self.feed.title
        return self.snapshot.title_hint or ""

    # -- parsed forms -----------------------------------------------------

    def _xml_document(self) -> Any:
        if self._xml_doc is None:
            self._xml_doc = parse_html(self.snapshot.text or "", xml_mode=True)
        return self._xml_doc

    @property
    def feed(self) -> Feed | None:
        if not self._feed_done:
            self._feed_done = True
            raw = self.snapshot.kind
            if raw == "xml":
                document = self._xml_document()
                if detect_feed(document):
                    self._feed = parse_feed(document, self.snapshot.url)
            elif raw == "json":
                self._feed = parse_json_feed(self.json_value, self.snapshot.url)
        return self._feed

    def _load_json(self) -> None:
        if self._json_done:
            return
        self._json_done = True
        text = self.snapshot.text
        if text is None:
            self._json_error = "тела нет"
            return
        try:
            self._json_value = json.loads(text.lstrip("﻿"))
        except ValueError as exc:
            self._json_error = str(exc)

    @property
    def json_value(self) -> Any:
        self._load_json()
        return self._json_value

    @property
    def json_error(self) -> str | None:
        self._load_json()
        return self._json_error

    def content_text(self) -> str:
        """Pretty JSON, pretty XML, raw text or HTML source, by kind."""
        if self._content_text is None:
            raw = self.snapshot.kind
            if raw == "json":
                if self.json_error is None:
                    text = json.dumps(self.json_value, indent=2, ensure_ascii=False)
                else:
                    text = self.snapshot.text or ""
            elif raw == "xml":
                text = pretty_xml(self._xml_document())
            elif raw == "html":
                text = self.snapshot.html or ""
            elif raw in ("binary", "empty"):
                text = ""
            else:
                text = self.snapshot.text or ""
            self._content_text = text
        return self._content_text

    @property
    def model(self) -> PageModel | None:
        if not self._model_built:
            self._model_built = True
            snapshot = self.snapshot
            kind = self.kind
            if kind == "binary":
                self._model = None
            else:
                if kind == "html":
                    document = parse_html(snapshot.html or "")
                elif kind == "feed":
                    assert self._feed is not None
                    document = parse_html(feed_to_html(self._feed))
                elif kind == "empty":
                    document = parse_html("")
                else:
                    document = parse_html(
                        _linkified_html(snapshot.title_hint or "", self.content_text())
                    )
                self._model = PageModel(
                    document,
                    url=snapshot.url,
                    engine=snapshot.engine,
                    stamped=snapshot.stamped and kind == "html",
                    state=snapshot.field_state,
                    charset=snapshot.charset or "utf-8",
                    focused_ref=snapshot.focused_ref,
                )
        return self._model

    def ref_counts(self) -> dict[str, int]:
        model = self.model
        if model is None:
            return {name: 0 for name in REF_COUNT_NAMES.values()}
        return model.ref_counts()

    # -- renderings -------------------------------------------------------

    def rendered_page(self) -> Rendered:
        """The whole page as Markdown (HTML and feeds) or the content text."""
        if self._rendered_page is None:
            model = self.model
            if self.kind in ("html", "feed") and model is not None:
                self._rendered_page = render_markdown(model, model.body)
            else:
                self._rendered_page = Rendered(self.content_text(), [])
        return self._rendered_page

    def rendered_content(
        self, *, inline_urls: bool = False, include_hidden: bool = False
    ) -> Rendered:
        """The main content as Markdown (HTML), the feed (feeds) or the content text."""
        default = not inline_urls and not include_hidden
        if default and self._rendered_content is not None:
            return self._rendered_content
        model = self.model
        if self.kind == "feed" and model is not None:
            result = render_markdown(
                model, model.body, inline_urls=inline_urls, include_hidden=include_hidden
            )
        elif self.kind == "html" and model is not None:
            result = self._main_content(model, inline_urls, include_hidden)
        else:
            result = Rendered(self.content_text(), [])
        if default:
            self._rendered_content = result
        return result

    def _main_content(self, model: PageModel, inline_urls: bool, include_hidden: bool) -> Rendered:
        main = model.main
        rendered = render_markdown(
            model,
            main,
            inline_urls=inline_urls,
            include_hidden=include_hidden,
            skip_landmarks=True,
        )
        if main is model.body:
            return rendered
        if any(
            element.tag == "h1" and not model.is_hidden(element) for element in main.iter()
        ):
            return rendered
        inside = {element.index for element in main.iter()}
        heading = next(
            (
                element
                for element in model.document.elements
                if element.tag == "h1"
                and element.index not in inside
                and not model.is_hidden(element)
                and model.accessible_name(element)
            ),
            None,
        )
        if heading is None:
            return rendered
        title = model.accessible_name(heading)
        prefix = f"# {title}\n\n"
        headings = [(0, model.heading_number(heading) or "", 1, title)]
        headings += [
            (offset + len(prefix), number, level, text)
            for offset, number, level, text in rendered.headings
        ]
        return Rendered(prefix + rendered.text, headings)

    def content_notes(self) -> list[str]:
        """Notes for the content view: refs left outside the main text."""
        model = self.model
        if self.kind != "html" or model is None:
            return []
        counts = model.outside_main_counts(include_landmarks=True)
        parts = [
            f"{counts[name]} {plural(counts[name], *_COUNT_WORDS[name])}"
            for name in ("links", "buttons", "fields", "forms", "tables", "media")
            if counts.get(name)
        ]
        if not parts:
            return []
        return [
            "Вне основного текста: " + ", ".join(parts) + " — view=page, links или forms"
        ]

    def rendered_text(self) -> Rendered:
        if self._rendered_text is None:
            model = self.model
            if self.kind in ("html", "feed") and model is not None:
                self._rendered_text = render_text(model, model.body)
            else:
                self._rendered_text = Rendered(self.content_text(), [])
        return self._rendered_text


# ---------------------------------------------------------------------------
# Parameter checks and scopes


def _check_params(
    view: str,
    scope: Sequence[Element] | None,
    section: str | None,
    filter_text: str | None,
    json_path: str | None,
) -> None:
    if view not in VIEWS:
        raise BrowserError(f"Неизвестный вид view='{view}'. Доступны: {', '.join(VIEWS)}")
    if scope is not None and view not in DOM_VIEWS:
        raise BrowserError(E_VIEW_PARAM.format(param="selector/ref", view=view))
    if section is not None and view not in SECTION_VIEWS:
        raise BrowserError(E_VIEW_PARAM.format(param="section", view=view))
    if filter_text is not None and view not in FILTER_VIEWS:
        raise BrowserError(E_VIEW_PARAM.format(param="filter", view=view))
    if json_path is not None and view not in JSON_PATH_VIEWS:
        raise BrowserError(E_VIEW_PARAM.format(param="json_path", view=view))


def _scope_indices(scope: Sequence[Element] | None) -> set[int] | None:
    if scope is None:
        return None
    indices: set[int] = set()
    for root in scope:
        indices.update(element.index for element in root.iter())
    return indices


def _join_rendered(parts: list[Rendered]) -> Rendered:
    text_parts: list[str] = []
    headings: list[tuple[int, str, int, str]] = []
    offset = 0
    for position, part in enumerate(parts):
        if position:
            text_parts.append(SCOPE_SEPARATOR)
            offset += len(SCOPE_SEPARATOR)
        headings.extend((start + offset, number, level, title) for start, number, level, title in part.headings)
        text_parts.append(part.text)
        offset += len(part.text)
    return Rendered("".join(text_parts), headings)


def _matcher(match: str | Callable[[str], bool] | None) -> Callable[[str], bool]:
    if callable(match):
        return match
    return compile_filter(match)


# ---------------------------------------------------------------------------
# Section and outline


def render_section(page_render: Rendered, section: str) -> str:
    """The text of one section: by number (2.1), then by heading title."""
    target = (section or "").strip()
    headings = page_render.headings
    found = [heading for heading in headings if heading[1] and heading[1] == target]
    if not found:
        folded = target.casefold()
        found = [heading for heading in headings if heading[3].casefold() == folded]
        if not found and folded:
            found = [heading for heading in headings if folded in heading[3].casefold()]
    if not found:
        raise BrowserNotFound(E_SECTION.format(section=section))
    if len(found) > 1:
        listing = ", ".join(f"{number or '•'} «{title}»" for _, number, _, title in found)
        raise BrowserError(E_SECTION_AMBIGUOUS.format(section=section, list=listing))
    offset, _, level, _ = found[0]
    end = len(page_render.text)
    for other_offset, _, other_level, _ in headings:
        if other_offset > offset and other_level <= level:
            end = other_offset
            break
    return page_render.text[offset:end].rstrip()


def render_outline(page_render: Rendered) -> str:
    """"{indent}{number} {title} — {size} симв." for every heading."""
    headings = page_render.headings
    if not headings:
        return "Заголовков на странице нет."
    total = len(page_render.text)
    lines = []
    for position, (offset, number, level, title) in enumerate(headings):
        end = total
        for other_offset, _, other_level, _ in headings[position + 1 :]:
            if other_level <= level:
                end = other_offset
                break
        depth = number.count(".") + 1 if number else level
        lines.append(f"{'  ' * (depth - 1)}{number or '•'} {title} — {end - offset} симв.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Element lists


def _state_suffix(states: Iterable[str]) -> str:
    words = [_STATE_WORDS[state] for state in states if state in _STATE_WORDS]
    return f" ({', '.join(words)})" if words else ""


def _hidden_note(count: int, what: tuple[str, str, str]) -> list[str]:
    """"Скрытых ссылок не показано: N — include_hidden=true" (genitive plural)."""
    if not count:
        return []
    return [f"Скрытых {what[2]} не показано: {count} — include_hidden=true"]


def render_links(
    model: PageModel,
    *,
    scope: set[int] | None = None,
    match: str | Callable[[str], bool] | None = None,
    include_hidden: bool = False,
) -> tuple[str, list[str]]:
    """"[L5] text → url" with (новая вкладка), (nofollow), (скачивание) flags."""
    accept = _matcher(match)
    lines: list[str] = []
    skipped = 0
    for ref in model.refs_of_kind("L"):
        element = model.refs[ref]
        if scope is not None and element.index not in scope:
            continue
        hidden = model.is_hidden(element)
        url = display_url(model.href(element) or "")
        name = model.accessible_name(element)
        if element.tag in ("iframe", "frame"):
            line = f"[{ref}] фрейм" + (f" «{name}»" if name else "") + f" → {url}"
        else:
            line = f"[{ref}] {name or 'ссылка'} → {url}"
        if (element.attrs.get("target") or "").strip().lower() == "_blank":
            line += " (новая вкладка)"
        if "nofollow" in (element.attrs.get("rel") or "").lower().split():
            line += " (nofollow)"
        if "download" in element.attrs:
            line += " (скачивание)"
        if hidden:
            line += " (скрыта)"
        if not accept(line):
            continue
        if hidden and not include_hidden:
            skipped += 1
            continue
        lines.append(line)
    notes = _hidden_note(skipped, ("ссылки", "ссылки", "ссылок"))
    if not lines:
        return ("Ссылок нет" if match is None else "Ссылок по фильтру нет"), notes
    return "\n".join(lines), notes


def _value_text(model: PageModel, element: Element) -> str | None:
    value = model.display_value(element)
    if value is None:
        return None
    kind = model.field_kind(element)
    masked = value.startswith(("(заполнено", "(пусто)", "(скрыто"))
    if kind in ("select", "select-multiple", "file") or masked:
        return value
    return json.dumps(value, ensure_ascii=False)


def _element_word(model: PageModel, element: Element, prefix: str) -> str:
    if prefix == "I":
        kind = model.field_kind(element)
        if kind != "custom":
            return KIND_WORDS.get(kind, "поле")
    role = model.role(element) or ""
    if prefix == "L" and element.tag in ("iframe", "frame"):
        return "фрейм"
    if role in _ROLE_WORDS:
        return _ROLE_WORDS[role]
    return {"L": "ссылка", "B": "кнопка", "I": "поле"}.get(prefix, "элемент")


def render_interactive(
    model: PageModel,
    *,
    scope: set[int] | None = None,
    match: str | Callable[[str], bool] | None = None,
    include_hidden: bool = False,
) -> tuple[str, list[str]]:
    """Links, buttons and fields: "[ref] role «name» (name=…) = value (states)"."""
    accept = _matcher(match)
    lines: list[str] = []
    skipped = 0
    for ref, element in model.refs.items():
        prefix = ref[0]
        if prefix not in "LBI":
            continue
        if scope is not None and element.index not in scope:
            continue
        name = model.accessible_name(element)
        parts = [f"[{ref}]", _element_word(model, element, prefix)]
        if name:
            parts.append(f"«{name}»")
        field_name = element.attrs.get("name")
        if field_name and field_name != name and prefix != "L":
            parts.append(f"(name={field_name})")
        if prefix == "I":
            value = _value_text(model, element)
            if value:
                parts.append(f"= {value}")
        states = model.element_states(element)
        line = " ".join(parts) + _state_suffix(state for state in states if state != "hidden")
        hidden = "hidden" in states
        if hidden:
            line += " (скрыт)"
        if not accept(line):
            continue
        if hidden and not include_hidden:
            skipped += 1
            continue
        lines.append(line)
    notes = _hidden_note(skipped, ("элемента", "элемента", "элементов"))
    if not lines:
        return "Ссылок, кнопок и полей нет", notes
    return "\n".join(lines), notes


def _field_line(model: PageModel, element: Element) -> list[str]:
    """Lines of one form control for the forms view."""
    attrs = element.attrs
    ref = model.ref_of(element)
    kind = model.field_kind(element) if element.tag == "input" else element.tag
    name = attrs.get("name")
    hidden_mark = " (скрыто)" if model.is_hidden(element) and kind != "hidden" else ""
    if element.tag == "input" and kind == "hidden":
        value = model.field_value(element)
        if is_sensitive_name(name):
            shown = f"(скрыто, {len(value)} симв.)"
        else:
            shown = f"«{value}»"
        return [f"  скрытое {name or '(без имени)'} = {shown}"]
    label = model.accessible_name(element)
    states = model.element_states(element)
    if element.tag == "button" or (element.tag == "input" and kind in BUTTON_INPUT_TYPES):
        word = "отправка" if is_submit_button(model, element) else "кнопка"
        line = f"  [{ref or '—'}] {word} «{label}»" + (f" name={name}" if name else "")
        if "disabled" in states:
            line += " [недоступно]"
        return [line + hidden_mark]
    word = KIND_WORDS.get(model.field_kind(element), "поле")
    parts = [f"  [{ref or '—'}]", word]
    if name:
        parts.append(f"name={name}")
    if label and label != name:
        parts.append(f"«{label}»")
    if model.field_kind(element) in ("checkbox", "radio"):
        parts.append(f"= {json.dumps(attrs.get('value', 'on'), ensure_ascii=False)}")
        parts.append("отмечен" if model.field_checked(element) else "не отмечен")
    else:
        value = _value_text(model, element)
        if value:
            parts.append(f"= {value}")
    if "required" in states:
        parts.append("[обязательное]")
    if "disabled" in states:
        parts.append("[недоступно]")
    lines = [" ".join(parts) + hidden_mark]
    if element.tag == "select":
        chosen = {option.index for option in model.selected_options(element)}
        variants = []
        for option in model.options(element):
            label_text, value_text = option_label(option), option_value(option)
            item = label_text if value_text == label_text else f"{label_text} = {value_text}"
            if option.index in chosen:
                item += " (выбран)"
            variants.append(item)
        lines.append("    варианты: " + (", ".join(variants) if variants else "(нет)"))
    return lines


def render_forms(model: PageModel, *, scope: set[int] | None = None) -> str:
    """Every form with method, action, encoding, fields, options and hidden values."""
    blocks: list[str] = []
    for ref in model.refs_of_kind("F"):
        form = model.refs[ref]
        if scope is not None and form.index not in scope:
            continue
        info = form_info(model, form)
        charset = form_charset(info.accept_charset, model.charset)
        head = (
            f"[{ref}] форма {info.method.upper()} {display_url(info.action)} "
            f"({info.enctype}, {charset})"
        )
        if info.target == "_blank":
            head += " (новая вкладка)"
        if info.has_onsubmit:
            head += " (обрабатывается JavaScript)"
        if model.is_hidden(form):
            head += " (скрыта)"
        lines = [head]
        for element in form_fields(model, form):
            lines.extend(_field_line(model, element))
        blocks.append("\n".join(lines))
    loose: list[str] = []
    for ref, element in model.refs.items():
        if ref[0] != "I" or model.form_owner(element) is not None:
            continue
        if scope is not None and element.index not in scope:
            continue
        loose.extend(_field_line(model, element))
    if loose:
        blocks.append("Поля вне форм:\n" + "\n".join(loose))
    return "\n\n".join(blocks) if blocks else "Форм и полей нет"


def render_tables(
    model: PageModel, *, scope: set[int] | None = None, include_hidden: bool = False
) -> tuple[str, list[str]]:
    blocks: list[str] = []
    skipped = 0
    for ref in model.refs_of_kind("T"):
        table = model.refs[ref]
        if scope is not None and table.index not in scope:
            continue
        if model.is_hidden(table) and not include_hidden:
            skipped += 1
            continue
        blocks.append(render_table(model, table))
    notes = _hidden_note(skipped, ("таблицы", "таблицы", "таблиц"))
    return ("\n\n".join(blocks) if blocks else "Таблиц нет"), notes


def render_images(
    model: PageModel,
    *,
    scope: set[int] | None = None,
    match: str | Callable[[str], bool] | None = None,
    include_hidden: bool = False,
) -> tuple[str, list[str]]:
    """"[M1] «alt» url (WxH) подпись: … в ссылке L5"."""
    accept = _matcher(match)
    lines: list[str] = []
    skipped = 0
    for ref in model.refs_of_kind("M"):
        element = model.refs[ref]
        if scope is not None and element.index not in scope:
            continue
        hidden = model.is_hidden(element)
        name = model.accessible_name(element)
        url = display_url(model.media_url(element) or "")
        parts = [f"[{ref}]"]
        if element.tag in ("video", "audio"):
            parts.append("видео" if element.tag == "video" else "аудио")
        parts.append(f"«{name}»" if name else "(без описания)")
        if url:
            parts.append(url)
        width = (element.attrs.get("width") or "").strip()
        height = (element.attrs.get("height") or "").strip()
        if width and height:
            parts.append(f"({width}×{height})")
        poster = model.media_poster(element) if element.tag == "video" else None
        if poster:
            parts.append(f"постер: {display_url(poster)}")
        figure = element.closest(lambda node: node.tag == "figure")
        if figure is not None:
            caption = next(
                (node for node in figure.descendants() if node.tag == "figcaption"), None
            )
            if caption is not None:
                text = normalize_space(caption.text())
                if text:
                    parts.append(f"подпись: {text}")
        anchor = element.parent.closest(
            lambda node: node.tag == "a" and "href" in node.attrs
        ) if element.parent is not None else None
        if anchor is not None and model.ref_of(anchor):
            parts.append(f"в ссылке {model.ref_of(anchor)}")
        if hidden:
            parts.append("(скрыто)")
        line = " ".join(parts)
        if not accept(line):
            continue
        if hidden and not include_hidden:
            skipped += 1
            continue
        lines.append(line)
    notes = _hidden_note(skipped, ("изображения", "изображения", "изображений"))
    if not lines:
        return "Изображений нет", notes
    return "\n".join(lines), notes


# ---------------------------------------------------------------------------
# Metadata views


def _meta_value(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return str(value)


def render_meta(meta: dict[str, Any]) -> str:
    """Key lines of page_metadata: title, description, og:*, canonical, feeds…"""
    labels = (
        ("title", "Заголовок"),
        ("lang", "Язык"),
        ("dir", "Направление текста"),
        ("description", "Описание"),
        ("keywords", "Ключевые слова"),
        ("author", "Автор"),
        ("published", "Опубликовано"),
        ("modified", "Изменено"),
        ("robots", "Robots"),
        ("viewport", "Viewport"),
        ("generator", "Генератор"),
        ("referrer_policy", "Referrer-Policy"),
        ("charset_meta", "Кодировка (meta)"),
        ("canonical", "Каноническая ссылка"),
        ("base_href", "Базовый адрес (base)"),
        ("manifest", "Манифест"),
        ("amphtml", "AMP-версия"),
        ("prev", "Предыдущая страница"),
        ("next", "Следующая страница"),
    )
    lines: list[str] = []
    for key, label in labels:
        value = meta.get(key)
        if value:
            lines.append(f"{label}: {display_url(value) if key in ('canonical', 'base_href', 'manifest', 'amphtml', 'prev', 'next') else value}")
    for bucket in ("og", "twitter", "article"):
        for key, value in (meta.get(bucket) or {}).items():
            lines.append(f"{bucket}:{key}: {_meta_value(value)}")
    refresh = meta.get("refresh")
    if refresh:
        target = refresh.get("url")
        lines.append(
            f"Автообновление: через {refresh.get('delay')} с"
            + (f" → {display_url(target)}" if target else " (та же страница)")
        )
    alternates = meta.get("alternates") or []
    if alternates:
        lines.append("Альтернативные версии:")
        for item in alternates:
            details = " ".join(
                part
                for part in (item.get("hreflang"), item.get("media"), item.get("type"))
                if part
            )
            title = f" «{item['title']}»" if item.get("title") else ""
            lines.append(f"  {details or 'версия'}{title} → {display_url(item.get('href'))}")
    feeds = meta.get("feeds") or []
    if feeds:
        lines.append("Ленты:")
        for item in feeds:
            title = f" «{item['title']}»" if item.get("title") else ""
            lines.append(f"  лента {item.get('type')}{title}: {display_url(item.get('href'))}")
    icons = meta.get("icons") or []
    if icons:
        lines.append("Иконки:")
        for item in icons:
            sizes = f" {item['sizes']}" if item.get("sizes") else ""
            lines.append(f"  {item.get('rel')}{sizes} → {display_url(item.get('href'))}")
    return "\n".join(lines) if lines else "Метаданных нет"


def _ld_types(data: Any) -> list[str]:
    types: list[str] = []
    stack = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(reversed(item))
        elif isinstance(item, dict):
            value = item.get("@type")
            if isinstance(value, str):
                types.append(value)
            elif isinstance(value, list):
                types.extend(str(entry) for entry in value)
            graph = item.get("@graph")
            if isinstance(graph, list):
                stack.extend(reversed(graph))
    unique: list[str] = []
    for value in types:
        if value not in unique:
            unique.append(value)
    return unique


def render_structured(model: PageModel) -> str:
    """JSON-LD blocks, microdata items and inline JSON data scripts."""
    document = model.document
    sections: list[str] = []
    for block in json_ld(document):
        index = block["index"]
        if block["error"]:
            sections.append(f"## JSON-LD {index}: ошибка разбора — {block['error']}")
            continue
        types = _ld_types(block["data"])
        title = f"## JSON-LD {index}" + (f" (@type {', '.join(types)})" if types else "")
        sections.append(title + "\n" + json.dumps(block["data"], indent=2, ensure_ascii=False))
    items = microdata(document, model.base_url)
    if items:
        sections.append("## Микроданные\n" + json.dumps(items, indent=2, ensure_ascii=False))
    scripts = inline_json_scripts(document)
    if scripts:
        lines = ["## JSON в скриптах"]
        for script in scripts:
            ident = script["id"]
            selector = f"script#{ident}" if ident else f"script[type='{script['type']}']"
            lines.append(
                f"{ident or '(без id)'} ({script['type']}): {script['chars']} симв. — "
                f"browser_extract(selector=\"{selector}\", output='html')"
            )
        sections.append("\n".join(lines))
    return "\n\n".join(sections) if sections else "Структурированных данных нет"


def _enclosure_line(enclosure: tuple[str, str | None, int | None]) -> str:
    url, kind, length = enclosure
    details = ", ".join(
        part for part in (kind, human_bytes(length) if length is not None else None) if part
    )
    return f"{display_url(url)}" + (f" ({details})" if details else "")


def render_feed(feed: Feed, model: PageModel | None = None) -> str:
    """Feed items with numbers, refs, links, dates, authors and summaries."""
    item_refs: list[str] = model.refs_of_kind("L") if model is not None else []
    head = f"Лента {_FEED_KINDS.get(feed.kind, feed.kind)} «{feed.title or 'без названия'}»"
    count = len(feed.items)
    head += f" — {count} {plural(count, 'запись', 'записи', 'записей')}"
    if feed.link:
        head += f", сайт: {display_url(feed.link)}"
    blocks = [head]
    if feed.description:
        blocks.append(feed.description)
    position = 0
    for number, item in enumerate(feed.items, 1):
        ref = None
        if item.link:
            if position < len(item_refs):
                ref = item_refs[position]
            position += 1
        lines = [f"{number}. {item.title or '(без заголовка)'}" + (f" [{ref}]" if ref else "")]
        if item.link:
            lines.append(f"   {display_url(item.link)}")
        facts = [part for part in (item.published or item.updated, item.author) if part]
        if facts:
            lines.append("   " + " · ".join(facts))
        if item.summary:
            lines.append("   " + item.summary)
        if item.categories:
            lines.append("   Категории: " + ", ".join(item.categories))
        for enclosure in item.enclosures:
            lines.append("   Вложение: " + _enclosure_line(enclosure))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _http_version(value: str | None) -> str:
    if not value:
        return "HTTP"
    text = str(value).strip()
    lowered = text.lower()
    if lowered.startswith("http/"):
        return "HTTP/" + text[5:]
    if lowered in ("h2", "h3"):
        return "HTTP/" + lowered[1]
    if lowered in ("10", "11", "20"):
        return f"HTTP/{lowered[0]}.{lowered[1]}"
    return f"HTTP/{text}"


def render_headers(info: HeadersInfo | None) -> str:
    """Request line and headers, status line and headers (masked), redirects, sizes."""
    if info is None:
        return "Сведений о запросе и ответе нет"
    lines = [f"{info.method} {display_url(info.url)}"]
    lines.extend(f"{name}: {value}" for name, value in info.request_headers)
    lines.append("")
    if info.status is not None:
        lines.append(f"{_http_version(info.http_version)} {info.status} {info.reason}".rstrip())
    else:
        lines.append("Ответа нет")
    lines.extend(f"{name}: {value}" for name, value in info.response_headers)
    if info.redirects:
        lines.append("")
        lines.append("Перенаправления:")
        for hop in info.redirects:
            lines.append(f"  {hop.status} → {display_url(hop.url)} ({hop.method})")
    facts: list[str] = []
    if info.mime:
        facts.append(f"Тип: {info.mime}")
    if info.charset:
        source = f" ({info.charset_source})" if info.charset_source else ""
        facts.append(f"Кодировка: {info.charset}{source}")
    if info.bytes is not None:
        facts.append(f"Размер: {human_bytes(info.bytes)} ({info.bytes} байт)")
    if info.elapsed_ms is not None:
        facts.append(f"Время: {info.elapsed_ms} мс")
    if info.remote_address:
        facts.append(f"Адрес сервера: {info.remote_address}")
    if facts:
        lines.append("")
        lines.extend(facts)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON


def _parse_json_path(path: str) -> list[tuple[str, Any]]:
    """Segments ("key", name) / ("index", n) / ("wild", None) of a dotted path."""
    text = path.strip()
    position = 0
    if text.startswith("$"):
        position = 1
    segments: list[tuple[str, Any]] = []
    length = len(text)
    first = True
    while position < length:
        char = text[position]
        if char == ".":
            position += 1
            if position < length and text[position] == "*":
                segments.append(("wild", None))
                position += 1
                first = False
                continue
            start = position
            while position < length and text[position] not in ".[":
                position += 1
            name = text[start:position]
            if not name:
                raise BrowserError(
                    E_JSON_PATH.format(path=path, reason=f"пустое имя ключа (символ {start + 1})")
                )
            segments.append(("key", name))
        elif char == "[":
            close = position + 1
            if close < length and text[close] in "'\"":
                quote = text[close]
                buffer: list[str] = []
                cursor = close + 1
                while cursor < length and text[cursor] != quote:
                    if text[cursor] == "\\" and cursor + 1 < length:
                        cursor += 1
                    buffer.append(text[cursor])
                    cursor += 1
                if cursor >= length or text[cursor + 1 : cursor + 2] != "]":
                    raise BrowserError(
                        E_JSON_PATH.format(path=path, reason="не закрыта скобка ['…']")
                    )
                segments.append(("key", "".join(buffer)))
                position = cursor + 2
            else:
                end = text.find("]", position)
                if end < 0:
                    raise BrowserError(E_JSON_PATH.format(path=path, reason="не закрыта скобка «[»"))
                inner = text[position + 1 : end].strip()
                if inner == "*":
                    segments.append(("wild", None))
                elif re.fullmatch(r"-?\d+", inner):
                    segments.append(("index", int(inner)))
                else:
                    raise BrowserError(
                        E_JSON_PATH.format(
                            path=path, reason=f"в [] нужен номер, * или 'ключ' (получено «{inner}»)"
                        )
                    )
                position = end + 1
        elif first:
            start = position
            while position < length and text[position] not in ".[":
                position += 1
            segments.append(("key", text[start:position]))
        else:
            raise BrowserError(
                E_JSON_PATH.format(path=path, reason=f"неожиданный символ «{char}» (символ {position + 1})")
            )
        first = False
    return segments


def _keys_text(value: dict[str, Any]) -> str:
    keys = list(value.keys())
    return ", ".join(str(key) for key in keys) if keys else "(пусто)"


def _step(value: Any, kind: str, key: Any, path: str, level: str) -> Any:
    if kind == "key":
        if isinstance(value, dict):
            if key in value:
                return value[key]
            raise BrowserError(
                E_JSON_PATH.format(
                    path=path,
                    reason=f"нет ключа «{key}» на уровне «{level}»; ключи: {_keys_text(value)}",
                )
            )
        if isinstance(value, list) and re.fullmatch(r"-?\d+", str(key)):
            kind, key = "index", int(key)
        else:
            what = f"список из {len(value)} элементов" if isinstance(value, list) else type(value).__name__
            raise BrowserError(
                E_JSON_PATH.format(
                    path=path, reason=f"на уровне «{level}» не объект, а {what}; ключа «{key}» нет"
                )
            )
    if isinstance(value, list):
        if -len(value) <= key < len(value):
            return value[key]
        raise BrowserError(
            E_JSON_PATH.format(
                path=path,
                reason=f"индекс {key} вне списка из {len(value)} элементов на уровне «{level}»",
            )
        )
    if isinstance(value, dict) and str(key) in value:
        return value[str(key)]
    raise BrowserError(
        E_JSON_PATH.format(path=path, reason=f"на уровне «{level}» не список; индекса {key} нет")
    )


def _pointer(value: Any, path: str) -> Any:
    current = value
    level = "/"
    for raw in path.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not re.fullmatch(r"\d+", token):
                raise BrowserError(
                    E_JSON_PATH.format(path=path, reason=f"на уровне «{level}» список, нужен номер, а не «{token}»")
                )
            current = _step(current, "index", int(token), path, level)
        else:
            current = _step(current, "key", token, path, level)
        level = (level.rstrip("/") + "/" + raw) or "/"
    return current


def json_path_get(value: Any, path: str) -> Any:
    """Select from JSON: JSON Pointer (/a/0) or a dotted path (a.b[0], items[*].id)."""
    text = (path or "").strip()
    if not text:
        return value
    if text.startswith("/"):
        return _pointer(value, text)
    segments = _parse_json_path(text)
    current: list[Any] = [value]
    many = False
    level = "$"
    for kind, key in segments:
        if kind == "wild":
            expanded: list[Any] = []
            for item in current:
                if isinstance(item, list):
                    expanded.extend(item)
                elif isinstance(item, dict):
                    expanded.extend(item.values())
                elif not many:
                    raise BrowserError(
                        E_JSON_PATH.format(path=path, reason=f"на уровне «{level}» нечего перебирать (*)")
                    )
            current = expanded
            many = True
            level += "[*]"
            continue
        if many:
            selected = []
            for item in current:
                try:
                    selected.append(_step(item, kind, key, path, level))
                except BrowserError:
                    continue
            if current and not selected:
                current = [_step(current[0], kind, key, path, level)]
            current = selected
        else:
            current = [_step(current[0], kind, key, path, level)]
        level += f".{key}" if kind == "key" else f"[{key}]"
    return current if many else current[0]


def render_json(value: Any, json_path: str | None = None) -> str:
    selected = json_path_get(value, json_path) if json_path else value
    return json.dumps(selected, indent=2, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Network and console (chrome)


def _network_line(entry: dict[str, Any]) -> str:
    status = entry.get("status")
    size = entry.get("bytes")
    parts = [
        str(entry.get("id") or "N?"),
        str(entry.get("method") or "GET"),
        str(status) if status is not None else "—",
        str(entry.get("type") or "—"),
        str(entry.get("mime") or "—"),
        human_bytes(size) if size is not None else "—",
        display_url(entry.get("url") or ""),
    ]
    line = " ".join(parts)
    if entry.get("blocked"):
        line += " ЗАБЛОКИРОВАН (link-local)"
    if entry.get("failure"):
        line += f" ошибка: {entry['failure']}"
    return line


def render_network(
    entries: Sequence[dict[str, Any]] | None, match: str | Callable[[str], bool] | None = None
) -> str:
    """"N3 GET 200 xhr application/json 12 КБ https://…" per request."""
    accept = _matcher(match)
    lines = [line for line in (_network_line(entry) for entry in entries or ()) if accept(line)]
    if not lines:
        return "Запросов нет" if match is None else "Запросов по фильтру нет"
    return "\n".join(lines)


def render_network_entry(entry: dict[str, Any], body_text: str | None) -> str:
    lines = [
        f"{entry.get('id') or 'N?'} {entry.get('method') or 'GET'} {display_url(entry.get('url') or '')}",
        f"Статус: {entry.get('status') if entry.get('status') is not None else '—'}",
        f"Тип: {entry.get('type') or '—'}",
        f"MIME: {entry.get('mime') or '—'}",
        f"Размер: {human_bytes(entry.get('bytes'))}",
    ]
    if entry.get("blocked"):
        lines.append("Запрос заблокирован: адрес link-local")
    if entry.get("failure"):
        lines.append(f"Ошибка: {entry['failure']}")
    lines.append("")
    lines.append(body_text if body_text is not None else "(тело ответа недоступно)")
    return "\n".join(lines)


def render_console(
    entries: Sequence[dict[str, Any]] | None, match: str | Callable[[str], bool] | None = None
) -> str:
    """"[error] text (url:line)" per console message."""
    accept = _matcher(match)
    lines = []
    for entry in entries or ():
        where = ""
        if entry.get("url"):
            where = f" ({display_url(entry['url'])}"
            where += f":{entry['line']})" if entry.get("line") is not None else ")"
        line = f"[{entry.get('level') or 'log'}] {entry.get('text') or ''}{where}"
        if accept(line):
            lines.append(line)
    if not lines:
        return "Сообщений консоли нет" if match is None else "Сообщений по фильтру нет"
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# render_view


def _parse_notes(model: PageModel | None) -> list[str]:
    if model is None:
        return []
    return [f"HTML разобран не полностью: {warning}" for warning in model.document.parse_warnings]


def _scoped(
    model: PageModel,
    scope: Sequence[Element],
    render: Callable[[Element], Rendered],
) -> Rendered:
    return _join_rendered([render(root) for root in scope])


def render_view(
    page: ViewPage,
    view: str,
    *,
    scope: list[Element] | None = None,
    section: str | None = None,
    filter: str | None = None,  # noqa: A002 — the tool parameter's name
    inline_urls: bool = False,
    json_path: str | None = None,
    include_hidden: bool = False,
    network: list[dict[str, Any]] | None = None,
    console: list[dict[str, Any]] | None = None,
) -> tuple[str, list[str]]:
    """Render one view of the page; returns (text, notes)."""
    _check_params(view, scope, section, filter, json_path)
    snapshot = page.snapshot
    kind = page.kind
    notes: list[str] = []
    if kind == "binary":
        if view == "content":
            return BINARY_DESCRIPTION(snapshot.tab, snapshot.binary), notes
        if view == "headers":
            return render_headers(snapshot.headers), notes
        info = snapshot.binary
        raise BrowserError(
            E_BINARY_VIEW.format(
                tab=snapshot.tab,
                mime=(info.mime if info is not None else snapshot.mime) or "application/octet-stream",
                size=human_bytes(info.length if info is not None else snapshot.bytes),
            )
        )
    model = page.model
    assert model is not None
    scope_set = _scope_indices(scope)
    markup = kind in ("html", "feed")
    if view in ("content", "page", "text"):
        if scope is not None:
            if view == "text":
                rendered = _scoped(
                    model, scope, lambda root: render_text(model, root, include_hidden=include_hidden)
                )
            else:
                rendered = _scoped(
                    model,
                    scope,
                    lambda root: render_markdown(
                        model, root, inline_urls=inline_urls, include_hidden=include_hidden
                    ),
                )
        elif view == "content":
            rendered = page.rendered_content(inline_urls=inline_urls, include_hidden=include_hidden)
            notes.extend(page.content_notes())
        elif view == "page":
            if markup and (inline_urls or include_hidden):
                rendered = render_markdown(
                    model, model.body, inline_urls=inline_urls, include_hidden=include_hidden
                )
            else:
                rendered = page.rendered_page()
        elif markup and include_hidden:
            rendered = render_text(model, model.body, include_hidden=True)
        else:
            rendered = page.rendered_text()
        if markup:
            notes.extend(_parse_notes(model))
        elif kind == "json" and page.json_error:
            notes.append(f"JSON не разобран ({page.json_error}); показан исходный текст")
        if kind == "empty":
            notes.append("Документ пустой: содержимого нет")
        if section is not None:
            return render_section(rendered, section), notes
        return rendered.text, notes
    if view == "html":
        if scope is not None:
            return "\n\n".join(serialize(root) for root in scope), notes
        if kind == "html":
            if snapshot.stamped:
                return serialize(model.document), notes
            return snapshot.html or "", notes
        return snapshot.text or "", notes
    if view == "outline":
        return render_outline(page.rendered_page()), notes
    if view == "links":
        text, extra = render_links(model, scope=scope_set, match=filter, include_hidden=include_hidden)
        return text, notes + extra
    if view == "interactive":
        text, extra = render_interactive(
            model, scope=scope_set, match=filter, include_hidden=include_hidden
        )
        return text, notes + extra
    if view == "forms":
        return render_forms(model, scope=scope_set), notes
    if view == "tables":
        text, extra = render_tables(model, scope=scope_set, include_hidden=include_hidden)
        return text, notes + extra
    if view == "images":
        text, extra = render_images(model, scope=scope_set, match=filter, include_hidden=include_hidden)
        return text, notes + extra
    if view == "meta":
        if kind == "html":
            return render_meta(page_metadata(model.document, model.base_url)), notes
        facts = [f"Тип: {snapshot.content_type or snapshot.mime or kind}"]
        if page.title:
            facts.append(f"Заголовок: {page.title}")
        if kind == "feed" and page.feed is not None:
            if page.feed.link:
                facts.append(f"Сайт: {display_url(page.feed.link)}")
            if page.feed.description:
                facts.append(f"Описание: {page.feed.description}")
        return "\n".join(facts), notes
    if view == "structured":
        if kind == "html":
            return render_structured(model), notes
        return "Структурированных данных нет: это не HTML-страница", notes
    if view == "feed":
        if kind == "feed" and page.feed is not None:
            return render_feed(page.feed, model), notes
        if kind == "html":
            feeds = page_metadata(model.document, model.base_url).get("feeds") or []
            if not feeds:
                return "Это не лента, и ссылок на ленты на странице нет", notes
            lines = ["Это HTML-страница; ленты сайта:"]
            for item in feeds:
                title = f" «{item['title']}»" if item.get("title") else ""
                lines.append(f"лента {item.get('type')}{title}: {display_url(item.get('href'))}")
            return "\n".join(lines), notes
        return "Это не лента (RSS, Atom или JSON Feed)", notes
    if view == "headers":
        return render_headers(snapshot.headers), notes
    if view == "json":
        content_type = snapshot.content_type or snapshot.mime or "не указан"
        if snapshot.kind not in ("json", "text"):
            raise BrowserError(
                E_NOT_JSON.format(ct=content_type, reason=f"это документ {kind}")
            )
        if page.json_error is not None:
            raise BrowserError(E_NOT_JSON.format(ct=content_type, reason=page.json_error))
        return render_json(page.json_value, json_path), notes
    if view == "network":
        return render_network(network, filter), notes
    return render_console(console, filter), notes


# ---------------------------------------------------------------------------
# Find


@dataclass
class Match:
    offset: int
    number: str | None
    section: str | None
    ref: str | None
    context: str


def find_matches(
    text: str,
    query: str,
    *,
    regex: bool = False,
    case_sensitive: bool = False,
    context_chars: int | None = None,
    headings: Sequence[tuple[int, str, int, str]] = (),
) -> list[Match]:
    """Every non-empty match with its section, nearest ref and context."""
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query if regex else re.escape(query), flags)
    except re.error as exc:
        raise BrowserError(E_REGEX.format(q=query, reason=str(exc))) from exc
    ordered = sorted(headings, key=lambda heading: heading[0])
    offsets = [heading[0] for heading in ordered]
    found: list[Match] = []
    total = len(text)
    for match in pattern.finditer(text):
        start, end = match.span()
        if start == end:
            continue
        position = bisect.bisect_right(offsets, start) - 1
        number = section = None
        if position >= 0:
            number = ordered[position][1] or None
            section = ordered[position][3] or None
        line_start = text.rfind("\n", 0, start) + 1
        refs = _REF_MARK.findall(text, line_start, start)
        ref = refs[-1] if refs else None
        if context_chars is None:
            begin = text.rfind("\n\n", 0, start)
            begin = 0 if begin < 0 else begin + 2
            finish = text.find("\n\n", end)
            finish = total if finish < 0 else finish
            lead = tail = ""
        else:
            begin = max(0, start - context_chars)
            finish = min(total, end + context_chars)
            lead = "…" if begin > 0 else ""
            tail = "…" if finish < total else ""
        context = (
            lead + text[begin:start] + "**" + text[start:end] + "**" + text[end:finish] + tail
        )
        found.append(Match(start, number, section, ref, context))
    return found


def render_find(matches: Sequence[Match], *, query: str, view: str) -> str:
    count = len(matches)
    if not count:
        return f"Совпадений «{query}» не найдено (view={view})"
    head = (
        f"Найдено {count} {plural(count, 'совпадение', 'совпадения', 'совпадений')} "
        f"«{query}» (view={view}):"
    )
    items = []
    for number, match in enumerate(matches, 1):
        parts = []
        if match.number or match.section:
            where = f"§{match.number}" if match.number else "§"
            if match.section:
                where += f" «{match.section}»"
            parts.append(where)
        if match.ref:
            parts.append(f"[{match.ref}]")
        parts.append(f"смещение {match.offset}")
        context = "\n".join("   " + line for line in match.context.split("\n"))
        items.append(f"{number}. " + " · ".join(parts) + "\n" + context)
    return head + "\n\n" + "\n\n".join(items)


# ---------------------------------------------------------------------------
# Extract


def _attribute_lines(
    model: PageModel, element: Element, attributes: Sequence[str] | None
) -> list[str]:
    attrs = element.attrs
    password = element.tag == "input" and model.field_kind(element) == "password"

    def shown(name: str, value: str) -> str:
        if (password and name == "value") or is_sensitive_name(name):
            return f"(скрыто, {len(value)} симв.)"
        return value

    lines = []
    if attributes:
        for name in attributes:
            key = name.strip().lower()
            if key in attrs:
                lines.append(f"   {key} = {shown(key, attrs[key])}")
            else:
                lines.append(f"   {key}: (нет)")
    else:
        for key, value in attrs.items():
            if key.startswith("data-logos-"):
                continue
            lines.append(f"   {key} = {shown(key, value)}")
        if not lines:
            lines.append("   (атрибутов нет)")
    return lines


def render_extract(
    page: ViewPage,
    elements: Sequence[Element],
    *,
    selector: str,
    output: str = "text",
    attributes: Sequence[str] | None = None,
    include_hidden: bool = True,
) -> str:
    """"Найдено N элементов по «sel»:" and each element as text, Markdown, HTML or attributes."""
    model = page.model
    if model is None:
        return f"Найдено 0 элементов по «{selector}»: документ — файл"
    chosen = [element for element in elements if include_hidden or not model.is_hidden(element)]
    skipped = len(elements) - len(chosen)
    count = len(chosen)
    head = f"Найдено {count} {plural(count, 'элемент', 'элемента', 'элементов')} по «{selector}»:"
    if skipped:
        head += f" (скрытых не показано: {skipped} — include_hidden=true)"
    blocks: list[str] = []
    for number, element in enumerate(chosen, 1):
        ref = model.ref_of(element)
        title = f"{number}. " + (f"[{ref}] " if ref else "") + element_descriptor(element)
        if model.is_hidden(element):
            title += " (скрыт)"
        if output == "html":
            blocks.append(title + "\n" + serialize(element))
        elif output == "markdown":
            blocks.append(title + "\n" + render_markdown(model, element).text)
        elif output == "attributes":
            blocks.append(title + "\n" + "\n".join(_attribute_lines(model, element, attributes)))
        else:
            if model.is_field(element):
                text = model.display_value(element) or ""
            elif element.tag == "script":
                text = element.text(skip=())
            else:
                text = " ".join(render_text(model, element).text.split())
            blocks.append(f"{title} — {text}" if text else title)
    separator = "\n" if output == "text" else "\n\n"
    return head + ("\n" + separator.join(blocks) if blocks else "")


# ---------------------------------------------------------------------------
# Changes


def semantic_snapshot(page: ViewPage) -> SemanticSnapshot:
    """L/B/I nodes and the visible text size, for the change list after an action."""
    model = page.model
    nodes = model.snapshot_nodes() if model is not None else []
    text_chars = model.visible_text_length() if model is not None else 0
    return build_semantic_snapshot(
        {
            "url": page.display_url,
            "title": page.title,
            "version": page.version,
            "nodes": nodes,
            "facts": {"text_chars": text_chars},
        },
        max_nodes=max(1, len(nodes)),
        max_chars=sys.maxsize,
    )


def _ref_key(ref: str, order: str) -> tuple[int, int, str]:
    parsed = parse_ref(ref)
    if parsed is None:
        return (len(order), 0, ref)
    prefix, number = parsed
    position = order.find(prefix)
    return (position if position >= 0 else len(order), number, ref)


def _shortened(text: str, limit: int) -> bool:
    """Whether a semantic text was cut to ``limit`` characters; with no limit (0) nothing is cut."""
    return limit > 0 and len(text) >= limit and text.endswith("…")


def _node_line(sign: str, node: Any) -> tuple[str, bool]:
    word = _ROLE_WORDS.get(node.role, node.role)
    parts = [f"{sign} [{node.node_id}] {word}"]
    cut = False
    if node.name:
        parts.append(f"«{node.name}»")
        cut = cut or _shortened(node.name, SEMANTIC_NAME_LIMIT)
    if node.value:
        value = node.value
        shown = value if value.startswith(("(", "«")) else json.dumps(value, ensure_ascii=False)
        parts.append(f"= {shown}")
        cut = cut or _shortened(value, SEMANTIC_VALUE_LIMIT)
    states = [state for state in node.states if state != "hidden"]
    line = " ".join(parts) + _state_suffix(states)
    if "hidden" in node.states:
        line += " (скрыт)"
    return line, cut


def render_changes(
    before: SemanticSnapshot,
    after: SemanticSnapshot,
    *,
    ref_order: str | Sequence[str] | None = None,
) -> str:
    """What changed on the page: added, changed and removed elements, text size."""
    order = "".join(ref_order) if ref_order else REF_PREFIX_ORDER
    diff = diff_semantic_snapshots(before, after)
    lines: list[str] = []
    cut = False
    if before.url != after.url and after.url:
        lines.append(f"Адрес: {after.url}")
        cut = _shortened(after.url, SEMANTIC_URL_LIMIT)
    if before.title != after.title:
        lines.append(f"Заголовок: «{after.title or ''}»")
        title = after.title or ""
        cut = cut or _shortened(title, SEMANTIC_VALUE_LIMIT)
    for node in sorted(diff.added_nodes, key=lambda item: _ref_key(item.node_id, order)):
        line, shortened = _node_line("+", node)
        lines.append(line)
        cut = cut or shortened
    for node in sorted(diff.changed_nodes, key=lambda item: _ref_key(item.node_id, order)):
        line, shortened = _node_line("~", node)
        lines.append(line)
        cut = cut or shortened
    if diff.removed_node_ids:
        removed = sorted(diff.removed_node_ids, key=lambda ref: _ref_key(ref, order))
        lines.append("- удалены: " + ", ".join(removed))
    change = diff.changed_facts.get("text_chars")
    if isinstance(change, dict):
        old, new = change.get("before"), change.get("after")
        if isinstance(old, int) and isinstance(new, int):
            lines.append(f"Текст страницы: было {old} симв., стало {new} ({new - old:+d})")
    if not lines:
        return "Видимых изменений нет"
    if cut:
        named = (
            (SEMANTIC_NAME_LIMIT, f"названия длиннее {SEMANTIC_NAME_LIMIT} симв."),
            (SEMANTIC_VALUE_LIMIT, f"значения и заголовок длиннее {SEMANTIC_VALUE_LIMIT} симв."),
            (SEMANTIC_URL_LIMIT, f"адрес длиннее {SEMANTIC_URL_LIMIT} симв."),
        )
        caps = ", ".join(text for limit, text in named if limit > 0)
        lines.append(f"(в этом списке сокращены: {caps}; полностью — browser_read)")
    return "\n".join(lines)


__all__ = [
    "BINARY_DESCRIPTION",
    "DOM_VIEWS",
    "FILTER_VIEWS",
    "JSON_PATH_VIEWS",
    "Match",
    "NAV_VIEWS",
    "SECTION_VIEWS",
    "VIEWS",
    "ViewPage",
    "find_matches",
    "json_path_get",
    "render_changes",
    "render_console",
    "render_extract",
    "render_feed",
    "render_find",
    "render_forms",
    "render_headers",
    "render_images",
    "render_interactive",
    "render_json",
    "render_links",
    "render_meta",
    "render_network",
    "render_network_entry",
    "render_outline",
    "render_section",
    "render_structured",
    "render_tables",
    "render_view",
    "semantic_snapshot",
]
