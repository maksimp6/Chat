"""The http tab engine: browser semantics without JavaScript.

A tab keeps its history as fetched documents. Pages that are not current are
stored zlib-compressed; back and forward show the stored copy without a new
request (reload repeats it, POST included). Typed values, checks, selections
and attached files live in a ``FieldState`` per history entry, so forms are
filled and submitted exactly as a browser would build the request: links are
followed, submit buttons submit, reset resets, checkboxes toggle. Anything that
needs a script (a JavaScript link, a button without a form) is reported with the
way to open the page in Chrome.
"""

from __future__ import annotations

import logging
import time
import zlib
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from browser import forms
from browser.dom import Element, normalize_space
from browser.engine import (
    Action,
    ActionOutcome,
    CaptureOutcome,
    CaptureRequest,
    ElementTarget,
    EvalOutcome,
    HistoryItem,
    Navigation,
    NavOutcome,
    PageSnapshot,
    WaitRequest,
    content_version,
)
from browser.errors import (
    E_BINARY_VIEW,
    E_CLICK_FORM,
    E_HISTORY_FWD,
    E_JS_NEEDED_HTTP,
    E_PRESS_NO_FORM,
    E_TAB_EMPTY,
    E_TARGET_REQUIRED,
    E_URL_SCHEME,
    E_WAIT_HTTP,
    BrowserBlocked,
    BrowserError,
    BrowserNetworkError,
    BrowserNotFound,
    Deadline,
    chrome_only_message,
    history_back_message,
)
from browser.http_engine import (
    HttpClient,
    LoadedDocument,
    RequestSpec,
    encode_submission,
    normalize_referrer_policy,
    origin_of,
    referrer_value,
    replay_spec,
)
from browser.page import FieldState, PageModel
from browser.paging import human_bytes, normalize_ref
from browser.redact import display_url
from browser.views import ViewPage

logger = logging.getLogger("logos.browser.http")

_NAVIGABLE_SCHEMES = ("http", "https", "file", "data", "about")
_ENTER_KEYS = ("enter", "return", "numpadenter")
_SAME_CONTEXT_TARGETS = ("", "_self", "_parent", "_top")
_WAIT_STEP = 0.2


def _strip_fragment(url: str) -> str:
    return url.split("#", 1)[0]


def _needs_chrome(message: str) -> BrowserError:
    """An error whose action Chrome can do: operations may reopen the page there (auto mode)."""
    return BrowserError(message, details={"needs_chrome": True})


@dataclass
class _Entry:
    doc: LoadedDocument
    state: FieldState = field(default_factory=FieldState)
    initiator: str = "typed"
    packed: bytes | None = None
    dropped: bool = False
    page: ViewPage | None = None
    dirty: bool = False
    title: str = ""


class HttpTabEngine:
    """TabEngine of the http engine for one tab."""

    name = "http"

    def __init__(self, client: HttpClient, tab_id: str) -> None:
        self.client = client
        self.tab_id = tab_id
        self.lost = False
        self.entries: list[_Entry] = []
        self.index = -1

    # -- history storage

    def has_page(self) -> bool:
        return 0 <= self.index < len(self.entries)

    def _current(self) -> _Entry:
        if not self.has_page():
            raise BrowserError(E_TAB_EMPTY.format(tab=self.tab_id))
        return self.entries[self.index]

    def _current_url(self) -> str | None:
        return self.entries[self.index].doc.url if self.has_page() else None

    @staticmethod
    def _freeze(entry: _Entry) -> None:
        text = entry.doc.text
        if text is not None and entry.packed is None:
            entry.packed = zlib.compress(text.encode("utf-8", "surrogatepass"), 6)
            entry.doc.text = None
        entry.page = None
        entry.dirty = False

    @staticmethod
    def _thaw(entry: _Entry) -> None:
        if entry.doc.text is None and entry.packed is not None:
            entry.doc.text = zlib.decompress(entry.packed).decode("utf-8", "surrogatepass")
            entry.packed = None

    def drop_copies(self) -> int:
        """Forget the stored text of every non-current entry; returns how many were dropped."""
        count = 0
        for position, entry in enumerate(self.entries):
            if position == self.index or entry.doc.kind == "binary":
                continue
            if entry.packed is not None or entry.doc.text is not None:
                entry.packed = None
                entry.doc.text = None
                entry.dropped = True
                entry.page = None
                count += 1
        return count

    def _snapshot(self, entry: _Entry) -> PageSnapshot:
        doc = entry.doc
        kind = doc.kind
        html = doc.text if kind == "html" else None
        text = doc.text if kind in ("xml", "json", "text") else None
        return PageSnapshot(
            engine="http",
            tab=self.tab_id,
            url=doc.url,
            title_hint="",
            status=doc.status,
            reason=doc.reason,
            method=doc.method,
            mime=doc.mime,
            content_type=doc.content_type,
            kind=kind,
            charset=doc.charset or "utf-8",
            charset_source=doc.charset_source,
            html=html,
            text=text,
            binary=doc.binary,
            headers=doc.headers,
            stamped=False,
            field_state=entry.state,
            focused_ref=None,
            version=content_version(html, text, doc.url),
            notes=[],
            fetched_at=doc.fetched_at,
            scroll=None,
            bytes=doc.size,
        )

    def page(self) -> ViewPage:
        entry = self._current()
        self._thaw(entry)
        if entry.page is None or entry.dirty:
            entry.page = ViewPage(self._snapshot(entry))
            entry.dirty = False
            try:
                entry.title = entry.page.title or ""
            except Exception as exc:
                logger.debug("title of %s failed: %s", self.tab_id, type(exc).__name__)
        return entry.page

    def _model(self) -> tuple[_Entry, PageModel]:
        """The current entry and its page model (the cached one, even when only its state changed)."""
        entry = self._current()
        if entry.page is None:
            self.page()
        assert entry.page is not None
        model = entry.page.model
        if model is None:
            doc = entry.doc
            raise BrowserError(
                E_BINARY_VIEW.format(
                    tab=self.tab_id,
                    mime=doc.mime or "application/octet-stream",
                    size=human_bytes(doc.size),
                )
            )
        return entry, model

    def _push(self, doc: LoadedDocument, initiator: str) -> ViewPage:
        if self.has_page():
            self._freeze(self.entries[self.index])
        del self.entries[self.index + 1 :]
        self.entries.append(_Entry(doc=doc, state=FieldState(), initiator=initiator))
        self.index = len(self.entries) - 1
        return self.page()

    def push_document(self, doc: LoadedDocument, *, initiator: str) -> ViewPage:
        """Show an already fetched document as the tab's new page (search and request tab modes)."""
        return self._push(doc, initiator)

    # -- loading

    def _spec_for(self, nav: Navigation) -> RequestSpec:
        if nav.submission is not None:
            source = self._current_url()
            spec = encode_submission(
                nav.submission,
                referrer=nav.referrer,
                origin=origin_of(source) if source else None,
                deadline=nav.deadline,
                max_redirects=nav.max_redirects,
                max_bytes=nav.max_bytes,
            )
            if nav.headers:
                spec.headers = list(spec.headers) + list(nav.headers)
            return spec
        return RequestSpec(
            url=nav.url,
            method=(nav.method or "GET").strip().upper() or "GET",
            headers=list(nav.headers or []),
            body=nav.body,
            content_type=nav.content_type,
            referrer=nav.referrer,
            mode="navigate",
            user_initiated=nav.user_initiated,
            follow_redirects=nav.follow_redirects,
            follow_refresh=True,
            max_redirects=nav.max_redirects,
            max_bytes=nav.max_bytes,
            deadline=nav.deadline,
        )

    def _fetch(self, spec: RequestSpec, ctx: Any) -> LoadedDocument:
        try:
            doc = self.client.load(spec)
        except BrowserBlocked as exc:
            details = getattr(exc, "details", {}) or {}
            host = details.get("blocked_host")
            if not host:
                try:
                    host = urlsplit(spec.url).hostname or "?"
                except ValueError:
                    host = "?"
            ctx.event(
                "browser_blocked",
                {
                    "tab": self.tab_id,
                    "host": host,
                    "stage": details.get("blocked_stage") or "request",
                    "count": 1,
                },
            )
            raise
        ctx.event(
            "browser_navigation",
            {
                "tab": self.tab_id,
                "engine": "http",
                "method": spec.method,
                "url": display_url(spec.url),
                "final_url": display_url(doc.url),
                "status": doc.status,
                "redirects": len(doc.headers.redirects) if doc.headers else 0,
                "bytes": doc.size,
                "kind": doc.kind,
                "elapsed_ms": doc.elapsed_ms,
            },
        )
        return doc

    def load(self, nav: Navigation, ctx: Any) -> NavOutcome:
        spec = self._spec_for(nav)
        doc = self._fetch(spec, ctx)
        page = self._push(doc, nav.initiator)
        return NavOutcome(page=page, navigated=True, notes=list(doc.notes))

    def go(
        self,
        delta: int,
        *,
        wait_until: str | None = None,
        deadline: Deadline,
        ctx: Any,
        max_redirects: int | None = None,
        max_bytes: int | None = None,
    ) -> NavOutcome:
        if not self.has_page():
            raise BrowserError(E_TAB_EMPTY.format(tab=self.tab_id))
        target = self.index + int(delta)
        if target < 0:
            raise BrowserError(history_back_message(self.tab_id, "http"))
        if target >= len(self.entries):
            raise BrowserError(E_HISTORY_FWD.format(tab=self.tab_id))
        if target == self.index:
            return NavOutcome(page=self.page(), navigated=False)
        previous = self.index
        self._freeze(self.entries[previous])
        entry = self.entries[target]
        notes: list[str] = []
        if entry.dropped:
            changes: dict[str, Any] = {}
            if max_redirects is not None:
                changes["max_redirects"] = max_redirects
            if max_bytes is not None:
                changes["max_bytes"] = max_bytes
            spec = replay_spec(entry.doc.spec, deadline=deadline, **changes)
            try:
                doc = self._fetch(spec, ctx)
            except BaseException:
                self._thaw(self.entries[previous])
                raise
            entry.doc = doc
            entry.dropped = False
            entry.packed = None
            entry.state = FieldState()
            entry.page = None
            notes.append(
                "сохранённая копия была удалена (browser_session clear) — страница загружена заново"
            )
            if spec.method == "POST":
                notes.append("запрос POST отправлен повторно")
            notes.extend(doc.notes)
        else:
            self._thaw(entry)
            notes.append("показана сохранённая копия; обновить — browser_history(action='reload')")
        self.index = target
        return NavOutcome(page=self.page(), navigated=True, notes=notes)

    def reload(
        self,
        *,
        wait_until: str | None = None,
        deadline: Deadline,
        ctx: Any,
        max_redirects: int | None = None,
        max_bytes: int | None = None,
    ) -> NavOutcome:
        entry = self._current()
        changes: dict[str, Any] = {}
        if max_redirects is not None:
            changes["max_redirects"] = max_redirects
        if max_bytes is not None:
            changes["max_bytes"] = max_bytes
        spec = replay_spec(entry.doc.spec, deadline=deadline, **changes)
        doc = self._fetch(spec, ctx)
        entry.doc = doc
        entry.packed = None
        entry.dropped = False
        entry.state = FieldState()
        entry.page = None
        entry.dirty = False
        notes = list(doc.notes)
        if spec.method == "POST":
            notes.insert(0, "запрос POST отправлен повторно")
        return NavOutcome(page=self.page(), navigated=True, notes=notes)

    def history(self) -> list[HistoryItem]:
        items: list[HistoryItem] = []
        for position, entry in enumerate(self.entries):
            items.append(
                HistoryItem(
                    index=position,
                    url=display_url(entry.doc.url),
                    title=entry.title,
                    method=entry.doc.method,
                    status=entry.doc.status,
                    engine="http",
                    current=position == self.index,
                )
            )
        return items

    # -- actions

    def _chrome_only(self, action: str) -> BrowserError:
        url = self._current_url() or "about:blank"
        return _needs_chrome(chrome_only_message(action, self.tab_id, display_url(url)))

    def _js_needed(self, model: PageModel, element: Element) -> BrowserError:
        url = self._current_url() or model.url
        return _needs_chrome(
            E_JS_NEEDED_HTTP.format(
                ref=model.ref_label(element),
                name=model.accessible_name(element) or normalize_space(element.text())[:80],
                tab=self.tab_id,
                url=display_url(url),
            )
        )

    @staticmethod
    def _page_policy(entry: _Entry, model: PageModel) -> str | None:
        policy = None
        for element in model.document.elements:
            if element.tag == "meta" and (element.get("name") or "").strip().lower() == "referrer":
                found = normalize_referrer_policy(element.get("content"))
                if found:
                    policy = found
        return policy or entry.doc.referrer_policy

    def _resolve(self, model: PageModel, target: ElementTarget | None, prefixes: str, label: str) -> tuple[Element, list[str]]:
        if target is None or target.is_empty():
            raise BrowserError(E_TARGET_REQUIRED)
        if not (target.ref or target.selector or target.text):
            raise self._chrome_only(label)
        return model.resolve_target(target, prefixes)

    def _mutated(self, entry: _Entry, model: PageModel) -> list[str]:
        entry.state = model.state
        entry.dirty = True
        return model.take_notes()

    def perform(self, action: Action, ctx: Any) -> ActionOutcome:
        kind = (action.kind or "").strip().lower()
        if kind == "click":
            return self._click(action, ctx)
        if kind in ("hover", "scroll"):
            raise self._chrome_only("наведение" if kind == "hover" else "прокрутка")
        entry, model = self._model()
        if kind == "press":
            return self._press(entry, model, action, ctx)
        if kind == "submit":
            return self._submit_action(entry, model, action, ctx)
        if kind in ("type", "set_value", "clear"):
            element, notes = self._resolve(model, action.target, "I", "ввод текста")
            if kind == "clear":
                value = ""
            else:
                value = action.value or ""
                if not action.replace and element.tag != "select":
                    value = model.field_value(element) + value
            if element.tag == "select":
                selected = model.select_options(element, [value]) if value else []
                notes.extend(self._mutated(entry, model))
                return ActionOutcome(selected=selected, notes=notes)
            model.set_value(element, value)
            model.focus(element)
            notes.extend(self._mutated(entry, model))
            return ActionOutcome(notes=notes)
        if kind == "select":
            element, notes = self._resolve(model, action.target, "I", "выбор варианта")
            values = list(action.values) if action.values else (
                [action.value] if action.value is not None else []
            )
            selected = model.select_options(element, values)
            notes.extend(self._mutated(entry, model))
            return ActionOutcome(selected=selected, notes=notes)
        if kind in ("check", "uncheck"):
            element, notes = self._resolve(model, action.target, "I", "флажок")
            if element.tag == "label":
                control = self._label_control(model, element)
                if control is not None:
                    element = control
            model.set_checked(element, kind == "check")
            notes.extend(self._mutated(entry, model))
            return ActionOutcome(notes=notes)
        if kind == "focus":
            element, notes = self._resolve(model, action.target, "IBL", "фокус")
            model.focus(element)
            notes.extend(self._mutated(entry, model))
            return ActionOutcome(notes=notes)
        if kind == "set_files":
            element, notes = self._resolve(model, action.target, "I", "выбор файлов")
            model.set_files(element, list(action.values))
            notes.extend(self._mutated(entry, model))
            return ActionOutcome(notes=notes)
        raise self._chrome_only(kind or "действие")

    @staticmethod
    def _activation(model: PageModel, element: Element) -> str | None:
        tag = element.tag
        if tag in ("a", "area") and "href" in element.attrs:
            return "link"
        if tag in ("iframe", "frame") and (element.get("src") or "").strip():
            return "link"
        if tag == "button":
            kind = (element.get("type") or "submit").strip().lower()
            if kind == "reset":
                return "reset"
            if kind in ("button", "menu"):
                return None
            return "submit"
        if tag == "input":
            kind = model.field_kind(element)
            if kind in ("submit", "image"):
                return "submit"
            if kind == "reset":
                return "reset"
            if kind in ("checkbox", "radio"):
                return "toggle"
            if kind in ("button", "hidden"):
                return None
            return "focus"
        if tag == "label":
            return "label"
        if tag == "summary":
            return "summary"
        if tag == "option":
            return "option"
        if tag in ("select", "textarea"):
            return "focus"
        if model.field_kind(element) == "contenteditable":
            return "focus"
        return None

    @staticmethod
    def _label_control(model: PageModel, label: Element) -> Element | None:
        target_id = (label.get("for") or "").strip()
        if target_id:
            found = model.document.by_id(target_id)
            if found is not None:
                return found
        for element in label.descendants():
            if element.tag in ("button", "select", "textarea", "meter", "output", "progress"):
                return element
            if element.tag == "input" and model.field_kind(element) != "hidden":
                return element
        return None

    def _click(self, action: Action, ctx: Any) -> ActionOutcome:
        target = action.target
        if target is None or target.is_empty():
            raise BrowserError(E_TARGET_REQUIRED)
        if not (target.ref or target.selector or target.text):
            raise self._chrome_only("нажатие по координатам")
        entry, model = self._model()
        element, notes = model.resolve_target(target, "LBI")
        ref = model.ref_of(element) or ""
        if element.tag == "form" or ref.startswith("F"):
            raise BrowserError(E_CLICK_FORM.format(ref=ref or model.ref_label(element)))
        activation = self._activation(model, element)
        if activation is None:
            ancestor = model.clickable_ancestor(element)
            if ancestor is None or ancestor is element:
                raise self._js_needed(model, element)
            element = ancestor
            activation = self._activation(model, element)
            if activation is None:
                raise self._js_needed(model, element)
        if activation == "label":
            control = self._label_control(model, element)
            if control is None:
                notes.append(f"Подпись {model.ref_label(element)} не связана с полем — нажатие ничего не меняет")
                return ActionOutcome(notes=notes)
            element = control
            activation = self._activation(model, element) or "focus"
            if activation == "label":
                activation = "focus"
        if activation == "link":
            return self._follow(entry, model, element, action, ctx, notes)
        if activation == "submit":
            form = forms.form_of(model, element)
            if form is None:
                raise self._js_needed(model, element)
            return self._submit(entry, model, form, element, action, ctx, notes)
        if activation == "reset":
            form = forms.form_of(model, element)
            if form is None:
                raise self._js_needed(model, element)
            forms.reset_form(model, form)
            notes.extend(self._mutated(entry, model))
            notes.append(f"Форма {model.ref_label(form)} сброшена к исходным значениям")
            return ActionOutcome(notes=notes)
        if activation == "toggle":
            kind = model.field_kind(element)
            checked = True if kind == "radio" else not model.field_checked(element)
            model.set_checked(element, checked)
            model.focus(element)
            notes.extend(self._mutated(entry, model))
            return ActionOutcome(notes=notes)
        if activation == "option":
            holder = element.closest(lambda node: node.tag in ("select", "datalist"))
            if holder is None or holder.tag != "select":
                raise self._js_needed(model, element)
            option_value = element.get("value")
            if option_value is None:
                option_value = normalize_space(element.text())
            selected = model.select_options(holder, [option_value])
            notes.extend(self._mutated(entry, model))
            return ActionOutcome(selected=selected, notes=notes)
        if activation == "summary":
            notes.append("Содержимое details и так показано целиком")
            return ActionOutcome(notes=notes)
        # focus
        model.focus(element)
        notes.extend(self._mutated(entry, model))
        if model.field_kind(element) == "file":
            notes.append(
                f"Файлы для {model.ref_label(element)} задаются через browser_fill_form(fields=[{{field: '{model.ref_label(element)}', file_paths: […]}}])"
            )
        else:
            notes.append(f"Фокус на {model.ref_label(element)}; ввод — browser_act(action='type')")
        return ActionOutcome(notes=notes)

    def _follow(
        self,
        entry: _Entry,
        model: PageModel,
        element: Element,
        action: Action,
        ctx: Any,
        notes: list[str],
    ) -> ActionOutcome:
        raw = (element.get("href") if element.tag in ("a", "area") else element.get("src")) or ""
        if raw.strip().lower().startswith("javascript:"):
            raise self._js_needed(model, element)
        current = entry.doc.url
        target_url = model.href(element) or current
        try:
            parts = urlsplit(target_url)
        except ValueError:
            raise BrowserError(E_URL_SCHEME) from None
        scheme = (parts.scheme or "").lower()
        if scheme not in _NAVIGABLE_SCHEMES:
            raise BrowserError(
                f"{E_URL_SCHEME}. Ссылка {model.ref_label(element)} ведёт на {display_url(target_url)}"
            )
        if parts.fragment and _strip_fragment(target_url) == _strip_fragment(current):
            notes.append(
                f"Ссылка ведёт на якорь этой страницы: browser_read(selector='#{parts.fragment}')"
            )
            return ActionOutcome(navigated=False, notes=notes)
        if "download" in element.attrs:
            notes.append(
                f"У ссылки атрибут download — файл можно сохранить: browser_download(ref='{model.ref_label(element)}', path='…', overwrite=false)"
            )
        if (element.get("onclick") or "").strip():
            notes.append("У ссылки есть обработчик onclick — без JavaScript результат может отличаться")
        rel = (element.get("rel") or "").lower().split()
        policy = element.get("referrerpolicy") or self._page_policy(entry, model)
        referrer = referrer_value(
            current, target_url, policy=policy, noreferrer="noreferrer" in rel
        )
        target_name = (element.get("target") or "").strip().lower()
        navigation = Navigation(
            url=target_url,
            method="GET",
            referrer=referrer,
            initiator="link",
            deadline=action.deadline,
            max_redirects=action.max_redirects,
            max_bytes=action.max_bytes,
        )
        if action.new_tab or target_name not in _SAME_CONTEXT_TARGETS:
            return ActionOutcome(navigated=False, new_tab_navigation=navigation, notes=notes)
        outcome = self.load(navigation, ctx)
        return ActionOutcome(navigated=True, page=outcome.page, notes=notes + outcome.notes)

    def _submit(
        self,
        entry: _Entry,
        model: PageModel,
        form: Element,
        submitter: Element | None,
        action: Action,
        ctx: Any,
        notes: list[str],
    ) -> ActionOutcome:
        submission = forms.build_submission(model, form, submitter)
        notes = list(notes)
        for note in forms.validation_notes(model, form):
            if note not in notes:
                notes.append(note)
        current = entry.doc.url
        referrer = referrer_value(current, submission.url, policy=self._page_policy(entry, model))
        navigation = Navigation(
            url=submission.url,
            method=submission.method,
            referrer=referrer,
            initiator="form",
            submission=submission,
            deadline=action.deadline,
            max_redirects=action.max_redirects,
            max_bytes=action.max_bytes,
        )
        target_name = (submission.target or "").strip().lower()
        if action.new_tab or target_name not in _SAME_CONTEXT_TARGETS:
            origin = origin_of(current)
            if submission.method.upper() == "POST" and origin:
                navigation.headers = [("Origin", origin)]
            return ActionOutcome(navigated=False, new_tab_navigation=navigation, notes=notes)
        outcome = self.load(navigation, ctx)
        return ActionOutcome(navigated=True, page=outcome.page, notes=notes + outcome.notes)

    def _submit_action(self, entry: _Entry, model: PageModel, action: Action, ctx: Any) -> ActionOutcome:
        element, notes = self._resolve(model, action.target, "FBI", "отправка формы")
        form = element if element.tag == "form" else forms.form_of(model, element)
        if form is None:
            raise BrowserError(E_PRESS_NO_FORM.format(ref=model.ref_label(element)))
        submitter = None
        if action.submitter is not None and not action.submitter.is_empty():
            submitter, extra = model.resolve_target(action.submitter, "B")
            notes.extend(extra)
        return self._submit(entry, model, form, submitter, action, ctx, notes)

    def _press(self, entry: _Entry, model: PageModel, action: Action, ctx: Any) -> ActionOutcome:
        key = (action.value or "").strip()
        if key.lower() not in _ENTER_KEYS:
            raise self._chrome_only(f"клавиша {key or '?'}")
        notes: list[str] = []
        if action.target is not None and not action.target.is_empty():
            element, notes = self._resolve(model, action.target, "IBL", "нажатие клавиши")
        else:
            focused = model.state.focused
            if focused is None or not (0 <= focused < len(model.document.elements)):
                raise BrowserError(E_TARGET_REQUIRED)
            element = model.document.elements[focused]
        if element.tag == "textarea":
            model.set_value(element, model.field_value(element) + "\n")
            notes.extend(self._mutated(entry, model))
            return ActionOutcome(notes=notes)
        activation = self._activation(model, element)
        if activation == "link":
            return self._follow(entry, model, element, action, ctx, notes)
        if activation in ("submit", "reset", "toggle", "option", "summary"):
            click = Action(
                kind="click",
                target=ElementTarget(ref=model.ref_of(element)) if model.ref_of(element) else action.target,
                new_tab=action.new_tab,
                deadline=action.deadline,
                max_redirects=action.max_redirects,
                max_bytes=action.max_bytes,
            )
            if click.target is not None and not click.target.is_empty():
                return self._click(click, ctx)
        form = forms.form_of(model, element)
        if form is None:
            raise BrowserError(E_PRESS_NO_FORM.format(ref=model.ref_label(element)))
        buttons = forms.submit_buttons(model, form)
        submitter = buttons[0] if buttons else None
        return self._submit(entry, model, form, submitter, action, ctx, notes)

    # -- other engine operations

    def mark_matches(self, selector: str) -> ViewPage:
        raise self._chrome_only("пометка элементов по селектору")

    def wait(self, request: WaitRequest, ctx: Any) -> dict[str, Any]:
        if (
            request.selector
            or request.text
            or request.text_gone
            or request.url_contains
            or request.expression
            or request.network_idle
        ):
            raise _needs_chrome(E_WAIT_HTTP)
        seconds = float(request.seconds or 0)
        started = time.monotonic()
        end = started + seconds
        while True:
            left = end - time.monotonic()
            if left <= 0:
                break
            if self.client.closed:
                raise BrowserNetworkError("Ожидание прервано: браузерная сессия закрыта")
            budget = request.deadline.remaining()
            if budget is not None and budget <= 0:
                request.deadline.check(f"пауза {seconds:g} с")
            step = min(left, _WAIT_STEP)
            if budget is not None:
                step = min(step, max(budget, 0.01))
            time.sleep(step)
        waited = time.monotonic() - started
        url = self._current_url()
        title = ""
        if self.has_page():
            title = self.entries[self.index].title
        return {
            "waited_seconds": round(waited, 3),
            "satisfied": [f"пауза {seconds:g} с"] if seconds else [],
            "url": display_url(url) if url else None,
            "title": title,
        }

    def evaluate(
        self,
        expression: str,
        *,
        target: ElementTarget | None,
        await_promise: bool,
        deadline: Deadline,
    ) -> EvalOutcome:
        raise self._chrome_only("выполнение JavaScript")

    def capture(self, request: CaptureRequest, sink: Any) -> CaptureOutcome:
        raise self._chrome_only("снимок страницы")

    def network(self) -> list[dict[str, Any]]:
        if not self.has_page():
            return []
        doc = self.entries[self.index].doc
        entries: list[dict[str, Any]] = []
        hops = doc.headers.redirects if doc.headers else []
        for hop in hops:
            entries.append(
                {
                    "id": f"N{len(entries) + 1}",
                    "method": hop.method,
                    "url": hop.url,
                    "status": hop.status,
                    "type": "refresh" if hop.status == "refresh" else "redirect",
                    "mime": "",
                    "bytes": None,
                    "blocked": False,
                    "failure": None,
                }
            )
        entries.append(
            {
                "id": f"N{len(entries) + 1}",
                "method": doc.method,
                "url": display_url(doc.url),
                "status": doc.status,
                "type": "document",
                "mime": doc.mime,
                "bytes": doc.size,
                "blocked": False,
                "failure": None,
            }
        )
        return entries

    def console(self) -> list[dict[str, Any]]:
        return []

    def response_body(self, entry_id: str) -> tuple[bytes, str | None]:
        wanted = normalize_ref(entry_id)
        entries = self.network()
        ids = [item["id"] for item in entries]
        if wanted not in ids:
            raise BrowserNotFound(
                f"Запрос {wanted} не найден; список запросов: browser_read(view='network')"
            )
        if wanted != ids[-1]:
            return b"", None
        entry = self._current()
        self._thaw(entry)
        doc = entry.doc
        if doc.text is None:
            raise BrowserNotFound(
                f"Тело ответа {wanted} не загружено: это файл ({doc.mime or 'двоичные данные'}); "
                f"скачайте его: browser_download(tab='{self.tab_id}', path='…', overwrite=false)"
            )
        return doc.text.encode("utf-8", "surrogatepass"), "utf-8"

    def close(self) -> None:
        self.entries.clear()
        self.index = -1


__all__ = ["HttpTabEngine"]
