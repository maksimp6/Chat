"""The engine interface shared by the http and Chrome tab engines.

A tab engine loads documents, keeps the tab history and performs actions; it
hands pages to the views as ``PageSnapshot`` values wrapped in ``ViewPage``.
Engines raise ``BrowserError`` and never return error mappings.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Protocol

from browser.errors import Deadline

if TYPE_CHECKING:
    from browser.forms import FormSubmission
    from browser.page import FieldState
    from browser.views import ViewPage

logger = logging.getLogger("logos.browser.engine")


def _no_deadline() -> Deadline:
    return Deadline(None)


def content_version(html: str | None, text: str | None, url: str) -> str:
    """sha1 of the document content (or of the URL when it has none), 16 hex chars."""
    source = html if html is not None else text if text is not None else url
    data = (source or "").encode("utf-8", "surrogatepass")
    return hashlib.sha1(data).hexdigest()[:16]


@dataclass
class Hop:
    status: int | str  # "refresh" for meta/Refresh hops
    url: str  # display URL
    method: str


@dataclass
class HeadersInfo:
    method: str
    url: str
    status: int | None = None
    reason: str = ""
    http_version: str | None = None
    request_headers: list[tuple[str, str]] = field(default_factory=list)  # masked
    response_headers: list[tuple[str, str]] = field(default_factory=list)  # masked
    redirects: list[Hop] = field(default_factory=list)
    mime: str = ""
    charset: str | None = None
    charset_source: str | None = None
    bytes: int | None = None
    elapsed_ms: int | None = None
    remote_address: str | None = None


@dataclass
class BinaryInfo:
    mime: str
    length: int | None = None
    filename: str | None = None
    disposition: str | None = None


@dataclass
class PageSnapshot:
    engine: str
    tab: str
    url: str  # real URL, internal only
    title_hint: str = ""
    status: int | None = None
    reason: str = ""
    method: str = "GET"
    mime: str = ""
    content_type: str = ""
    kind: str = "empty"  # html|xml|json|text|binary|empty
    charset: str = "utf-8"
    charset_source: str = ""
    html: str | None = None  # kind html
    text: str | None = None  # xml/json/text
    binary: BinaryInfo | None = None
    headers: HeadersInfo | None = None
    stamped: bool = False
    field_state: FieldState | None = None
    focused_ref: str | None = None
    version: str = ""  # content_version(html, text, url)
    notes: list[str] = field(default_factory=list)
    fetched_at: float = field(default_factory=time.time)
    scroll: dict[str, Any] | None = None
    bytes: int | None = None


@dataclass
class ElementTarget:
    ref: str | None = None
    selector: str | None = None
    text: str | None = None
    x: float | None = None
    y: float | None = None
    nth: int | None = None

    def describe(self) -> str:
        """"L5" / "селектор «…»" / "текст «…»" / "точка (x, y)"."""
        if self.ref:
            return self.ref
        suffix = f" №{self.nth}" if self.nth else ""
        if self.selector:
            return f"селектор «{self.selector}»{suffix}"
        if self.text:
            return f"текст «{self.text}»{suffix}"
        if self.x is not None and self.y is not None:
            return f"точка ({self.x:g}, {self.y:g})"
        return "цель не указана"

    def is_empty(self) -> bool:
        return not (
            self.ref
            or self.selector
            or self.text
            or (self.x is not None and self.y is not None)
        )


@dataclass
class Navigation:
    url: str
    method: str = "GET"
    headers: list[tuple[str, str]] = field(default_factory=list)
    referrer: str | None = None
    initiator: str = "typed"  # typed|link|form|reload|history|search|request
    submission: FormSubmission | None = None  # http only
    body: bytes | None = None  # http only (browser_request tab mode)
    content_type: str | None = None  # http only
    wait_until: str | None = None  # chrome: commit|domcontentloaded|load|networkidle|networkidle2
    deadline: Deadline = field(default_factory=_no_deadline)
    max_redirects: int = 0
    max_bytes: int = 0
    follow_redirects: bool = True
    user_initiated: bool = True


@dataclass
class NavOutcome:
    page: ViewPage
    navigated: bool = True
    blocked: list[str] = field(default_factory=list)
    downloads: list[dict[str, Any]] = field(default_factory=list)
    popups: list[TabEngine] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Action:
    kind: str  # click|type|press|hover|scroll|select|check|uncheck|focus|clear|set_value|set_files|submit
    target: ElementTarget | None = None
    value: str | None = None
    values: list[str] = field(default_factory=list)
    submitter: ElementTarget | None = None
    force: bool = False
    new_tab: bool = False
    replace: bool = True
    wait_until: str | None = None
    deadline: Deadline = field(default_factory=_no_deadline)
    max_redirects: int = 0
    max_bytes: int = 0


@dataclass
class ActionOutcome:
    navigated: bool = False
    page: ViewPage | None = None
    # http: target=_blank / new_tab; the session opens it in a new tab.
    new_tab_navigation: Navigation | None = None
    popups: list[TabEngine] = field(default_factory=list)
    downloads: list[dict[str, Any]] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    selected: list[str] = field(default_factory=list)


@dataclass
class HistoryItem:
    index: int
    url: str
    title: str
    method: str
    status: int | None
    engine: str
    current: bool


@dataclass
class WaitRequest:
    selector: str | None = None
    selector_state: str = "visible"
    text: str | None = None
    text_gone: str | None = None
    url_contains: str | None = None
    expression: str | None = None
    network_idle: bool = False
    seconds: float | None = None
    deadline: Deadline = field(default_factory=_no_deadline)


@dataclass
class EvalOutcome:
    value: Any
    type: str
    console: list[str] = field(default_factory=list)


@dataclass
class CaptureRequest:
    format: str  # png|jpeg|pdf
    full_page: bool = False
    target: ElementTarget | None = None
    quality: int | None = None
    landscape: bool = False
    print_background: bool = False
    paper: str | None = None
    scale: float | None = None
    page_ranges: str | None = None
    deadline: Deadline = field(default_factory=_no_deadline)


@dataclass
class CaptureOutcome:
    bytes: int
    width: int | None = None
    height: int | None = None


class BinarySink(Protocol):
    def write(self, data: bytes) -> int: ...


class EventSink(Protocol):
    def event(self, event_type: str, payload: Mapping[str, Any]) -> None: ...

    def secret(self, value: str | None) -> None: ...


class TabEngine(Protocol):
    name: str  # "http" | "chrome"
    tab_id: str  # assignable (popups)
    lost: bool

    def load(self, nav: Navigation, ctx: EventSink) -> NavOutcome: ...

    def page(self) -> ViewPage:
        """The current page; chrome takes a fresh snapshot on every call."""
        ...

    def has_page(self) -> bool: ...

    def history(self) -> list[HistoryItem]: ...

    def go(
        self,
        delta: int,
        *,
        wait_until: str | None,
        deadline: Deadline,
        ctx: EventSink,
    ) -> NavOutcome: ...

    def reload(
        self, *, wait_until: str | None, deadline: Deadline, ctx: EventSink
    ) -> NavOutcome: ...

    def perform(self, action: Action, ctx: EventSink) -> ActionOutcome: ...

    def mark_matches(self, selector: str) -> ViewPage:
        """Chrome only; the http engine raises chrome_only_message."""
        ...

    def wait(self, request: WaitRequest, ctx: EventSink) -> dict[str, Any]: ...

    def evaluate(
        self,
        expression: str,
        *,
        target: ElementTarget | None,
        await_promise: bool,
        deadline: Deadline,
    ) -> EvalOutcome: ...

    def capture(self, request: CaptureRequest, sink: BinarySink) -> CaptureOutcome: ...

    def network(self) -> list[dict[str, Any]]: ...

    def console(self) -> list[dict[str, Any]]: ...

    def response_body(self, entry_id: str) -> tuple[bytes, str | None]: ...

    def close(self) -> None: ...


__all__ = [
    "Action",
    "ActionOutcome",
    "BinaryInfo",
    "BinarySink",
    "CaptureOutcome",
    "CaptureRequest",
    "ElementTarget",
    "EvalOutcome",
    "EventSink",
    "HeadersInfo",
    "HistoryItem",
    "Hop",
    "NavOutcome",
    "Navigation",
    "PageSnapshot",
    "TabEngine",
    "WaitRequest",
    "content_version",
]
