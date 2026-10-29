"""Forms: field association and lookup, the WHATWG entry list and submissions.

``build_submission`` turns a form, its field state and the chosen submit
button into a ``FormSubmission`` the http engine encodes. Validation never
blocks a submission; ``validation_notes`` only reports what a browser would
have complained about.
"""

from __future__ import annotations

import codecs
import logging
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from browser import css
from browser.dom import Element, normalize_space
from browser.errors import (
    E_FORM_DIALOG,
    E_FORM_SCHEME,
    BrowserError,
    BrowserNotFound,
    field_not_found_message,
)
from browser.page import BUTTON_INPUT_TYPES, PageModel, option_label, option_value
from browser.paging import parse_ref

logger = logging.getLogger("logos.browser.forms")

FORM_URLENCODED = "application/x-www-form-urlencoded"
FORM_MULTIPART = "multipart/form-data"
FORM_TEXT = "text/plain"
_ENCTYPES = frozenset({FORM_URLENCODED, FORM_MULTIPART, FORM_TEXT})
_METHODS = frozenset({"get", "post", "dialog"})
_CONTROLS = frozenset({"input", "button", "select", "textarea"})
_SUBMIT_SCHEMES = frozenset({"http", "https", "file", "data"})
_NO_NEWLINES = frozenset({"text", "search", "tel", "password", "url", "email"})
_DIRNAME_TYPES = frozenset({"text", "search"})
_CSS_HINT = re.compile(r"[#.\[>: ]")
# WHATWG maps these labels to windows-1252.
_CHARSET_ALIASES = {"iso8859-1": "cp1252", "ascii": "cp1252", "latin-1": "cp1252"}


@dataclass
class FormInfo:
    element: Element
    ref: str | None
    action: str
    method: str  # get|post|dialog
    enctype: str
    accept_charset: str
    target: str | None
    novalidate: bool
    has_onsubmit: bool


@dataclass
class FormSubmission:
    method: str  # "GET" | "POST"
    url: str
    enctype: str
    charset: str
    fields: list[tuple[str, str]]
    # (field name, local path or None for an empty file input, content type or None)
    files: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    target: str | None = None
    form_ref: str | None = None
    submitter_ref: str | None = None
    field_names: list[str] = field(default_factory=list)


def _normalize_method(value: str | None) -> str:
    method = (value or "").strip().lower()
    return method if method in _METHODS else "get"


def _normalize_enctype(value: str | None) -> str:
    enctype = (value or "").strip().lower()
    return enctype if enctype in _ENCTYPES else FORM_URLENCODED


def _action_url(model: PageModel, value: str | None) -> str:
    if value is None or not value.strip():
        return model.url
    return model.resolve(value) or model.url


def _python_charset(label: str | None) -> str | None:
    if not label:
        return None
    try:
        name = codecs.lookup(label.strip()).name
    except (LookupError, ValueError):
        return None
    if name.startswith(("utf-16", "utf-32")):
        return "utf-8"
    return _CHARSET_ALIASES.get(name, name)


def form_charset(accept_charset: str | None, document_charset: str | None) -> str:
    """The first Python-known accept-charset label, else the document charset."""
    for label in re.split(r"[\s,]+", accept_charset or ""):
        name = _python_charset(label)
        if name:
            return name
    return _python_charset(document_charset) or "utf-8"


def form_of(model: PageModel, element: Element) -> Element | None:
    """The form owner: the form="id" attribute, else the ancestor form."""
    return model.form_owner(element)


def form_fields(model: PageModel, form: Element) -> list[Element]:
    """Controls of the form in tree order, including form="id" controls outside it."""
    return [
        element
        for element in model.document.elements
        if element.tag in _CONTROLS and model.form_owner(element) is form
    ]


def _is_button(model: PageModel, element: Element) -> bool:
    if element.tag == "button":
        return True
    return element.tag == "input" and model.field_kind(element) in BUTTON_INPUT_TYPES


def is_submit_button(model: PageModel, element: Element) -> bool:
    """A button that submits its form (button type submit or default, input submit/image)."""
    if element.tag == "button":
        kind = (element.attrs.get("type") or "").strip().lower()
        return kind not in ("button", "reset")
    return element.tag == "input" and model.field_kind(element) in ("submit", "image")


def submit_buttons(model: PageModel, form: Element) -> list[Element]:
    return [element for element in form_fields(model, form) if is_submit_button(model, element)]


def form_info(model: PageModel, form: Element) -> FormInfo:
    attrs = form.attrs
    target = attrs.get("target")
    return FormInfo(
        element=form,
        ref=model.ref_of(form),
        action=_action_url(model, attrs.get("action")),
        method=_normalize_method(attrs.get("method")),
        enctype=_normalize_enctype(attrs.get("enctype")),
        accept_charset=(attrs.get("accept-charset") or "").strip(),
        target=target.strip() if target and target.strip() else None,
        novalidate="novalidate" in attrs,
        has_onsubmit=bool((attrs.get("onsubmit") or "").strip()),
    )


def _field_candidates(model: PageModel, form: Element | None) -> list[Element]:
    """Controls (and other I-ref fields such as contenteditable) in scope."""
    if form is not None:
        controls = form_fields(model, form)
        inside = {element.index for element in form.iter()}
    else:
        controls = [element for element in model.document.elements if element.tag in _CONTROLS]
        inside = None
    seen = {element.index for element in controls}
    extra = []
    for ref in model.refs_of_kind("I"):
        element = model.refs[ref]
        if element.index in seen:
            continue
        if inside is None or element.index in inside:
            extra.append(element)
    combined = controls + extra
    combined.sort(key=lambda element: element.index)
    return combined


def _expand_group(model: PageModel, found: list[Element]) -> list[Element]:
    first = found[0]
    if first.tag != "input" or model.field_kind(first) not in ("checkbox", "radio"):
        return found
    name = first.attrs.get("name")
    if not name:
        return found
    kind = model.field_kind(first)
    owner = model.form_owner(first)
    return [
        element
        for element in model.document.elements
        if element.tag == "input"
        and model.field_kind(element) == kind
        and element.attrs.get("name") == name
        and model.form_owner(element) is owner
    ]


def _describe_field(model: PageModel, element: Element) -> str:
    name = (
        element.attrs.get("name")
        or model.accessible_name(element)
        or element.attrs.get("id")
        or model.field_kind(element)
    )
    ref = model.ref_of(element)
    if ref is None:
        ref = "скрытое" if model.field_kind(element) == "hidden" else "без метки"
    return f"{name} ({ref})"


def find_field(model: PageModel, form: Element | None, spec: str) -> list[Element]:
    """Fields matching ``spec``: ref, name, id, label, placeholder, aria-label or CSS.

    A checkbox or radio match returns its whole same-name group.
    """
    text = (spec or "").strip()
    parsed = parse_ref(text)
    if parsed is not None and parsed[0] in ("I", "B"):
        return _expand_group(model, [model.element(text)])
    candidates = _field_candidates(model, form)
    fields = [element for element in candidates if not _is_button(model, element)]
    folded = normalize_space(text).casefold()
    if text:
        for found in (
            [element for element in fields if element.attrs.get("name") == text],
            [element for element in fields if element.attrs.get("id") == text],
        ):
            if found:
                return _expand_group(model, found)
    if folded:
        labels = [(element, model.label_text(element).casefold()) for element in fields]
        for found in (
            [element for element, label in labels if label == folded],
            [element for element, label in labels if label and folded in label],
        ):
            if found:
                return _expand_group(model, found)
        for attribute in ("placeholder", "aria-label"):
            values = [
                (element, normalize_space(element.attrs.get(attribute)).casefold())
                for element in fields
            ]
            for found in (
                [element for element, value in values if value == folded],
                [element for element, value in values if value and folded in value],
            ):
                if found:
                    return _expand_group(model, found)
    if text and _CSS_HINT.search(text):
        try:
            selected = css.select(model.document, text)
        except css.SelectorError:
            selected = []
        if selected:
            allowed = {element.index for element in candidates}
            found = [
                element
                for element in selected
                if element.index in allowed or (form is None and model.is_field(element))
            ]
            if found:
                return _expand_group(model, found)
    listing = ", ".join(_describe_field(model, element) for element in fields) or "нет полей"
    form_ref = model.ref_label(form) if form is not None else None
    raise BrowserNotFound(field_not_found_message(text, form_ref, listing))


def _direction(element: Element) -> str:
    holder = element.closest(lambda node: "dir" in node.attrs)
    if holder is not None and (holder.attrs.get("dir") or "").strip().lower() == "rtl":
        return "rtl"
    return "ltr"


def _crlf(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")


def _skipped(element: Element) -> bool:
    if css.is_disabled(element):
        return True
    return element.closest(lambda node: node.tag == "datalist") is not None


def build_submission(
    model: PageModel, form: Element, submitter: Element | None = None
) -> FormSubmission:
    """The form's entry list and request parameters (WHATWG form submission)."""
    info = form_info(model, form)
    method, action, enctype, target = info.method, info.action, info.enctype, info.target
    if submitter is not None and is_submit_button(model, submitter):
        attrs = submitter.attrs
        if "formmethod" in attrs:
            method = _normalize_method(attrs.get("formmethod"))
        if "formaction" in attrs:
            action = _action_url(model, attrs.get("formaction"))
        if "formenctype" in attrs:
            enctype = _normalize_enctype(attrs.get("formenctype"))
        if "formtarget" in attrs:
            value = (attrs.get("formtarget") or "").strip()
            target = value or None
    if method == "dialog":
        raise BrowserError(E_FORM_DIALOG)
    try:
        scheme = urlsplit(action).scheme.lower()
    except ValueError:
        scheme = ""
    if scheme and scheme not in _SUBMIT_SCHEMES:
        raise BrowserError(E_FORM_SCHEME.format(scheme=scheme))
    charset = form_charset(info.accept_charset, model.charset)
    multipart = method == "post" and enctype == FORM_MULTIPART
    fields: list[tuple[str, str]] = []
    files: list[tuple[str, str | None, str | None]] = []
    names: list[str] = []

    def add(name: str, value: str) -> None:
        fields.append((name, value))
        if name not in names:
            names.append(name)

    for element in form_fields(model, form):
        if _skipped(element):
            continue
        tag = element.tag
        kind = model.field_kind(element) if tag == "input" else tag
        attrs = element.attrs
        if _is_button(model, element):
            if element is not submitter:
                continue
            name = attrs.get("name") or ""
            if kind == "image":
                prefix = f"{name}." if name else ""
                add(prefix + "x", "0")
                add(prefix + "y", "0")
            elif name:
                add(name, attrs.get("value", ""))
            continue
        name = attrs.get("name")
        if not name:
            continue
        if tag == "input":
            if kind in ("checkbox", "radio"):
                if model.field_checked(element):
                    add(name, attrs.get("value", "on"))
                continue
            if kind == "file":
                paths = model.state.files.get(element.index) or ()
                if multipart:
                    for path in paths or (None,):
                        files.append((name, path, None))
                    if name not in names:
                        names.append(name)
                else:
                    # Without multipart a file entry is sent as its file name.
                    for path in paths or (None,):
                        add(name, os.path.basename(path) if path else "")
                continue
            value = model.field_value(element)
            if kind == "hidden" and name.casefold() == "_charset_":
                value = charset
            elif kind in _NO_NEWLINES:
                value = value.replace("\r", "").replace("\n", "")
            add(name, value)
            dirname = (attrs.get("dirname") or "").strip()
            if dirname and kind in _DIRNAME_TYPES:
                add(dirname, _direction(element))
            continue
        if tag == "select":
            for option in model.selected_options(element):
                if not css.is_disabled(option):
                    add(name, option_value(option))
            continue
        if tag == "textarea":
            add(name, _crlf(model.field_value(element)))
            dirname = (attrs.get("dirname") or "").strip()
            if dirname:
                add(dirname, _direction(element))
    return FormSubmission(
        method=method.upper(),
        url=action,
        enctype=enctype if method == "post" else FORM_URLENCODED,
        charset=charset,
        fields=fields,
        files=files,
        target=target,
        form_ref=model.ref_of(form),
        submitter_ref=model.ref_of(submitter) if submitter is not None else None,
        field_names=names,
    )


def reset_form(model: PageModel, form: Element) -> None:
    """Forget typed values, checks, selections and files: markup defaults return."""
    state = model.state
    for element in form_fields(model, form):
        state.values.pop(element.index, None)
        state.checked.pop(element.index, None)
        state.selected.pop(element.index, None)
        state.files.pop(element.index, None)


def validation_notes(model: PageModel, form: Element) -> list[str]:
    """What a browser's constraint validation would report; notes only."""
    notes: list[str] = []
    radio_groups: set[str] = set()
    for element in form_fields(model, form):
        if _skipped(element) or _is_button(model, element):
            continue
        tag = element.tag
        kind = model.field_kind(element) if tag == "input" else tag
        if kind == "hidden" or "readonly" in element.attrs:
            continue
        attrs = element.attrs
        label = model.accessible_name(element)
        who = f"поле {model.ref_label(element)}" + (f" «{label}»" if label else "")
        required = "required" in attrs
        if kind == "radio":
            name = attrs.get("name") or ""
            if not required or name in radio_groups:
                continue
            radio_groups.add(name)
            group = _expand_group(model, [element])
            if not any(model.field_checked(item) for item in group):
                notes.append(f"{who}: не выбран ни один вариант обязательной группы")
            continue
        if kind == "checkbox":
            if required and not model.field_checked(element):
                notes.append(f"{who}: обязательный флажок не отмечен")
            continue
        if kind == "file":
            if required and not model.state.files.get(element.index):
                notes.append(f"{who}: обязательный файл не выбран")
            continue
        if tag == "select":
            if required:
                chosen = model.selected_options(element)
                if not chosen or option_value(chosen[0]) == "":
                    shown = option_label(chosen[0]) if chosen else ""
                    notes.append(
                        f"{who}: обязательный список не выбран"
                        + (f" (выбран «{shown}»)" if shown else "")
                    )
            continue
        value = model.field_value(element)
        if required and value == "":
            notes.append(f"{who}: обязательное поле не заполнено")
            continue
        if not value:
            continue
        for attribute, too_long in (("maxlength", True), ("minlength", False)):
            raw = (attrs.get(attribute) or "").strip()
            if raw.isdigit():
                limit = int(raw)
                if too_long and len(value) > limit:
                    notes.append(f"{who}: длина {len(value)} больше maxlength={limit}")
                elif not too_long and len(value) < limit:
                    notes.append(f"{who}: длина {len(value)} меньше minlength={limit}")
        pattern = attrs.get("pattern")
        if pattern and kind in _NO_NEWLINES:
            try:
                matched = re.fullmatch(f"(?:{pattern})", value) is not None
            except re.error:
                matched = True
            if not matched:
                notes.append(f"{who}: значение не соответствует шаблону pattern «{pattern}»")
    return notes


__all__ = [
    "FORM_MULTIPART",
    "FORM_TEXT",
    "FORM_URLENCODED",
    "FormInfo",
    "FormSubmission",
    "build_submission",
    "find_field",
    "form_charset",
    "form_fields",
    "form_info",
    "form_of",
    "is_submit_button",
    "reset_form",
    "submit_buttons",
    "validation_notes",
]
