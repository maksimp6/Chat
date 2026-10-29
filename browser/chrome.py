"""The Chrome engine: one headless Chrome per browser session, one target per tab.

``ChromeBrowser`` owns the Chrome process of a session and its DevTools
connection. Every page, frame and worker target is attached in flat mode and
paused until it is prepared (``waitForDebuggerOnStart``); preparing a target
enables request interception first, so every request and redirect of the page,
its out-of-process frames and its workers passes the same link-local guard as
the http engine (``guard.is_blocked``). A target whose interception cannot be
enabled is never resumed (fail closed). Chrome's own URL blocklist with the
literal link-local patterns is a second line under the interception.

``ChromeTabEngine`` implements the TabEngine interface for one tab. Its page
helper (``chrome_page.js``) runs in an isolated world, so page scripts cannot
see or change it: it stamps refs (L/B/I/F/T/M, the same ``REF_KINDS`` as the
http engine) and live field state into the DOM and serializes the document for
the views. JavaScript the model sends with browser_evaluate runs in the page's
main world.

Threads: the DevTools reader ("logos-browser-cdp") only records events and
enqueues work; target preparation and download renames run on one worker
("logos-browser-attach"); request decisions, dialogs and proxy credentials run
on a small pool ("logos-browser-guard"), whose size is scheduling width only:
every request is decided. Tab state lives under the browser's lock; no DevTools
command is ever sent while that lock is held.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import math
import os
import queue
import re
import signal
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from browser import chrome_launch, guard
from browser.cdp import CdpConnection, CdpError, ChromeClosed
from browser.charsets import GENERIC_MIMES, classify, decode_body, normalize_charset, parse_content_type
from browser.engine import (
    Action,
    ActionOutcome,
    BinaryInfo,
    BinarySink,
    CaptureOutcome,
    CaptureRequest,
    ElementTarget,
    EvalOutcome,
    EventSink,
    HeadersInfo,
    HistoryItem,
    Hop,
    Navigation,
    NavOutcome,
    PageSnapshot,
    WaitRequest,
    content_version,
)
from browser.errors import (
    E_BODY_GONE,
    E_CHROME_DIED,
    E_CHROME_NAV,
    E_CONNECT,
    E_COVERED,
    E_DNS,
    E_EVAL,
    E_GUARD_SETUP,
    E_HISTORY_FWD,
    E_NOT_VISIBLE,
    E_NTH_RANGE,
    E_OPTION_NOT_FOUND,
    E_PROXY,
    E_REDIRECT_LINK_LOCAL,
    E_REF_NOT_FOUND,
    E_SELECTOR_NONE,
    E_SELECTOR_SYNTAX,
    E_TAB_EMPTY,
    E_TAB_LOST,
    E_TARGET_REQUIRED,
    E_TEXT_NOT_FOUND,
    E_TLS,
    E_UNKNOWN_KEY,
    E_WAIT_TIMEOUT,
    LINK_LOCAL_MESSAGE,
    BrowserBlocked,
    BrowserError,
    BrowserNetworkError,
    BrowserNotFound,
    BrowserTimeout,
    BrowserUnavailable,
    Deadline,
    history_back_message,
    timeout_message,
)
from browser.files import filename_from_response, sanitize_filename, unique_name
from browser.page import (
    CHECKED_ATTR,
    HIDDEN_ATTR,
    MATCH_ATTR,
    REF_ATTR,
    REF_KINDS,
    SELECTED_ATTR,
    SRC_ATTR,
    VALUE_ATTR,
    VALUE_LEN_ATTR,
)
from browser.paging import normalize_ref, plural
from browser.redact import display_url, mask_headers, redact_text
from browser.views import ViewPage

if TYPE_CHECKING:
    from browser.settings import ChromeOptions

logger = logging.getLogger("logos.browser.chrome")

# Technical constants (none of them limits content): how long a started action may take to
# begin a navigation, the pause after an action that did not navigate, the quiet window of
# "network idle", the IO.read chunk of a streamed PDF, the poll step of waits that cannot
# use page promises, the pool width of request decisions, and the grace for a page-side
# wait to report its own timeout before the reply wait gives up.
_NAVIGATION_START_WINDOW = 0.5
_SETTLE_DELAY = 0.15
_NETWORK_IDLE_WINDOW = 0.5
_IO_CHUNK = 1 << 20
_WAIT_POLL = 0.1
_GUARD_WORKERS = 8
_WAIT_REPLY_GRACE = 1.0

_WORLD_NAME = "logos"
_ACTION_GROUP = "logos-action"
_EVAL_GROUP = "logos-eval"
_SERIALIZER_MARK = "/*__LOGOS_SERIALIZER__*/"
_HELPER_FILE = "chrome_page.js"
_AUTO_ATTACH = {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True}
_ATTRS = {
    "REF_ATTR": REF_ATTR,
    "HIDDEN_ATTR": HIDDEN_ATTR,
    "VALUE_ATTR": VALUE_ATTR,
    "VALUE_LEN_ATTR": VALUE_LEN_ATTR,
    "CHECKED_ATTR": CHECKED_ATTR,
    "SELECTED_ATTR": SELECTED_ATTR,
    "MATCH_ATTR": MATCH_ATTR,
    "SRC_ATTR": SRC_ATTR,
}
_LIFECYCLE_NAMES = {
    "domcontentloaded": "DOMContentLoaded",
    "load": "load",
    "networkidle": "networkIdle",
    "networkidle2": "networkAlmostIdle",
}
_ALL_LIFECYCLE = frozenset({"init", "commit", "DOMContentLoaded", "load", "networkAlmostIdle", "networkIdle"})
_TRANSITIONS = {
    "typed": "typed",
    "link": "link",
    "form": "form_submit",
    "reload": "reload",
    "history": "auto_toplevel",
    "search": "generated",
    "request": "other",
}
_PAPER_SIZES = {
    "A3": (11.69, 16.54),
    "A4": (8.27, 11.69),
    "A5": (5.83, 8.27),
    "Letter": (8.5, 11.0),
    "Legal": (8.5, 14.0),
    "Tabloid": (11.0, 17.0),
}
_CONTEXT_GONE = (
    "cannot find context",
    "execution context was destroyed",
    "cannot find default execution context",
    "cannot find execution context",
    "inspected target navigated or closed",
    "uniquecontextid",
    "promise was collected",
    "node with given id",
    "could not find node",
    "no frame with given id",
    "frame with the given id was not found",
)
_DIALOG_WORDS = {
    "alert": "сообщение (alert)",
    "confirm": "подтверждение (confirm)",
    "prompt": "запрос ввода (prompt)",
    "beforeunload": "предупреждение об уходе со страницы (beforeunload)",
}
_SELECTOR_STATES = {
    "visible": "виден",
    "attached": "есть на странице",
    "hidden": "скрыт или отсутствует",
    "detached": "отсутствует",
}
_SOURCE_WORDS = {"env": chrome_launch.CHROME_ENV, "path": "PATH", "playwright": "Playwright"}
_EMULATION_WORDS = {
    "Network.setExtraHTTPHeaders": "дополнительные заголовки",
    "Emulation.setUserAgentOverride": "User-Agent и язык",
    "Emulation.setDeviceMetricsOverride": "размер окна",
    "Emulation.setTouchEmulationEnabled": "сенсорный ввод",
    "Emulation.setScriptExecutionDisabled": "настройка JavaScript",
    "Emulation.setFocusEmulationEnabled": "фокус вкладки",
}
_FN_POINT = "function () { return window.__logos.point(this); }"
_FN_REF = "function () { return window.__logos.refOf(this); }"
_HELPER: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Small helpers


def _helper_sources() -> tuple[str, str]:
    """(page helper source, value serializer function), read from chrome_page.js once."""
    helper = _HELPER.get("helper")
    serializer = _HELPER.get("serializer")
    if helper is not None and serializer is not None:
        return helper, serializer
    path = Path(__file__).with_name(_HELPER_FILE)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BrowserUnavailable(
            f"Не найден файл помощника страницы {path}: {type(exc).__name__}. Переустановите Logos"
        ) from None
    source = (
        raw.replace("__LOGOS_REF_KINDS__", json.dumps([[prefix, selector] for prefix, selector in REF_KINDS]))
        .replace("__LOGOS_ATTRS__", json.dumps(_ATTRS))
        .replace("__LOGOS_REF_ATTR__", json.dumps(REF_ATTR))
    )
    parts = source.split(_SERIALIZER_MARK)
    if len(parts) != 3 or not parts[1].strip():
        raise BrowserUnavailable(
            f"Файл помощника страницы {path} повреждён (нет функции сериализации). Переустановите Logos"
        )
    _HELPER["helper"] = source
    _HELPER["serializer"] = parts[1].strip()
    return source, parts[1].strip()


def _context_gone(text: Any) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in _CONTEXT_GONE)


def _cdp_text(exc: BaseException) -> str:
    return str(getattr(exc, "cdp_message", "") or exc)


def _int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _reason(status: int | None, text: str) -> str:
    if text:
        return text
    if status is None:
        return ""
    try:
        return HTTPStatus(int(status)).phrase
    except ValueError:
        return ""


def _header_pairs(headers: Any) -> list[tuple[str, str]]:
    """CDP header objects join repeated headers with newlines; split them back."""
    pairs: list[tuple[str, str]] = []
    if isinstance(headers, dict):
        for name, value in headers.items():
            for part in str(value).split("\n"):
                pairs.append((str(name), part))
    return pairs


def _raw_header_pairs(params: dict) -> list[tuple[str, str]]:
    text = params.get("headersText")
    if isinstance(text, str) and text.strip():
        lines = text.replace("\r\n", "\n").split("\n")
        pairs: list[tuple[str, str]] = []
        for line in lines[1:]:
            name, colon, value = line.partition(":")
            if colon and name.strip():
                pairs.append((name.strip(), value.strip()))
        if pairs:
            return pairs
    return _header_pairs(params.get("headers"))


def _header(pairs: list[tuple[str, str]], name: str) -> str | None:
    wanted = name.lower()
    for key, value in pairs:
        if key.lower() == wanted:
            return value
    return None


def _headers_dict(pairs: Any) -> dict[str, str]:
    """Header pairs for Network.setExtraHTTPHeaders: later wins, an empty value removes."""
    merged: dict[str, tuple[str, str]] = {}
    for name, value in pairs or []:
        key = str(name).strip()
        if not key:
            continue
        if value is None or str(value) == "":
            merged.pop(key.lower(), None)
            continue
        merged[key.lower()] = (key, str(value))
    return {key: value for key, value in merged.values()}


def _charset_of(content_type: str | None) -> str | None:
    if not content_type:
        return None
    _mime, params = parse_content_type(content_type)
    label = params.get("charset")
    return normalize_charset(label) if label else None


def _strip_fragment(url: str) -> str:
    return str(url or "").split("#", 1)[0]


def _exception_text(details: Any) -> str:
    if not isinstance(details, dict):
        return "ошибка"
    exception = details.get("exception")
    if isinstance(exception, dict):
        text = exception.get("description")
        if not text and "value" in exception:
            text = str(exception.get("value"))
        if text:
            return str(text)
    return str(details.get("text") or "ошибка")


def _preview_value(item: dict) -> str:
    value = item.get("value")
    if item.get("type") == "string":
        return f"'{value}'"
    if value is not None:
        return str(value)
    return str(item.get("subtype") or item.get("type") or "")


def _remote_text(item: Any) -> str:
    """A console argument as Chrome's console shows it."""
    if not isinstance(item, dict):
        return str(item)
    if "value" in item:
        value = item["value"]
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if "unserializableValue" in item:
        return str(item["unserializableValue"])
    preview = item.get("preview")
    if isinstance(preview, dict) and isinstance(preview.get("properties"), list):
        properties = [prop for prop in preview["properties"] if isinstance(prop, dict)]
        tail = ", …" if preview.get("overflow") else ""
        if preview.get("subtype") == "array":
            return "[" + ", ".join(_preview_value(prop) for prop in properties) + tail + "]"
        inner = ", ".join(f"{prop.get('name')}: {_preview_value(prop)}" for prop in properties)
        return "{" + inner + tail + "}"
    return str(item.get("description") or item.get("type") or "")


def _js_truthy(remote: dict) -> bool:
    """JavaScript truthiness of a RemoteObject."""
    kind = remote.get("type")
    if kind == "undefined":
        return False
    if kind == "object":
        return remote.get("subtype") != "null"
    if kind == "boolean":
        return bool(remote.get("value"))
    if kind == "number":
        if "unserializableValue" in remote:
            return str(remote["unserializableValue"]) not in ("NaN", "-0")
        return remote.get("value") not in (0, None)
    if kind == "string":
        return bool(remote.get("value"))
    if kind == "bigint":
        return str(remote.get("unserializableValue") or "0n") not in ("0n", "-0n")
    return True


def _image_size(data: bytes) -> tuple[int | None, int | None]:
    """Pixel size of a PNG (IHDR) or JPEG (SOF marker)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    if data[:2] == b"\xff\xd8":
        index = 2
        size = len(data)
        while index + 9 < size:
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in (0xD8, 0x01, 0xFF) or 0xD0 <= marker <= 0xD7:
                index += 2 if marker != 0xFF else 1
                continue
            length = struct.unpack(">H", data[index + 2 : index + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[index + 5 : index + 9])
                return int(width), int(height)
            index += 2 + length
    return None, None


def _pause(seconds: float, deadline: Deadline | None = None) -> None:
    wait = max(0.0, float(seconds))
    if deadline is not None:
        remaining = deadline.remaining()
        if remaining is not None:
            wait = min(wait, remaining)
    if wait > 0:
        time.sleep(wait)


def _ua_metadata(user_agent: str, *, branded: bool) -> tuple[dict[str, Any] | None, str | None]:
    """Client hints matching a Chrome User-Agent (no "HeadlessChrome" brand); and navigator.platform."""
    match = re.search(r"(?:HeadlessChrome|Chrome|CriOS)/(\d+)((?:\.\d+)*)", user_agent or "")
    if match is None:
        return None, None
    major = match.group(1)
    parts = (major + (match.group(2) or "")).split(".")
    while len(parts) < 4:
        parts.append("0")
    full = ".".join(parts[:4])
    brands = [{"brand": "Chromium", "version": major}]
    full_list = [{"brand": "Chromium", "version": full}]
    if branded:
        brands.append({"brand": "Google Chrome", "version": major})
        full_list.append({"brand": "Google Chrome", "version": full})
    brands.append({"brand": "Not.A/Brand", "version": "99"})
    full_list.append({"brand": "Not.A/Brand", "version": "99.0.0.0"})
    platform = ""
    navigator_platform: str | None = None
    platform_version = ""
    architecture = ""
    model = ""
    lowered = user_agent.lower()
    if "windows" in lowered:
        platform, navigator_platform, platform_version, architecture = "Windows", "Win32", "10.0.0", "x86"
    elif "android" in lowered:
        platform, navigator_platform = "Android", "Linux armv8l"
        version_match = re.search(r"Android (\d+(?:\.\d+)*)", user_agent)
        platform_version = version_match.group(1) if version_match else ""
        model_match = re.search(r"Android [\d.]+; ([^;)]+)\)", user_agent)
        model = model_match.group(1).strip() if model_match else ""
    elif "cros" in lowered:
        platform, navigator_platform, architecture = "Chrome OS", "Linux x86_64", "x86"
    elif "mac os x" in lowered or "macintosh" in lowered:
        platform, navigator_platform, architecture = "macOS", "MacIntel", "x86"
        mac_match = re.search(r"Mac OS X (\d+(?:[_.]\d+)*)", user_agent)
        platform_version = mac_match.group(1).replace("_", ".") if mac_match else ""
    elif "linux" in lowered or "x11" in lowered:
        arm = "aarch64" in lowered or "arm" in lowered
        platform = "Linux"
        navigator_platform = "Linux aarch64" if arm else "Linux x86_64"
        architecture = "arm" if arm else "x86"
    metadata = {
        "brands": brands,
        "fullVersionList": full_list,
        "fullVersion": full,
        "platform": platform,
        "platformVersion": platform_version,
        "architecture": architecture,
        "model": model,
        "mobile": "mobile" in lowered,
        "bitness": "64",
        "wow64": False,
    }
    return metadata, navigator_platform


# ---------------------------------------------------------------------------
# Keys


@dataclass(frozen=True)
class _Key:
    key: str
    code: str
    key_code: int
    text: str | None = None
    bit: int = 0


_MODIFIER_KEYS = {
    "alt": _Key("Alt", "AltLeft", 18, bit=1),
    "option": _Key("Alt", "AltLeft", 18, bit=1),
    "control": _Key("Control", "ControlLeft", 17, bit=2),
    "ctrl": _Key("Control", "ControlLeft", 17, bit=2),
    "meta": _Key("Meta", "MetaLeft", 91, bit=4),
    "cmd": _Key("Meta", "MetaLeft", 91, bit=4),
    "command": _Key("Meta", "MetaLeft", 91, bit=4),
    "shift": _Key("Shift", "ShiftLeft", 16, bit=8),
}
_NAMED_KEYS = {
    "enter": _Key("Enter", "Enter", 13, "\r"),
    "return": _Key("Enter", "Enter", 13, "\r"),
    "tab": _Key("Tab", "Tab", 9),
    "escape": _Key("Escape", "Escape", 27),
    "esc": _Key("Escape", "Escape", 27),
    "backspace": _Key("Backspace", "Backspace", 8),
    "delete": _Key("Delete", "Delete", 46),
    "del": _Key("Delete", "Delete", 46),
    "arrowleft": _Key("ArrowLeft", "ArrowLeft", 37),
    "left": _Key("ArrowLeft", "ArrowLeft", 37),
    "arrowup": _Key("ArrowUp", "ArrowUp", 38),
    "up": _Key("ArrowUp", "ArrowUp", 38),
    "arrowright": _Key("ArrowRight", "ArrowRight", 39),
    "right": _Key("ArrowRight", "ArrowRight", 39),
    "arrowdown": _Key("ArrowDown", "ArrowDown", 40),
    "down": _Key("ArrowDown", "ArrowDown", 40),
    "home": _Key("Home", "Home", 36),
    "end": _Key("End", "End", 35),
    "pageup": _Key("PageUp", "PageUp", 33),
    "pagedown": _Key("PageDown", "PageDown", 34),
    "space": _Key(" ", "Space", 32, " "),
    "insert": _Key("Insert", "Insert", 45),
    **{f"f{number}": _Key(f"F{number}", f"F{number}", 111 + number) for number in range(1, 13)},
}


def _char_key(char: str) -> _Key:
    if char.isascii() and char.isalpha():
        return _Key(char, "Key" + char.upper(), ord(char.upper()), char)
    if char.isascii() and char.isdigit():
        return _Key(char, "Digit" + char, ord(char), char)
    return _Key(char, "", 0, char)


def _parse_keys(text: str) -> list[tuple[list[_Key], _Key]]:
    """Key combos: "Enter", "Control+A", "Shift+Tab"; several separated by spaces."""
    combos: list[tuple[list[_Key], _Key]] = []
    for token in str(text or "").split():
        if token == "+":
            parts = ["+"]
        elif token.endswith("++"):
            parts = token[:-2].split("+") + ["+"]
        else:
            parts = token.split("+")
        if any(part == "" for part in parts):
            raise BrowserError(E_UNKNOWN_KEY.format(k=token))
        modifiers: list[_Key] = []
        for part in parts[:-1]:
            modifier = _MODIFIER_KEYS.get(part.lower())
            if modifier is None:
                raise BrowserError(E_UNKNOWN_KEY.format(k=part))
            modifiers.append(modifier)
        last = parts[-1]
        key = _MODIFIER_KEYS.get(last.lower()) or _NAMED_KEYS.get(last.lower())
        if key is None:
            if len(last) != 1:
                raise BrowserError(E_UNKNOWN_KEY.format(k=last))
            key = _char_key(last)
        combos.append((modifiers, key))
    if not combos:
        raise BrowserError(E_UNKNOWN_KEY.format(k=text))
    return combos


# ---------------------------------------------------------------------------
# Tab state records


@dataclass(frozen=True)
class _Marks:
    """Counters before an action, to tell what the action caused."""

    nav: int = 0
    samedoc: int = 0
    request: int = 0
    started: int = 0
    stop: int = 0
    popups: int = 0
    downloads: int = 0


@dataclass
class _MainDoc:
    """The main-frame document request of one loader (navigation)."""

    loader_id: str
    url: str = ""
    method: str = "GET"
    status: int | None = None
    status_text: str = ""
    mime: str = ""
    content_type: str = ""
    protocol: str | None = None
    remote: str | None = None
    request_headers: list[tuple[str, str]] = field(default_factory=list)
    response_headers: list[tuple[str, str]] = field(default_factory=list)
    hops: list[Hop] = field(default_factory=list)
    started: float | None = None
    finished: float | None = None
    bytes: int | None = None
    failure: str | None = None
    kind: str = ""
    text: str | None = None
    text_charset: str = ""
    text_source: str = ""
    body_state: str = ""


@dataclass
class _NetEntry:
    id: str
    request_id: str
    method: str
    url: str
    plain_url: str
    type: str
    status: int | None = None
    mime: str = ""
    charset: str | None = None
    bytes: int | None = None
    blocked: bool = False
    failure: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "method": self.method,
            "url": display_url(self.url),
            "status": self.status,
            "type": self.type,
            "mime": self.mime,
            "bytes": self.bytes,
            "blocked": self.blocked,
            "failure": self.failure,
        }


@dataclass
class _Handle:
    """A resolved target: an element of the isolated world, or a viewport point."""

    object_id: str | None
    label: str
    notes: list[str] = field(default_factory=list)
    x: float | None = None
    y: float | None = None


# ---------------------------------------------------------------------------
# Tab engine


class ChromeTabEngine:
    """TabEngine of one Chrome page target."""

    name = "chrome"

    def __init__(
        self,
        browser: ChromeBrowser,
        target_id: str,
        session_id: str,
        *,
        opener_id: str | None = None,
        url: str = "",
    ) -> None:
        self._browser = browser
        self.target_id = target_id
        self.session_id = session_id
        self.opener_id = opener_id or None
        self.tab_id = ""
        self.lost = False
        self.lost_reason: str | None = None
        self.setup_error: str | None = None
        self.ready = False
        self.own_navigation = False
        self._closed = False
        self._frame_id = target_id
        self._loader: str | None = None
        self._url = url or ""
        self._title = ""
        # A popup arrives with content; a tab Logos opens is empty until its first load.
        self._has_page = self.opener_id is not None
        self._initial_blank = self.opener_id is None
        self._nav_seq = 0
        self._samedoc_seq = 0
        self._request_seq = 0
        self._started_seq = 0
        self._stop_seq = 0
        self._stopped_after = 0
        self._loading = False
        self._lifecycle: dict[str, set[str]] = {}
        self._commits: dict[str, int] = {}
        self._docs: dict[str, _MainDoc] = {}
        self._net: list[_NetEntry] = []
        self._net_by_request: dict[str, _NetEntry] = {}
        self._net_counter = 0
        self._net_document: str | None = None
        self._inflight: set[str] = set()
        self._last_activity = time.monotonic()
        self._extra_request: dict[str, list[tuple[str, str]]] = {}
        self._extra_response: dict[str, list[tuple[str, str]]] = {}
        self._console: list[dict[str, Any]] = []
        self._console_total = 0
        self._dialogs: list[str] = []
        self._blocked: list[str] = []
        self._blocked_urls: set[str] = set()
        self._nav_blocked: str | None = None
        self._notes: list[str] = []
        self._downloads: list[dict[str, Any]] = []
        self._active_downloads: set[str] = set()
        self._download_seq = 0
        self._world_context: int | None = None
        self._world_loader: str | None = None

    # -- basics

    @property
    def _cond(self) -> threading.Condition:
        return self._browser._cond

    def _display_url(self) -> str:
        with self._cond:
            return display_url(self._url)

    def _dead(self) -> bool:
        return self.lost or self._closed or not self._browser.alive()

    def _lost_error(self) -> BrowserError:
        if not self._browser.alive():
            return ChromeClosed(E_CHROME_DIED.format(exit=self._browser.exit_info()))
        return BrowserError(
            E_TAB_LOST.format(
                tab=self.tab_id or "?",
                reason=self.lost_reason or "вкладка закрыта",
                url=display_url(self._url) or "…",
            )
        )

    def _ensure_usable(self) -> None:
        if not self._browser.alive():
            raise ChromeClosed(E_CHROME_DIED.format(exit=self._browser.exit_info()))
        if self.lost or self._closed:
            raise self._lost_error()
        if self.setup_error:
            raise BrowserError(self.setup_error)

    def _call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        deadline: Deadline | None = None,
        *,
        what: str | None = None,
    ) -> dict[str, Any]:
        if self.lost or self._closed:
            raise self._lost_error()
        try:
            return self._browser.conn.call(
                method, params, session_id=self.session_id, deadline=deadline, what=what
            )
        except CdpError:
            if self.lost or self._closed:
                raise self._lost_error() from None
            raise

    def _send(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._browser.conn.send_nowait(method, params, session_id=self.session_id)

    def _marks(self) -> _Marks:
        with self._cond:
            return _Marks(
                nav=self._nav_seq,
                samedoc=self._samedoc_seq,
                request=self._request_seq,
                started=self._started_seq,
                stop=self._stop_seq,
                popups=self._browser._popup_seq,
                downloads=self._download_seq,
            )

    def _drain(self) -> tuple[list[str], list[str], list[str], list[dict[str, Any]]]:
        """Dialogs, blocked URLs, notes and finished downloads since the last drain."""
        with self._cond:
            dialogs, self._dialogs = self._dialogs, []
            blocked, self._blocked = self._blocked, []
            notes, self._notes = self._notes, []
            downloads, self._downloads = self._downloads, []
        return dialogs, blocked, notes, downloads

    # -- events (reader thread, browser lock held)

    def _reset_document(self, loader: str | None) -> None:
        """A new main-frame document: the network and console lists start over."""
        self._net = []
        self._net_by_request = {}
        self._net_counter = 0
        self._inflight = set()
        self._net_document = loader
        self._console = []
        self._extra_request = {key: value for key, value in self._extra_request.items() if key == loader}
        self._extra_response = {key: value for key, value in self._extra_response.items() if key == loader}
        keep = {loader, self._loader}
        for key in [key for key in self._docs if key not in keep]:
            del self._docs[key]
        for key in [key for key in self._lifecycle if key not in keep]:
            del self._lifecycle[key]
        self._last_activity = time.monotonic()

    def _on_event(self, method: str, params: dict) -> None:
        if method.startswith("Network."):
            self._on_network(method, params)
        elif method == "Page.frameNavigated":
            self._on_frame_navigated(params)
        elif method == "Page.lifecycleEvent":
            if params.get("frameId") == self._frame_id:
                loader = params.get("loaderId")
                name = params.get("name")
                if isinstance(loader, str) and isinstance(name, str):
                    events = self._lifecycle.setdefault(loader, set())
                    if name == "init":
                        events.clear()
                    events.add(name)
        elif method == "Page.navigatedWithinDocument":
            if params.get("frameId") == self._frame_id:
                self._url = str(params.get("url") or self._url)
                self._samedoc_seq += 1
        elif method == "Page.frameRequestedNavigation":
            if params.get("frameId") == self._frame_id and params.get("disposition", "currentTab") == "currentTab":
                self._request_seq += 1
        elif method == "Page.frameStartedLoading":
            if params.get("frameId") == self._frame_id:
                self._loading = True
                self._started_seq += 1
        elif method == "Page.frameStoppedLoading":
            if params.get("frameId") == self._frame_id:
                self._loading = False
                self._stop_seq += 1
                self._stopped_after = self._started_seq
        elif method == "Runtime.consoleAPICalled":
            kind = str(params.get("type") or "log")
            level = "error" if kind == "assert" else kind
            text = " ".join(_remote_text(arg) for arg in params.get("args") or [])
            frames = (params.get("stackTrace") or {}).get("callFrames") or []
            top = frames[0] if frames and isinstance(frames[0], dict) else {}
            line = _int(top.get("lineNumber"))
            self._add_console(level, text, top.get("url"), line + 1 if line is not None else None)
        elif method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails") or {}
            line = _int(details.get("lineNumber"))
            self._add_console(
                "error", _exception_text(details), details.get("url"), line + 1 if line is not None else None
            )
        elif method == "Log.entryAdded":
            entry = params.get("entry") or {}
            source = str(entry.get("source") or "")
            text = str(entry.get("text") or "")
            if source and source not in ("javascript", "console-api", "other"):
                text = f"{source}: {text}"
            line = _int(entry.get("lineNumber"))
            self._add_console(
                str(entry.get("level") or "info"), text, entry.get("url"), line + 1 if line is not None else None
            )
        elif method == "Runtime.executionContextDestroyed":
            if params.get("executionContextId") == self._world_context:
                self._world_context = None
        elif method == "Runtime.executionContextsCleared":
            self._world_context = None

    def _add_console(self, level: str, text: str, url: Any, line: int | None) -> None:
        self._console_total += 1
        self._console.append(
            {
                "level": level,
                "text": text,
                "url": str(url) if url else None,
                "line": line,
                "seq": self._console_total,
            }
        )

    def _on_frame_navigated(self, params: dict) -> None:
        frame = params.get("frame") or {}
        if not isinstance(frame, dict) or frame.get("parentId"):
            return
        frame_id = frame.get("id")
        if isinstance(frame_id, str) and frame_id:
            self._frame_id = frame_id
        self._url = str(frame.get("url") or "") + str(frame.get("urlFragment") or "")
        loader = frame.get("loaderId")
        if isinstance(loader, str) and loader and loader != self._loader:
            self._loader = loader
            self._nav_seq += 1
            self._commits[loader] = self._nav_seq
            for old in [key for key, seq in self._commits.items() if seq < self._nav_seq - 16]:
                del self._commits[old]
            if loader != self._net_document:
                # A document without a request of its own (back/forward cache, about:blank).
                self._reset_document(loader)
        if isinstance(loader, str) and params.get("type") == "BackForwardCacheRestore":
            # A page restored from the back/forward cache fires no lifecycle events again.
            self._lifecycle.setdefault(loader, set()).update(_ALL_LIFECYCLE)
        self._has_page = True
        self._world_context = None

    def _on_network(self, method: str, params: dict) -> None:
        request_id = params.get("requestId")
        if not isinstance(request_id, str):
            return
        if method == "Network.requestWillBeSent":
            self._on_request(request_id, params)
        elif method == "Network.requestWillBeSentExtraInfo":
            if request_id in self._docs or request_id not in self._net_by_request:
                headers = _header_pairs(params.get("headers"))
                self._extra_request[request_id] = headers
                doc = self._docs.get(request_id)
                if doc is not None and headers:
                    doc.request_headers = headers
        elif method == "Network.responseReceived":
            self._on_response(request_id, params)
        elif method == "Network.responseReceivedExtraInfo":
            if request_id in self._docs or request_id not in self._net_by_request:
                headers = _raw_header_pairs(params)
                self._extra_response[request_id] = headers
                doc = self._docs.get(request_id)
                if doc is not None and headers:
                    doc.response_headers = headers
                    doc.content_type = _header(headers, "content-type") or doc.content_type
        elif method == "Network.loadingFinished":
            entry = self._net_by_request.get(request_id)
            size = _int(params.get("encodedDataLength"))
            if entry is not None:
                entry.bytes = size
            self._inflight.discard(request_id)
            self._last_activity = time.monotonic()
            doc = self._docs.get(request_id)
            if doc is not None:
                doc.bytes = size
                doc.finished = _float(params.get("timestamp"))
        elif method == "Network.loadingFailed":
            entry = self._net_by_request.get(request_id)
            error = str(params.get("errorText") or "ошибка")
            if params.get("canceled"):
                error += " (отменён)"
            if entry is not None:
                entry.failure = error
                if entry.plain_url in self._blocked_urls:
                    entry.blocked = True
            self._inflight.discard(request_id)
            self._last_activity = time.monotonic()
            doc = self._docs.get(request_id)
            if doc is not None:
                doc.failure = error
        elif method == "Network.webSocketCreated":
            url = str(params.get("url") or "")
            self._net_counter += 1
            entry = _NetEntry(
                id=f"N{self._net_counter}",
                request_id=request_id,
                method="GET",
                url=url,
                plain_url=_strip_fragment(url),
                type="websocket",
            )
            self._net.append(entry)
            self._net_by_request[request_id] = entry

    def _on_request(self, request_id: str, params: dict) -> None:
        request = params.get("request") or {}
        url = str(request.get("url") or "") + str(request.get("urlFragment") or "")
        method = str(request.get("method") or "GET")
        kind = str(params.get("type") or "Other").lower()
        main = kind == "document" and params.get("frameId") == self._frame_id and request_id == params.get("loaderId")
        redirect = params.get("redirectResponse")
        if main and not isinstance(redirect, dict) and request_id != self._net_document:
            self._reset_document(request_id)
        if isinstance(redirect, dict):
            previous = self._net_by_request.get(request_id)
            if previous is not None:
                previous.status = _int(redirect.get("status"))
                previous.mime = str(redirect.get("mimeType") or "")
            doc = self._docs.get(request_id) if main else None
            if doc is not None:
                doc.hops.append(
                    Hop(
                        status=_int(redirect.get("status")) or "?",
                        url=display_url(str(redirect.get("url") or doc.url)),
                        method=doc.method,
                    )
                )
                doc.url = url
                doc.method = method
                doc.request_headers = _header_pairs(request.get("headers"))
                doc.response_headers = []
        self._net_counter += 1
        entry = _NetEntry(
            id=f"N{self._net_counter}",
            request_id=request_id,
            method=method,
            url=url,
            plain_url=_strip_fragment(url),
            type=kind,
        )
        self._net.append(entry)
        self._net_by_request[request_id] = entry
        self._inflight.add(request_id)
        self._last_activity = time.monotonic()
        if main and not isinstance(redirect, dict):
            doc = _MainDoc(
                loader_id=request_id,
                url=url,
                method=method,
                started=_float(params.get("timestamp")),
                request_headers=self._extra_request.get(request_id) or _header_pairs(request.get("headers")),
            )
            self._docs[request_id] = doc

    def _on_response(self, request_id: str, params: dict) -> None:
        response = params.get("response") or {}
        mime = str(response.get("mimeType") or "")
        entry = self._net_by_request.get(request_id)
        headers = _header_pairs(response.get("headers"))
        if entry is not None:
            entry.status = _int(response.get("status"))
            entry.mime = mime
            entry.charset = normalize_charset(response.get("charset")) if response.get("charset") else None
            entry.charset = entry.charset or _charset_of(_header(headers, "content-type"))
        self._last_activity = time.monotonic()
        doc = self._docs.get(request_id)
        if doc is None:
            return
        doc.status = _int(response.get("status"))
        doc.status_text = str(response.get("statusText") or "")
        doc.mime = mime
        protocol = response.get("protocol")
        doc.protocol = str(protocol) if protocol else None
        address = response.get("remoteIPAddress")
        if address:
            host = f"[{address}]" if ":" in str(address) else str(address)
            port = response.get("remotePort")
            doc.remote = f"{host}:{port}" if port else host
        extra = self._extra_response.get(request_id)
        doc.response_headers = extra or headers
        doc.content_type = _header(doc.response_headers, "content-type") or mime
        if not self._extra_request.get(request_id):
            request_headers = _header_pairs(response.get("requestHeaders"))
            if request_headers:
                doc.request_headers = request_headers

    # -- documents and snapshots

    def has_page(self) -> bool:
        return self._has_page and not self.lost and not self._closed

    def _current_doc(self) -> tuple[_MainDoc | None, str]:
        with self._cond:
            loader = self._loader
            doc = self._docs.get(loader) if loader else None
            return doc, self._url

    @staticmethod
    def _doc_kind(doc: _MainDoc | None) -> str:
        if doc is None or not doc.mime:
            return "html"
        if doc.kind:
            return doc.kind
        base = doc.mime.split(";", 1)[0].strip().lower()
        if base in GENERIC_MIMES:
            return "generic"
        return classify(base, None)

    def _doc_charset(self, doc: _MainDoc | None, fallback: str | None) -> tuple[str, str]:
        if doc is not None:
            charset = _charset_of(doc.content_type)
            if charset:
                return charset, "header"
        label = normalize_charset(fallback) if fallback else None
        return (label or "utf-8"), "chrome"

    def _headers_info(self, doc: _MainDoc, charset: str | None, source: str | None) -> HeadersInfo:
        elapsed = None
        if doc.started is not None and doc.finished is not None and doc.finished >= doc.started:
            elapsed = int(round((doc.finished - doc.started) * 1000))
        return HeadersInfo(
            method=doc.method,
            url=doc.url,
            status=doc.status,
            reason=_reason(doc.status, doc.status_text),
            http_version=doc.protocol,
            request_headers=mask_headers(doc.request_headers),
            response_headers=mask_headers(doc.response_headers),
            redirects=list(doc.hops),
            mime=doc.mime,
            charset=charset,
            charset_source=source,
            bytes=doc.bytes,
            elapsed_ms=elapsed,
            remote_address=doc.remote,
        )

    def _load_body(self, doc: _MainDoc, kind: str) -> None:
        """Fetch a non-HTML main document's body (text, JSON, XML or a sniffed generic type)."""
        try:
            result = self._call("Network.getResponseBody", {"requestId": doc.loader_id})
        except CdpError as exc:
            logger.debug("main body of tab %s unavailable: %s", self.tab_id, type(exc).__name__)
            doc.body_state = "failed"
            return
        body = result.get("body") or ""
        if result.get("base64Encoded"):
            try:
                raw = base64.b64decode(body)
            except (binascii.Error, ValueError):
                doc.body_state = "failed"
                return
            if kind == "generic":
                kind = classify(doc.mime, raw[:512])
            if kind == "binary":
                doc.kind = "binary"
                doc.body_state = "loaded"
                return
            text, charset, source, _had_errors = decode_body(raw, doc.content_type, kind)
        else:
            text = str(body)
            if kind == "generic":
                kind = classify(doc.mime, text[:512].encode("utf-8", "surrogatepass"))
            charset = _charset_of(doc.content_type) or "utf-8"
            source = "chrome"
        doc.kind = kind
        doc.text = text
        doc.text_charset = charset
        doc.text_source = source
        doc.body_state = "loaded"

    def _file_snapshot(self, doc: _MainDoc, kind: str, url: str) -> PageSnapshot | None:
        """A snapshot of a non-HTML main document; None when Chrome's rendering is used."""
        if kind != "binary" and doc.body_state == "":
            self._load_body(doc, kind)
            kind = doc.kind or kind
        if kind == "binary" or doc.kind == "binary":
            disposition = _header(doc.response_headers, "content-disposition")
            name, _source = filename_from_response(disposition, doc.url, doc.content_type)
            length = _int(_header(doc.response_headers, "content-length"))
            info = BinaryInfo(
                mime=doc.mime or "application/octet-stream",
                length=length if length is not None else doc.bytes,
                filename=name,
                disposition=disposition,
            )
            charset, source = self._doc_charset(doc, None)
            return PageSnapshot(
                engine="chrome",
                tab=self.tab_id,
                url=url or doc.url,
                status=doc.status,
                reason=_reason(doc.status, doc.status_text),
                method=doc.method,
                mime=doc.mime,
                content_type=doc.content_type or doc.mime,
                kind="binary",
                charset=charset,
                charset_source=source,
                binary=info,
                headers=self._headers_info(doc, charset, source),
                stamped=False,
                version=content_version(None, None, url or doc.url),
                bytes=doc.bytes,
            )
        if doc.body_state != "loaded" or doc.kind == "html" or doc.text is None:
            return None
        return PageSnapshot(
            engine="chrome",
            tab=self.tab_id,
            url=url or doc.url,
            title_hint=self._title,
            status=doc.status,
            reason=_reason(doc.status, doc.status_text),
            method=doc.method,
            mime=doc.mime,
            content_type=doc.content_type or doc.mime,
            kind=doc.kind or "text",
            charset=doc.text_charset or "utf-8",
            charset_source=doc.text_source,
            text=doc.text,
            headers=self._headers_info(doc, doc.text_charset, doc.text_source),
            stamped=False,
            version=content_version(None, doc.text, url or doc.url),
            bytes=doc.bytes,
        )

    def page(self) -> ViewPage:
        self._ensure_usable()
        if not self._has_page:
            raise BrowserError(E_TAB_EMPTY.format(tab=self.tab_id or "?"))
        doc, url = self._current_doc()
        kind = self._doc_kind(doc)
        if doc is not None and kind != "html":
            snapshot = self._file_snapshot(doc, kind, url)
            if snapshot is not None:
                return ViewPage(snapshot)
        data = self._isolated_value("window.__logos.stamp()")
        if not isinstance(data, dict):
            data = {}
        html = str(data.get("html") or "")
        final_url = str(data.get("url") or url or "about:blank")
        title = str(data.get("title") or "")
        with self._cond:
            self._title = title
        charset, source = self._doc_charset(doc, data.get("charset"))
        scroll = data.get("scroll")
        snapshot = PageSnapshot(
            engine="chrome",
            tab=self.tab_id,
            url=final_url,
            title_hint=title,
            status=doc.status if doc is not None else None,
            reason=_reason(doc.status, doc.status_text) if doc is not None else "",
            method=doc.method if doc is not None else "GET",
            mime=(doc.mime if doc is not None and doc.mime else "text/html"),
            content_type=(doc.content_type if doc is not None and doc.content_type else "text/html"),
            kind="html",
            charset=charset,
            charset_source=source,
            html=html,
            headers=self._headers_info(doc, charset, source) if doc is not None else None,
            stamped=True,
            focused_ref=str(data.get("focused")) if data.get("focused") else None,
            version=content_version(html, None, final_url),
            scroll=scroll if isinstance(scroll, dict) else None,
            bytes=doc.bytes if doc is not None else None,
        )
        return ViewPage(snapshot)

    # -- isolated world

    def _world(self, deadline: Deadline | None) -> int:
        with self._cond:
            if self._world_context is not None and self._world_loader == self._loader:
                return self._world_context
            loader = self._loader
            frame = self._frame_id
        helper, _serializer = _helper_sources()
        created = self._call(
            "Page.createIsolatedWorld", {"frameId": frame, "worldName": _WORLD_NAME}, deadline
        )
        context = created.get("executionContextId")
        if not isinstance(context, int):
            raise CdpError("Chrome не создал изолированный мир для помощника страницы", method="Page.createIsolatedWorld")
        installed = self._call(
            "Runtime.evaluate",
            {"expression": helper, "contextId": context, "returnByValue": True, "silent": True},
            deadline,
        )
        details = installed.get("exceptionDetails")
        if details:
            text = _exception_text(details)
            if _context_gone(text):
                raise CdpError(f"Chrome: {text}", method="Runtime.evaluate", cdp_message=text)
            raise BrowserError(f"Не удалось подготовить помощник страницы Logos: {text}")
        with self._cond:
            self._world_context = context
            self._world_loader = loader
        return context

    def _forget_world(self, context: int | None) -> None:
        with self._cond:
            if context is None or self._world_context == context:
                self._world_context = None

    def _isolated(
        self,
        expression: str,
        *,
        by_value: bool = True,
        await_promise: bool = False,
        deadline: Deadline | None = None,
    ) -> dict[str, Any]:
        """Evaluate in the helper's isolated world; one retry on a new document."""
        for attempt in (0, 1):
            context = self._world(deadline)
            try:
                result = self._call(
                    "Runtime.evaluate",
                    {
                        "expression": expression,
                        "contextId": context,
                        "returnByValue": by_value,
                        "awaitPromise": await_promise,
                        "silent": True,
                        "objectGroup": _ACTION_GROUP,
                    },
                    deadline,
                )
            except CdpError as exc:
                if attempt == 0 and _context_gone(_cdp_text(exc)):
                    self._forget_world(context)
                    continue
                raise
            details = result.get("exceptionDetails")
            if details:
                text = _exception_text(details)
                if attempt == 0 and _context_gone(text):
                    self._forget_world(context)
                    continue
                raise BrowserError(f"Помощник страницы Logos не сработал: {text}")
            remote = result.get("result")
            return remote if isinstance(remote, dict) else {}
        raise BrowserError("Страница сменилась во время действия; повторите его (метки обновились)")

    def _isolated_value(self, expression: str, **kwargs: Any) -> Any:
        return self._isolated(expression, by_value=True, **kwargs).get("value")

    def _on_element(
        self,
        object_id: str,
        declaration: str,
        *args: Any,
        by_value: bool = True,
        deadline: Deadline | None = None,
    ) -> dict[str, Any]:
        """Call a function with ``this`` = the element, in the element's (isolated) world."""
        try:
            result = self._call(
                "Runtime.callFunctionOn",
                {
                    "functionDeclaration": declaration,
                    "objectId": object_id,
                    "arguments": [{"value": value} for value in args],
                    "returnByValue": by_value,
                    "silent": True,
                    "objectGroup": _ACTION_GROUP,
                },
                deadline,
            )
        except CdpError as exc:
            if _context_gone(_cdp_text(exc)):
                raise BrowserError(
                    "Страница сменилась во время действия; посмотрите её заново (browser_read) — метки обновились"
                ) from None
            raise
        details = result.get("exceptionDetails")
        if details:
            raise BrowserError(f"Помощник страницы Logos не сработал: {_exception_text(details)}")
        remote = result.get("result")
        return remote if isinstance(remote, dict) else {}

    def _element_value(self, object_id: str, declaration: str, *args: Any, deadline: Deadline | None = None) -> Any:
        return self._on_element(object_id, declaration, *args, by_value=True, deadline=deadline).get("value")

    @staticmethod
    def _object_id(remote: dict[str, Any]) -> str | None:
        if remote.get("type") != "object" or remote.get("subtype") == "null":
            return None
        object_id = remote.get("objectId")
        return str(object_id) if object_id else None

    def _find(self, ref: str, deadline: Deadline | None) -> str | None:
        remote = self._isolated(f"window.__logos.find({json.dumps(ref)})", by_value=False, deadline=deadline)
        return self._object_id(remote)

    def _resolve(self, target: ElementTarget | None, prefixes: str, deadline: Deadline | None) -> _Handle:
        """The element (or point) a target names in the live page."""
        if target is None or target.is_empty():
            raise BrowserError(E_TARGET_REQUIRED)
        notes: list[str] = []
        if target.ref:
            ref = normalize_ref(target.ref)
            object_id = self._find(ref, deadline)
            if object_id is None:
                raise BrowserNotFound(E_REF_NOT_FOUND.format(ref=ref, url=self._display_url()))
            return _Handle(object_id, ref, notes)
        if target.selector:
            selector = target.selector
            info = self._isolated_value(f"window.__logos.count({json.dumps(selector)})", deadline=deadline)
            info = info if isinstance(info, dict) else {}
            if info.get("error") is not None:
                raise BrowserError(E_SELECTOR_SYNTAX.format(sel=selector, reason=info["error"], pos="—"))
            count = _int(info.get("count")) or 0
            if count == 0:
                raise BrowserNotFound(E_SELECTOR_NONE.format(sel=selector))
            nth = target.nth
            if nth is not None and not 1 <= nth <= count:
                raise BrowserNotFound(
                    E_NTH_RANGE.format(nth=nth, target=f"селектор «{selector}»", count=count)
                )
            if nth is None and count > 1:
                notes.append(
                    f"селектор нашёл {count} {plural(count, 'элемент', 'элемента', 'элементов')}, "
                    "использован первый (уточните nth)"
                )
            index = (nth or 1) - 1
            remote = self._isolated(
                f"window.__logos.nth({json.dumps(selector)}, {index})", by_value=False, deadline=deadline
            )
            object_id = self._object_id(remote)
            if object_id is None:
                raise BrowserNotFound(E_SELECTOR_NONE.format(sel=selector))
            ref = self._element_value(object_id, _FN_REF, deadline=deadline)
            return _Handle(object_id, str(ref) if ref else f"«{selector}»", notes)
        if target.text:
            page = self.page()
            model = page.model
            if model is None:
                raise BrowserError(
                    f"Во вкладке {self.tab_id or '?'} открыт файл, а не страница: элементов в нём нет"
                )
            element, found_notes = model.resolve_target(ElementTarget(text=target.text, nth=target.nth), prefixes)
            ref = model.ref_of(element)
            object_id = self._find(ref, deadline) if ref else None
            if object_id is None:
                raise BrowserNotFound(E_TEXT_NOT_FOUND.format(text=target.text))
            return _Handle(object_id, str(ref), list(found_notes))
        if target.x is not None and target.y is not None:
            return _Handle(None, target.describe(), notes, x=float(target.x), y=float(target.y))
        raise BrowserError(E_TARGET_REQUIRED)

    def _main_world_object(self, object_id: str, deadline: Deadline | None) -> str:
        """The same DOM node as an object of the page's main world."""
        described = self._call("DOM.describeNode", {"objectId": object_id}, deadline)
        backend = (described.get("node") or {}).get("backendNodeId")
        if backend is None:
            raise BrowserError("Chrome не описал элемент; посмотрите страницу заново (browser_read)")
        resolved = self._call(
            "DOM.resolveNode", {"backendNodeId": backend, "objectGroup": _EVAL_GROUP}, deadline
        )
        main_id = (resolved.get("object") or {}).get("objectId")
        if not main_id:
            raise BrowserError("Chrome не передал элемент странице; посмотрите её заново (browser_read)")
        return str(main_id)

    # -- input

    def _mouse_click(self, x: float, y: float, deadline: Deadline | None, modifiers: int = 0) -> None:
        base = {"x": float(x), "y": float(y), "modifiers": modifiers}
        self._call("Input.dispatchMouseEvent", {**base, "type": "mouseMoved", "button": "none", "buttons": 0}, deadline)
        self._call(
            "Input.dispatchMouseEvent",
            {**base, "type": "mousePressed", "button": "left", "buttons": 1, "clickCount": 1},
            deadline,
        )
        self._call(
            "Input.dispatchMouseEvent",
            {**base, "type": "mouseReleased", "button": "left", "buttons": 0, "clickCount": 1},
            deadline,
        )

    def _point(self, handle: _Handle, deadline: Deadline | None) -> dict[str, Any]:
        assert handle.object_id is not None
        where = self._element_value(handle.object_id, _FN_POINT, deadline=deadline)
        return where if isinstance(where, dict) else {}

    def _raise_unreachable(self, handle: _Handle, where: dict[str, Any]) -> None:
        if where.get("reason") == "covered":
            raise BrowserError(
                E_COVERED.format(
                    ref=handle.label,
                    cover=where.get("cover") or "?",
                    cover_ref=where.get("coverRef") or "…",
                )
            )
        raise BrowserError(E_NOT_VISIBLE.format(ref=handle.label))

    def _click_element(self, handle: _Handle, force: bool, deadline: Deadline | None) -> None:
        if handle.object_id is None:
            assert handle.x is not None and handle.y is not None
            self._mouse_click(handle.x, handle.y, deadline)
            return
        if force:
            self._on_element(handle.object_id, "function () { this.click(); return true; }", deadline=deadline)
            return
        where = self._point(handle, deadline)
        if not where.get("ok"):
            self._raise_unreachable(handle, where)
        self._mouse_click(float(where["x"]), float(where["y"]), deadline)

    def _key_event(self, params: dict[str, Any], deadline: Deadline | None) -> None:
        self._call("Input.dispatchKeyEvent", params, deadline)

    def _press_combo(self, modifiers: list[_Key], key: _Key, deadline: Deadline | None) -> None:
        mask = 0
        for modifier in modifiers:
            mask |= modifier.bit
            self._key_event(
                {
                    "type": "rawKeyDown",
                    "key": modifier.key,
                    "code": modifier.code,
                    "windowsVirtualKeyCode": modifier.key_code,
                    "modifiers": mask,
                },
                deadline,
            )
        shifted = bool(mask & 8)
        command_held = bool(mask & (1 | 2 | 4))
        key_name = key.key
        text = key.text
        if shifted and text and len(text) == 1 and text.isalpha():
            key_name = text = text.upper()
        params: dict[str, Any] = {
            "type": "keyDown" if text and not command_held else "rawKeyDown",
            "key": key_name,
            "code": key.code,
            "windowsVirtualKeyCode": key.key_code,
            "nativeVirtualKeyCode": key.key_code,
            "modifiers": mask,
        }
        if text and not command_held:
            params["text"] = text
            params["unmodifiedText"] = text
        if mask & (2 | 4) and key.key.lower() == "a":
            params["commands"] = ["selectAll"]
        self._key_event(params, deadline)
        self._key_event(
            {
                "type": "keyUp",
                "key": key_name,
                "code": key.code,
                "windowsVirtualKeyCode": key.key_code,
                "nativeVirtualKeyCode": key.key_code,
                "modifiers": mask,
            },
            deadline,
        )
        for modifier in reversed(modifiers):
            mask &= ~modifier.bit
            self._key_event(
                {
                    "type": "keyUp",
                    "key": modifier.key,
                    "code": modifier.code,
                    "windowsVirtualKeyCode": modifier.key_code,
                    "modifiers": mask,
                },
                deadline,
            )

    def _type(self, handle: _Handle, value: str, replace: bool, notes: list[str], deadline: Deadline | None) -> list[str]:
        object_id = handle.object_id
        if object_id is None:
            raise BrowserError(E_TARGET_REQUIRED)
        prepared = self._element_value(
            object_id,
            "function (replace) { return window.__logos.prepareInput(this, replace); }",
            bool(replace),
            deadline=deadline,
        )
        prepared = prepared if isinstance(prepared, dict) else {}
        mode = prepared.get("mode")
        if mode == "select":
            return self._select(handle, [value], notes, deadline)
        if mode == "unsupported":
            raise BrowserError(
                f"Элемент {handle.label} не текстовое поле ({prepared.get('type') or '?'}): для флажков — "
                "action='check', для списков — select, для файлов — browser_fill_form с file_paths"
            )
        if mode == "setter":
            if prepared.get("blocked"):
                notes.append(
                    f"поле {handle.label} недоступно для ввода на странице (disabled/readonly) — "
                    "значение всё равно записано"
                )
            self._element_value(
                object_id, "function (value) { return window.__logos.setValue(this, value); }", value, deadline=deadline
            )
            return []
        if value:
            self._call("Input.insertText", {"text": value}, deadline)
        elif replace:
            self._press_combo([], _NAMED_KEYS["backspace"], deadline)
        if prepared.get("type") == "contenteditable":
            return []
        current = self._element_value(object_id, "function () { return window.__logos.valueOf(this); }", deadline=deadline)
        current = "" if current is None else str(current)
        if replace and current != value:
            if current == "" or prepared.get("type") in ("email", "number"):
                self._element_value(
                    object_id,
                    "function (value) { return window.__logos.setValue(this, value); }",
                    value,
                    deadline=deadline,
                )
                notes.append(f"поле {handle.label} не приняло ввод с клавиатуры — значение записано напрямую")
            else:
                notes.append(
                    f"поле {handle.label} приняло не весь текст: в нём {len(current)} симв. из {len(value)} "
                    "(ограничение страницы, например maxlength)"
                )
        return []

    def _select(self, handle: _Handle, values: list[str], notes: list[str], deadline: Deadline | None) -> list[str]:
        object_id = handle.object_id
        if object_id is None:
            raise BrowserError(E_TARGET_REQUIRED)
        result = self._element_value(
            object_id,
            "function (values) { return window.__logos.selectOptions(this, values); }",
            [str(value) for value in values],
            deadline=deadline,
        )
        result = result if isinstance(result, dict) else {}
        if result.get("error") == "not_select":
            raise BrowserError(
                f"Элемент {handle.label} — не список <select>; нажмите его (browser_click) и выберите "
                "вариант нажатием"
            )
        if not result.get("ok"):
            options = result.get("options") or []
            raise BrowserNotFound(
                E_OPTION_NOT_FOUND.format(
                    ref=handle.label,
                    value=result.get("missing") or "",
                    options=", ".join(str(option) for option in options) or "нет вариантов",
                )
            )
        if result.get("disabled"):
            notes.append(
                f"поле {handle.label} недоступно для ввода на странице (disabled/readonly) — "
                "значение всё равно записано"
            )
        return [str(item) for item in result.get("selected") or []]

    def _toggle(self, handle: _Handle, want: bool, force: bool, notes: list[str], deadline: Deadline | None) -> bool:
        """Check or uncheck; returns whether something was clicked."""
        object_id = handle.object_id
        if object_id is None:
            raise BrowserError(E_TARGET_REQUIRED)
        state = self._element_value(object_id, "function () { return window.__logos.checkState(this); }", deadline=deadline)
        state = state if isinstance(state, dict) else {}
        kind = state.get("kind")
        if kind == "none":
            raise BrowserError(f"Элемент {handle.label} не флажок и не переключатель")
        if bool(state.get("checked")) == want:
            notes.append(f"{handle.label} уже {'отмечен' if want else 'снят'}")
            return False
        if not want and kind in ("radio", "menuitemradio"):
            notes.append(f"Переключатель {handle.label} нельзя снять щелчком — выберите другой вариант группы")
            return False
        if force:
            self._on_element(object_id, "function () { this.click(); return true; }", deadline=deadline)
        else:
            where = self._point(handle, deadline)
            if where.get("ok"):
                self._mouse_click(float(where["x"]), float(where["y"]), deadline)
            else:
                label = self._object_id(
                    self._on_element(
                        object_id,
                        "function () { return window.__logos.labelOf(this); }",
                        by_value=False,
                        deadline=deadline,
                    )
                )
                clicked = False
                if label is not None:
                    label_where = self._point(_Handle(label, handle.label), deadline)
                    if label_where.get("ok"):
                        self._mouse_click(float(label_where["x"]), float(label_where["y"]), deadline)
                        clicked = True
                if not clicked:
                    self._on_element(object_id, "function () { this.click(); return true; }", deadline=deadline)
                    notes.append(f"{handle.label} скрыт или перекрыт — нажат через JavaScript")
        after = self._element_value(object_id, "function () { return window.__logos.checkState(this); }", deadline=deadline)
        if isinstance(after, dict) and bool(after.get("checked")) != want:
            notes.append(f"{handle.label}: состояние не изменилось — страница не приняла нажатие")
        return True

    def _submit(self, action: Action, notes: list[str], deadline: Deadline | None) -> None:
        form_handle = self._resolve(action.target, "F", deadline)
        notes.extend(form_handle.notes)
        if form_handle.object_id is None:
            raise BrowserError(E_TARGET_REQUIRED)
        form_id = self._object_id(
            self._on_element(
                form_handle.object_id,
                "function () { return window.__logos.formOf(this); }",
                by_value=False,
                deadline=deadline,
            )
        )
        if form_id is None:
            raise BrowserError(
                f"{form_handle.label} не форма и не находится в форме; формы страницы: browser_read(view='forms')"
            )
        invalid = self._element_value(form_id, "function () { return window.__logos.invalidFields(this); }", deadline=deadline)
        if isinstance(invalid, list) and invalid:
            parts = []
            for item in invalid:
                if not isinstance(item, dict):
                    continue
                name = item.get("ref") or item.get("name") or "поле"
                parts.append(f"{name}: {item.get('message') or 'значение не подходит'}")
            notes.append("Браузер не отправит форму, пока поля заполнены неверно: " + "; ".join(parts))
        if action.submitter is not None and not action.submitter.is_empty():
            submitter = self._resolve(action.submitter, "B", deadline)
            notes.extend(submitter.notes)
            self._click_element(submitter, action.force, deadline)
            return
        candidate = self._object_id(
            self._on_element(
                form_id,
                "function () { return window.__logos.submitterOf(this); }",
                by_value=False,
                deadline=deadline,
            )
        )
        if candidate is not None:
            label = self._element_value(candidate, _FN_REF, deadline=deadline)
            handle = _Handle(candidate, str(label) if label else "кнопка отправки")
            where = self._point(handle, deadline)
            if where.get("ok"):
                self._mouse_click(float(where["x"]), float(where["y"]), deadline)
                return
            # The button is hidden or covered: submit with it as the submitter.
            self._on_element(
                candidate,
                "function () { return window.__logos.requestSubmit(window.__logos.formOf(this), this); }",
                deadline=deadline,
            )
            return
        self._on_element(form_id, "function () { return window.__logos.requestSubmit(this, null); }", deadline=deadline)

    def _perform_click(self, action: Action, notes: list[str], deadline: Deadline | None) -> Navigation | None:
        handle = self._resolve(action.target, "LBI", deadline)
        notes.extend(handle.notes)
        object_id = handle.object_id
        if object_id is not None and action.new_tab:
            link = self._element_value(object_id, "function () { return window.__logos.linkOf(this); }", deadline=deadline)
            if isinstance(link, str) and link and not link.lower().startswith("javascript:"):
                with self._cond:
                    referrer = self._url
                return Navigation(
                    url=link,
                    referrer=referrer or None,
                    initiator="link",
                    wait_until=action.wait_until,
                    deadline=deadline if deadline is not None else Deadline(None),
                    max_redirects=action.max_redirects,
                    max_bytes=action.max_bytes,
                )
            if self._element_value(object_id, "function () { return window.__logos.isSubmitter(this); }", deadline=deadline):
                previous = self._element_value(
                    object_id, "function () { return window.__logos.withTarget(this, '_blank'); }", deadline=deadline
                )
                try:
                    self._click_element(handle, action.force, deadline)
                finally:
                    try:
                        self._element_value(
                            object_id,
                            "function (previous) { window.__logos.restoreTarget(this, previous); return true; }",
                            previous,
                            deadline=deadline,
                        )
                    except BrowserError:
                        pass
                return None
            notes.append("new_tab действует на ссылки и кнопки отправки форм; выполнено обычное нажатие")
        self._click_element(handle, action.force, deadline)
        return None

    def perform(self, action: Action, ctx: EventSink) -> ActionOutcome:
        self._ensure_usable()
        deadline = action.deadline
        kind = action.kind
        notes: list[str] = []
        selected: list[str] = []
        marks = self._marks()
        started = time.monotonic()
        before_url = self._display_url()
        detect = kind in ("click", "press", "submit", "select", "check", "uncheck")
        self.own_navigation = True
        try:
            if kind == "click":
                navigation = self._perform_click(action, notes, deadline)
                if navigation is not None:
                    dialogs, blocked, more_notes, downloads = self._drain()
                    return ActionOutcome(
                        navigated=False,
                        new_tab_navigation=navigation,
                        dialogs=dialogs,
                        blocked=blocked,
                        downloads=downloads,
                        notes=notes + more_notes,
                    )
            elif kind in ("type", "set_value", "clear"):
                handle = self._resolve(action.target, "I", deadline)
                notes.extend(handle.notes)
                value = "" if kind == "clear" else (action.value or "")
                selected = self._type(handle, value, True if kind == "clear" else action.replace, notes, deadline)
            elif kind == "press":
                combos = _parse_keys(action.value or "")
                if action.target is not None and not action.target.is_empty():
                    handle = self._resolve(action.target, "LBI", deadline)
                    notes.extend(handle.notes)
                    if handle.object_id is not None:
                        focused = self._element_value(
                            handle.object_id, "function () { return window.__logos.focus(this); }", deadline=deadline
                        )
                        if not focused:
                            notes.append(f"{handle.label} не принял фокус — клавиши отправлены активному элементу")
                for modifiers, key in combos:
                    self._press_combo(modifiers, key, deadline)
            elif kind == "hover":
                handle = self._resolve(action.target, "LBI", deadline)
                notes.extend(handle.notes)
                if handle.object_id is None:
                    assert handle.x is not None and handle.y is not None
                    self._call(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseMoved", "x": handle.x, "y": handle.y, "button": "none", "buttons": 0},
                        deadline,
                    )
                elif action.force:
                    self._element_value(handle.object_id, "function () { return window.__logos.hover(this); }", deadline=deadline)
                else:
                    where = self._point(handle, deadline)
                    if not where.get("ok"):
                        self._raise_unreachable(handle, where)
                    self._call(
                        "Input.dispatchMouseEvent",
                        {
                            "type": "mouseMoved",
                            "x": float(where["x"]),
                            "y": float(where["y"]),
                            "button": "none",
                            "buttons": 0,
                        },
                        deadline,
                    )
            elif kind == "scroll":
                value = action.value
                if action.target is not None and not action.target.is_empty():
                    handle = self._resolve(action.target, "LBIFTM", deadline)
                    notes.extend(handle.notes)
                    if handle.object_id is None:
                        raise BrowserError(E_TARGET_REQUIRED)
                    result = self._element_value(
                        handle.object_id,
                        "function (value) { return window.__logos.scroll(this, value); }",
                        value,
                        deadline=deadline,
                    )
                else:
                    result = self._isolated_value(f"window.__logos.scroll(null, {json.dumps(value)})", deadline=deadline)
                result = result if isinstance(result, dict) else {}
                if result.get("error"):
                    raise BrowserError(
                        f"Не понял прокрутку «{value}»: укажите down, up, top, bottom, left, right или "
                        "число пикселей (например 800 или -300)"
                    )
                notes.append(
                    f"Прокрутка: позиция {result.get('y', 0)} из {result.get('height', 0)} пикс. "
                    f"(окно {result.get('viewport', 0)} пикс.)"
                )
            elif kind == "select":
                handle = self._resolve(action.target, "I", deadline)
                notes.extend(handle.notes)
                values = list(action.values) if action.values else ([action.value] if action.value is not None else [])
                selected = self._select(handle, values, notes, deadline)
            elif kind in ("check", "uncheck"):
                handle = self._resolve(action.target, "I", deadline)
                notes.extend(handle.notes)
                detect = self._toggle(handle, kind == "check", action.force, notes, deadline)
            elif kind == "focus":
                handle = self._resolve(action.target, "LBIFTM", deadline)
                notes.extend(handle.notes)
                if handle.object_id is None:
                    raise BrowserError(E_TARGET_REQUIRED)
                if not self._element_value(handle.object_id, "function () { return window.__logos.focus(this); }", deadline=deadline):
                    notes.append(f"{handle.label} не принимает фокус")
            elif kind == "set_files":
                handle = self._resolve(action.target, "I", deadline)
                notes.extend(handle.notes)
                if handle.object_id is None:
                    raise BrowserError(E_TARGET_REQUIRED)
                info = self._element_value(
                    handle.object_id,
                    "function () { return { tag: this.localName, type: String(this.type || '').toLowerCase() }; }",
                    deadline=deadline,
                )
                info = info if isinstance(info, dict) else {}
                if info.get("tag") != "input" or info.get("type") != "file":
                    raise BrowserError(f"Элемент {handle.label} не поле выбора файлов (input type=file)")
                self._call(
                    "DOM.setFileInputFiles",
                    {"files": [str(path) for path in action.values], "objectId": handle.object_id},
                    deadline,
                )
            elif kind == "submit":
                self._submit(action, notes, deadline)
            else:
                raise BrowserError(f"Действие «{kind}» движок chrome не выполняет")
            navigated, popups, wait_notes = self._settle(
                marks, detect=detect, wait_until=action.wait_until, deadline=deadline
            )
        except BrowserTimeout:
            self._send("Page.stopLoading")
            raise
        finally:
            self.own_navigation = False
            self._send("Runtime.releaseObjectGroup", {"objectGroup": _ACTION_GROUP})
        dialogs, blocked, more_notes, downloads = self._drain()
        if navigated:
            self._emit_navigation(ctx, before_url, started)
        return ActionOutcome(
            navigated=navigated,
            page=None,
            popups=popups,
            downloads=downloads,
            dialogs=dialogs,
            blocked=blocked,
            notes=notes + wait_notes + more_notes,
            selected=selected,
        )

    # -- navigation waits

    def _navigation_since(self, marks: _Marks) -> bool:
        return (
            self._nav_seq > marks.nav
            or self._samedoc_seq > marks.samedoc
            or self._request_seq > marks.request
            or self._started_seq > marks.started
        )

    def _activity_since(self, marks: _Marks) -> bool:
        return (
            self._navigation_since(marks)
            or self._browser._popup_seq > marks.popups
            or self._download_seq > marks.downloads
        )

    def _settle(
        self, marks: _Marks, *, detect: bool, wait_until: str | None, deadline: Deadline | None
    ) -> tuple[bool, list[ChromeTabEngine], list[str]]:
        """After an action: wait for a navigation it started, or let the page settle."""
        notes: list[str] = []
        if detect:
            end = time.monotonic() + _NAVIGATION_START_WINDOW
            with self._cond:
                while not self._activity_since(marks) and not self._dead():
                    left = end - time.monotonic()
                    remaining = deadline.remaining() if deadline is not None else None
                    if remaining is not None:
                        left = min(left, remaining)
                    if left <= 0:
                        break
                    self._cond.wait(left)
        with self._cond:
            started = self._navigation_since(marks)
        navigated = False
        if started:
            navigated = self._wait_navigation(marks, wait_until, deadline, "переход после действия")
        else:
            _pause(_SETTLE_DELAY, deadline)
        with self._cond:
            downloading = self._download_seq > marks.downloads
        if downloading:
            # The action started a download: the result reports the finished file.
            self._wait_download(marks, deadline, notes)
        popups = self._collect_popups(marks, wait_until, deadline, notes)
        return navigated, popups, notes

    def _wait_navigation(
        self, marks: _Marks, wait_until: str | None, deadline: Deadline | None, what: str
    ) -> bool:
        """Wait for the new document a navigation commits; False when none came."""
        began = time.monotonic()
        with self._cond:
            while True:
                if self._nav_seq > marks.nav:
                    break
                if self._samedoc_seq > marks.samedoc:
                    return True
                if self._download_seq > marks.downloads:
                    return False
                started = self._started_seq > marks.started
                if started and self._stopped_after > marks.started and not self._loading:
                    return False
                if self._dead():
                    raise self._lost_error()
                remaining = deadline.remaining() if deadline is not None else None
                timeout = remaining
                if not started:
                    window = _NAVIGATION_START_WINDOW - (time.monotonic() - began)
                    if window <= 0:
                        return False
                    timeout = window if timeout is None else min(timeout, window)
                if remaining is not None and remaining <= 0:
                    raise BrowserTimeout(timeout_message(deadline.seconds if deadline else None, what))
                self._cond.wait(timeout)
        self._wait_lifecycle(wait_until, deadline, what)
        return True

    def _wait_commit(self, loader: str, marks: _Marks, deadline: Deadline | None, what: str) -> None:
        with self._cond:
            while loader not in self._commits and self._nav_seq <= marks.nav:
                if self._dead():
                    raise self._lost_error()
                remaining = deadline.remaining() if deadline is not None else None
                if remaining is not None and remaining <= 0:
                    raise BrowserTimeout(timeout_message(deadline.seconds if deadline else None, what))
                self._cond.wait(remaining)

    def _wait_lifecycle(self, wait_until: str | None, deadline: Deadline | None, what: str) -> None:
        """Wait for the lifecycle event of the current document (following newer documents)."""
        name = _LIFECYCLE_NAMES.get(wait_until or "load")
        if name is None:
            return
        with self._cond:
            while True:
                loader = self._loader
                if loader is None or loader not in self._commits:
                    # A document that was there before the tab was prepared: its events were not seen.
                    return
                if name in self._lifecycle.get(loader, ()):
                    return
                if self._dead():
                    raise self._lost_error()
                remaining = deadline.remaining() if deadline is not None else None
                if remaining is not None and remaining <= 0:
                    raise BrowserTimeout(timeout_message(deadline.seconds if deadline else None, what))
                self._cond.wait(remaining)

    def _wait_first_load(self, wait_until: str | None, deadline: Deadline | None) -> None:
        """A popup: wait for its first document when it starts loading one."""
        end = time.monotonic() + _NAVIGATION_START_WINDOW
        what = "загрузка новой вкладки"
        with self._cond:
            while self._started_seq == 0 and self._request_seq == 0:
                if self._nav_seq > 0 and self._url not in ("", "about:blank"):
                    break
                left = end - time.monotonic()
                remaining = deadline.remaining() if deadline is not None else None
                if remaining is not None:
                    left = min(left, remaining)
                if left <= 0 or self._dead():
                    return
                self._cond.wait(left)
            while not (self._nav_seq > 0 and self._url not in ("", "about:blank")):
                if self._stop_seq > 0 and not self._loading and self._stopped_after >= self._started_seq:
                    break
                if self._dead():
                    raise self._lost_error()
                remaining = deadline.remaining() if deadline is not None else None
                if remaining is not None and remaining <= 0:
                    raise BrowserTimeout(timeout_message(deadline.seconds if deadline else None, what))
                self._cond.wait(remaining)
        self._wait_lifecycle(wait_until, deadline, what)

    def _collect_popups(
        self, marks: _Marks, wait_until: str | None, deadline: Deadline | None, notes: list[str]
    ) -> list[ChromeTabEngine]:
        browser = self._browser
        with self._cond:
            if browser._popup_seq == marks.popups:
                return []
            while browser._popups_pending > 0 and browser.alive():
                remaining = deadline.remaining() if deadline is not None else None
                if remaining is not None and remaining <= 0:
                    break
                self._cond.wait(remaining)
        popups = browser.take_popups()
        for popup in popups:
            try:
                popup._wait_first_load(wait_until, deadline)
            except BrowserTimeout:
                notes.append(f"Новая вкладка ещё загружается: {popup._display_url() or 'about:blank'}")
            except BrowserError as exc:
                notes.append(f"Новая вкладка: {exc}")
        return popups

    def _wait_download(self, marks: _Marks, deadline: Deadline | None, notes: list[str]) -> bool:
        """After ERR_ABORTED: did the navigation become a download? Waits for it to finish."""
        end = time.monotonic() + _NAVIGATION_START_WINDOW
        with self._cond:
            while self._download_seq == marks.downloads:
                left = end - time.monotonic()
                if left <= 0 or self._dead():
                    return False
                self._cond.wait(left)
            while self._active_downloads and not self._dead():
                remaining = deadline.remaining() if deadline is not None else None
                if remaining is not None and remaining <= 0:
                    names = [
                        str((self._browser._downloads.get(guid) or {}).get("suggested") or guid)
                        for guid in self._active_downloads
                    ]
                    notes.append(
                        "Загрузка ещё идёт (продолжится в фоне): "
                        + ", ".join(names)
                        + f"; папка загрузок {self._browser.downloads_dir}"
                    )
                    break
                self._cond.wait(remaining)
        return True

    def _emit_navigation(self, ctx: EventSink, from_url: str, started: float) -> None:
        doc, url = self._current_doc()
        ctx.event(
            "browser_navigation",
            {
                "tab": self.tab_id,
                "engine": "chrome",
                "method": doc.method if doc is not None else "GET",
                "url": from_url,
                "final_url": display_url(url),
                "status": doc.status if doc is not None else None,
                "redirects": len(doc.hops) if doc is not None else 0,
                "bytes": doc.bytes if doc is not None else None,
                "kind": self._doc_kind(doc),
                "elapsed_ms": int(round((time.monotonic() - started) * 1000)),
            },
        )

    def _nav_outcome(
        self,
        ctx: EventSink,
        *,
        from_url: str,
        marks: _Marks,
        started: float,
        wait_until: str | None,
        deadline: Deadline | None,
        navigated: bool,
        notes: list[str],
    ) -> NavOutcome:
        with self._cond:
            self._has_page = True
        page = self.page()
        popup_notes: list[str] = []
        popups = self._collect_popups(marks, wait_until, deadline, popup_notes)
        dialogs, blocked, more_notes, downloads = self._drain()
        status = page.snapshot.status
        all_notes = list(notes)
        if status is not None and status >= 400:
            all_notes.append(f"Сервер ответил {status} {page.snapshot.reason}".rstrip())
        if navigated:
            self._emit_navigation(ctx, from_url, started)
        return NavOutcome(
            page=page,
            navigated=navigated,
            blocked=blocked,
            downloads=downloads,
            popups=popups,
            dialogs=dialogs,
            notes=all_notes + popup_notes + more_notes,
        )

    # -- navigation

    def _push_headers(self, headers: list[tuple[str, str]], deadline: Deadline | None) -> bool:
        if not headers:
            return False
        merged = list(self._browser.options.extra_headers) + [(str(name), str(value)) for name, value in headers]
        self._call("Network.setExtraHTTPHeaders", {"headers": _headers_dict(merged)}, deadline)
        return True

    def _restore_headers(self) -> None:
        self._send(
            "Network.setExtraHTTPHeaders", {"headers": _headers_dict(self._browser.options.extra_headers)}
        )

    def _blocked_error(self, url: str, requested: str, ctx: EventSink) -> BrowserBlocked:
        host = guard.blocked_host(url)
        stage = "request" if _strip_fragment(url) == _strip_fragment(requested) else "redirect"
        ctx.event("browser_blocked", {"tab": self.tab_id, "host": host, "stage": stage, "count": 1})
        details = {"blocked_host": host, "blocked_stage": stage}
        if stage == "request":
            return BrowserBlocked(f"{LINK_LOCAL_MESSAGE}: {host}", details=details)
        return BrowserBlocked(E_REDIRECT_LINK_LOCAL.format(url=display_url(url)), details=details)

    def _navigation_error(self, url: str, error: str, ctx: EventSink) -> BrowserError:
        upper = error.upper()
        host = guard.blocked_host(url)
        if "ERR_BLOCKED_BY_CLIENT" in upper:
            with self._cond:
                blocked = self._nav_blocked
            if blocked:
                return self._blocked_error(blocked, url, ctx)
        if "ERR_NAME_NOT_RESOLVED" in upper or "ERR_NAME_RESOLUTION_FAILED" in upper:
            return BrowserNetworkError(E_DNS.format(host=host))
        if "ERR_CERT_" in upper or "ERR_SSL_" in upper or "ERR_BAD_SSL" in upper:
            return BrowserNetworkError(E_TLS.format(host=host, reason=error))
        if "ERR_PROXY_" in upper or "ERR_TUNNEL_CONNECTION_FAILED" in upper:
            proxy = display_url(self._browser.options.proxy) or "из окружения"
            return BrowserNetworkError(E_PROXY.format(proxy=proxy, reason=error))
        if (
            "ERR_CONNECTION_" in upper
            or "ERR_ADDRESS_" in upper
            or "ERR_INTERNET_DISCONNECTED" in upper
            or "ERR_TIMED_OUT" in upper
            or "ERR_NETWORK_" in upper
            or "ERR_EMPTY_RESPONSE" in upper
        ):
            return BrowserNetworkError(E_CONNECT.format(host=host, reason=error))
        return BrowserNetworkError(E_CHROME_NAV.format(url=display_url(url), errorText=error))

    def load(self, nav: Navigation, ctx: EventSink) -> NavOutcome:
        self._ensure_usable()
        if nav.submission is not None or nav.body is not None or (nav.method or "GET").upper() != "GET":
            # Page.navigate has no method or body: a POST must not silently become a GET.
            raise BrowserError(
                "Движок chrome открывает адреса только запросом GET. Форму в этой вкладке отправьте "
                "browser_fill_form(submit=true) или browser_click по кнопке; произвольный запрос — browser_request"
            )
        deadline = nav.deadline
        started = time.monotonic()
        try:
            guard.ensure_allowed(nav.url)
        except BrowserBlocked as exc:
            details = getattr(exc, "details", {}) or {}
            ctx.event(
                "browser_blocked",
                {
                    "tab": self.tab_id,
                    "host": details.get("blocked_host") or guard.blocked_host(nav.url),
                    "stage": details.get("blocked_stage") or "request",
                    "count": 1,
                },
            )
            raise
        what = f"открытие {display_url(nav.url)}"
        marks = self._marks()
        before_url = self._display_url()
        with self._cond:
            self._nav_blocked = None
        params: dict[str, Any] = {
            "url": nav.url,
            "transitionType": _TRANSITIONS.get(nav.initiator or "typed", "typed"),
        }
        if nav.referrer:
            params["referrer"] = nav.referrer
        notes: list[str] = []
        navigated = True
        self.own_navigation = True
        pushed = False
        try:
            pushed = self._push_headers(list(nav.headers or []), deadline)
            if pushed:
                notes.append("Дополнительные заголовки отправлены и с запросами, которые страница сделала при загрузке")
            result = self._call("Page.navigate", params, deadline, what=what)
            error = str(result.get("errorText") or "")
            loader = result.get("loaderId")
            if error and "ERR_HTTP_RESPONSE_CODE_FAILURE" not in error.upper():
                if "ERR_ABORTED" in error.upper() and self._wait_download(marks, deadline, notes):
                    navigated = False
                else:
                    raise self._navigation_error(nav.url, error, ctx)
            elif isinstance(loader, str) and loader:
                self._wait_commit(loader, marks, deadline, what)
                self._wait_lifecycle(nav.wait_until, deadline, what)
        except BrowserTimeout:
            self._send("Page.stopLoading")
            raise
        finally:
            self.own_navigation = False
            if pushed:
                self._restore_headers()
        with self._cond:
            if navigated:
                self._initial_blank = self._initial_blank and nav.url.strip().lower() != "about:blank"
        return self._nav_outcome(
            ctx,
            from_url=before_url,
            marks=marks,
            started=started,
            wait_until=nav.wait_until,
            deadline=deadline,
            navigated=navigated,
            notes=notes,
        )

    def _history_entries(self, deadline: Deadline | None) -> tuple[list[dict[str, Any]], int]:
        result = self._call("Page.getNavigationHistory", {}, deadline)
        entries = [entry for entry in result.get("entries") or [] if isinstance(entry, dict)]
        current = _int(result.get("currentIndex"))
        if current is None:
            current = len(entries) - 1
        start = 0
        # The blank page a new tab starts with is not part of the tab's history.
        if (
            self._initial_blank
            and len(entries) > 1
            and current > 0
            and str(entries[0].get("url") or "") in ("", "about:blank")
        ):
            start = 1
        return entries[start:], current - start

    def history(self) -> list[HistoryItem]:
        if not self.has_page():
            return []
        try:
            entries, current = self._history_entries(None)
        except BrowserError:
            return []
        doc, _url = self._current_doc()
        items: list[HistoryItem] = []
        for index, entry in enumerate(entries):
            is_current = index == current
            items.append(
                HistoryItem(
                    index=index,
                    url=display_url(str(entry.get("url") or "")),
                    title=str(entry.get("title") or ""),
                    method=doc.method if is_current and doc is not None else "GET",
                    status=doc.status if is_current and doc is not None else None,
                    engine="chrome",
                    current=is_current,
                )
            )
        return items

    def go(
        self,
        delta: int,
        *,
        wait_until: str | None = None,
        deadline: Deadline,
        ctx: EventSink,
    ) -> NavOutcome:
        self._ensure_usable()
        if not self._has_page:
            raise BrowserError(E_TAB_EMPTY.format(tab=self.tab_id or "?"))
        entries, current = self._history_entries(deadline)
        target = current + int(delta)
        if target < 0:
            raise BrowserError(history_back_message(self.tab_id or "?", "chrome"))
        if target >= len(entries):
            raise BrowserError(E_HISTORY_FWD.format(tab=self.tab_id or "?"))
        if target == current:
            return NavOutcome(page=self.page(), navigated=False)
        marks = self._marks()
        started = time.monotonic()
        before_url = self._display_url()
        what = "переход по истории вкладки"
        self.own_navigation = True
        try:
            self._call("Page.navigateToHistoryEntry", {"entryId": entries[target].get("id")}, deadline, what=what)
            navigated = self._wait_navigation(marks, wait_until, deadline, what)
        except BrowserTimeout:
            self._send("Page.stopLoading")
            raise
        finally:
            self.own_navigation = False
        return self._nav_outcome(
            ctx,
            from_url=before_url,
            marks=marks,
            started=started,
            wait_until=wait_until,
            deadline=deadline,
            navigated=navigated,
            notes=[],
        )

    def reload(self, *, wait_until: str | None = None, deadline: Deadline, ctx: EventSink) -> NavOutcome:
        self._ensure_usable()
        if not self._has_page:
            raise BrowserError(E_TAB_EMPTY.format(tab=self.tab_id or "?"))
        doc, _url = self._current_doc()
        notes: list[str] = []
        if doc is not None and doc.method.upper() == "POST":
            notes.append("запрос POST отправлен повторно")
        marks = self._marks()
        started = time.monotonic()
        before_url = self._display_url()
        what = "обновление страницы"
        self.own_navigation = True
        try:
            self._call("Page.reload", {"ignoreCache": False}, deadline, what=what)
            navigated = self._wait_navigation(marks, wait_until, deadline, what)
        except BrowserTimeout:
            self._send("Page.stopLoading")
            raise
        finally:
            self.own_navigation = False
        return self._nav_outcome(
            ctx,
            from_url=before_url,
            marks=marks,
            started=started,
            wait_until=wait_until,
            deadline=deadline,
            navigated=navigated,
            notes=notes,
        )

    # -- selectors, waits, JavaScript

    def mark_matches(self, selector: str) -> ViewPage:
        self._ensure_usable()
        result = self._isolated_value(f"window.__logos.markMatches({json.dumps(selector)})")
        result = result if isinstance(result, dict) else {}
        if result.get("error") is not None:
            raise BrowserError(E_SELECTOR_SYNTAX.format(sel=selector, reason=result["error"], pos="—"))
        return self.page()

    def _wait_url(self, value: str, deadline: Deadline) -> None:
        with self._cond:
            while value not in self._url:
                if self._dead():
                    raise self._lost_error()
                remaining = deadline.remaining()
                if remaining is not None and remaining <= 0:
                    raise BrowserTimeout("")
                self._cond.wait(remaining)

    def _wait_dom(self, spec: dict[str, Any], deadline: Deadline) -> None:
        javascript = bool(self._browser.options.javascript)
        while True:
            self._ensure_usable()
            remaining = deadline.remaining()
            if remaining is not None and remaining <= 0:
                raise BrowserTimeout("")
            if not javascript:
                # Without page scripts the helper's timers may not run: poll from here.
                result = self._isolated_value(f"window.__logos.check({json.dumps(spec)})", deadline=deadline)
            else:
                payload = dict(spec)
                payload["timeout"] = int(remaining * 1000) if remaining is not None else None
                backstop = Deadline(remaining + _WAIT_REPLY_GRACE) if remaining is not None else Deadline(None)
                try:
                    result = self._isolated_value(
                        f"window.__logos.waitFor({json.dumps(payload)})", await_promise=True, deadline=backstop
                    )
                except CdpError as exc:
                    if _context_gone(_cdp_text(exc)):
                        continue
                    raise
            result = result if isinstance(result, dict) else {}
            if result.get("ok"):
                return
            if result.get("error"):
                raise BrowserError(
                    E_SELECTOR_SYNTAX.format(sel=spec.get("value") or "", reason=result["error"], pos="—")
                )
            if result.get("timeout"):
                raise BrowserTimeout("")
            if not javascript:
                _pause(_WAIT_POLL, deadline)

    def _wait_expression(self, expression: str, deadline: Deadline) -> None:
        last_error = ""
        while True:
            self._ensure_usable()
            remaining = deadline.remaining()
            if remaining is not None and remaining <= 0:
                raise BrowserTimeout(last_error)
            try:
                result = self._call(
                    "Runtime.evaluate",
                    {
                        "expression": expression,
                        "awaitPromise": True,
                        "replMode": True,
                        "allowUnsafeEvalBlockedByCSP": True,
                        "returnByValue": False,
                        "silent": True,
                        "objectGroup": _EVAL_GROUP,
                    },
                    deadline,
                )
            except CdpError as exc:
                if not _context_gone(_cdp_text(exc)):
                    raise
                result = {}
            except BrowserTimeout:
                self._send("Runtime.terminateExecution")
                raise BrowserTimeout(last_error) from None
            finally:
                self._send("Runtime.releaseObjectGroup", {"objectGroup": _EVAL_GROUP})
            details = result.get("exceptionDetails")
            if details:
                last_error = _exception_text(details)
            elif result and _js_truthy(result.get("result") or {}):
                return
            _pause(_WAIT_POLL, deadline)

    def _wait_network_idle(self, deadline: Deadline) -> None:
        with self._cond:
            while True:
                if self._dead():
                    raise self._lost_error()
                quiet = time.monotonic() - self._last_activity
                if not self._inflight and quiet >= _NETWORK_IDLE_WINDOW:
                    return
                remaining = deadline.remaining()
                if remaining is not None and remaining <= 0:
                    raise BrowserTimeout("")
                timeout = remaining
                if not self._inflight:
                    left = _NETWORK_IDLE_WINDOW - quiet
                    timeout = left if timeout is None else min(timeout, left)
                self._cond.wait(None if timeout is None else max(timeout, 0.01))

    def wait(self, request: WaitRequest, ctx: EventSink) -> dict[str, Any]:
        self._ensure_usable()
        deadline = request.deadline
        began = time.monotonic()
        steps: list[tuple[str, Any, str]] = []
        if request.url_contains:
            steps.append(("url", request.url_contains, f"адрес содержит «{request.url_contains}»"))
        if request.selector:
            state = request.selector_state or "visible"
            word = _SELECTOR_STATES.get(state, state)
            steps.append(
                ("dom", {"kind": "selector", "value": request.selector, "state": state}, f"элемент «{request.selector}» {word}")
            )
        if request.text:
            steps.append(("dom", {"kind": "text", "value": request.text}, f"текст «{request.text}» на странице"))
        if request.text_gone:
            steps.append(("dom", {"kind": "text_gone", "value": request.text_gone}, f"текст «{request.text_gone}» исчез"))
        if request.expression:
            steps.append(("expression", request.expression, f"выражение «{request.expression}» истинно"))
        if request.network_idle:
            steps.append(("network", None, f"затишье сети {_NETWORK_IDLE_WINDOW:g} с"))
        if request.seconds:
            steps.append(("seconds", float(request.seconds), f"пауза {float(request.seconds):g} с"))
        satisfied: list[str] = []
        for position, (kind, value, label) in enumerate(steps):
            try:
                if kind == "url":
                    self._wait_url(str(value), deadline)
                elif kind == "dom":
                    self._wait_dom(value, deadline)
                elif kind == "expression":
                    self._wait_expression(str(value), deadline)
                elif kind == "network":
                    self._wait_network_idle(deadline)
                else:
                    remaining = deadline.remaining()
                    if remaining is not None and remaining < value:
                        _pause(remaining)
                        raise BrowserTimeout("")
                    _pause(value)
            except BrowserTimeout as exc:
                unmet = [step[2] for step in steps[position:]]
                detail = str(exc).strip()
                if detail:
                    unmet[0] = f"{unmet[0]} (последняя ошибка: {detail})"
                seconds = deadline.seconds if deadline.seconds is not None else 0.0
                raise BrowserTimeout(E_WAIT_TIMEOUT.format(t=f"{seconds:g}", conditions="; ".join(unmet))) from None
            satisfied.append(label)
        try:
            title = str(self._isolated_value("document.title") or "")
        except BrowserError:
            title = self._title
        return {
            "waited_seconds": round(time.monotonic() - began, 3),
            "satisfied": satisfied,
            "url": self._display_url() or None,
            "title": title,
        }

    def _serialize_remote(self, object_id: str, deadline: Deadline | None) -> Any:
        """A main-world object as JSON (DOM nodes described, cycles marked)."""
        _helper, serializer = _helper_sources()
        params = {
            "functionDeclaration": serializer,
            "objectId": object_id,
            "returnByValue": True,
            "silent": True,
        }
        try:
            result = self._call("Runtime.callFunctionOn", params, deadline)
        except CdpError as exc:
            logger.debug("direct serialization failed: %s", type(exc).__name__)
            result = {"exceptionDetails": {"text": _cdp_text(exc)}}
        if not result.get("exceptionDetails"):
            return (result.get("result") or {}).get("value")
        # Very deep values exceed Chrome's own transfer depth: send them as JSON text.
        text_params = dict(params)
        text_params["functionDeclaration"] = f"function () {{ return JSON.stringify(({serializer}).call(this)); }}"
        result = self._call("Runtime.callFunctionOn", text_params, deadline)
        details = result.get("exceptionDetails")
        if details:
            raise BrowserError(E_EVAL.format(text=f"результат не удалось преобразовать в JSON: {_exception_text(details)}"))
        text = (result.get("result") or {}).get("value")
        if not isinstance(text, str):
            return None
        try:
            return json.loads(text)
        except (ValueError, RecursionError):
            return text

    def evaluate(
        self,
        expression: str,
        *,
        target: ElementTarget | None,
        await_promise: bool,
        deadline: Deadline,
    ) -> EvalOutcome:
        self._ensure_usable()
        with self._cond:
            mark = self._console_total
        try:
            if target is None or target.is_empty():
                method = "Runtime.evaluate"
                params: dict[str, Any] = {
                    "expression": expression,
                    "awaitPromise": bool(await_promise),
                    "replMode": True,
                    "userGesture": True,
                    "allowUnsafeEvalBlockedByCSP": True,
                    "returnByValue": False,
                    "generatePreview": False,
                    "objectGroup": _EVAL_GROUP,
                }
            else:
                handle = self._resolve(target, "LBIFTM", deadline)
                if handle.object_id is None:
                    raise BrowserError(E_TARGET_REQUIRED)
                element = self._main_world_object(handle.object_id, deadline)
                body = expression.strip().rstrip(";").rstrip()
                if await_promise:
                    declaration = (
                        "async function (el) {\n  const __logosTarget = (\n"
                        + body
                        + "\n  );\n  return typeof __logosTarget === 'function' "
                        "? await __logosTarget.call(el, el) : __logosTarget;\n}"
                    )
                else:
                    declaration = (
                        "function (el) {\n  const __logosTarget = (\n"
                        + body
                        + "\n  );\n  return typeof __logosTarget === 'function' "
                        "? __logosTarget.call(el, el) : __logosTarget;\n}"
                    )
                method = "Runtime.callFunctionOn"
                params = {
                    "functionDeclaration": declaration,
                    "objectId": element,
                    "arguments": [{"objectId": element}],
                    "awaitPromise": bool(await_promise),
                    "userGesture": True,
                    "returnByValue": False,
                    "objectGroup": _EVAL_GROUP,
                }
            try:
                result = self._call(method, params, deadline, what="выполнение JavaScript")
            except BrowserTimeout:
                self._send("Runtime.terminateExecution")
                raise
            details = result.get("exceptionDetails")
            if details:
                raise BrowserError(E_EVAL.format(text=_exception_text(details)))
            remote = result.get("result") or {}
            kind = str(remote.get("type") or "undefined")
            subtype = remote.get("subtype")
            type_name = "null" if subtype == "null" else (f"{kind}/{subtype}" if subtype else kind)
            if "unserializableValue" in remote:
                value: Any = str(remote["unserializableValue"])
            elif kind == "undefined" or subtype == "null":
                value = None
            elif remote.get("objectId"):
                value = self._serialize_remote(str(remote["objectId"]), deadline)
            elif "value" in remote:
                value = remote.get("value")
            else:
                value = remote.get("description")
        finally:
            self._send("Runtime.releaseObjectGroup", {"objectGroup": _EVAL_GROUP})
            self._send("Runtime.releaseObjectGroup", {"objectGroup": _ACTION_GROUP})
        with self._cond:
            lines = [
                f"[{entry['level']}] {entry['text']}" for entry in self._console if entry.get("seq", 0) > mark
            ]
        return EvalOutcome(value=value, type=type_name, console=lines)

    # -- capture

    def capture(self, request: CaptureRequest, sink: BinarySink) -> CaptureOutcome:
        self._ensure_usable()
        deadline = request.deadline
        if request.format == "pdf":
            return self._print_pdf(request, sink, deadline)
        params: dict[str, Any] = {"format": "jpeg" if request.format == "jpeg" else "png", "fromSurface": True}
        if request.format == "jpeg" and request.quality is not None:
            params["quality"] = int(request.quality)
        clip: dict[str, Any] | None = None
        try:
            if request.target is not None and not request.target.is_empty():
                handle = self._resolve(request.target, "LBIFTM", deadline)
                if handle.object_id is None:
                    raise BrowserError(E_TARGET_REQUIRED)
                box = self._element_value(handle.object_id, "function () { return window.__logos.rect(this); }", deadline=deadline)
                box = box if isinstance(box, dict) else {}
                width = _float(box.get("width")) or 0.0
                height = _float(box.get("height")) or 0.0
                if width <= 0 or height <= 0:
                    raise BrowserError(E_NOT_VISIBLE.format(ref=handle.label))
                clip = {
                    "x": _float(box.get("x")) or 0.0,
                    "y": _float(box.get("y")) or 0.0,
                    "width": width,
                    "height": height,
                    "scale": 1,
                }
            elif request.full_page:
                metrics = self._call("Page.getLayoutMetrics", {}, deadline)
                size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
                width = math.ceil(_float(size.get("width")) or 0)
                height = math.ceil(_float(size.get("height")) or 0)
                if width > 0 and height > 0:
                    clip = {"x": 0, "y": 0, "width": width, "height": height, "scale": 1}
        finally:
            self._send("Runtime.releaseObjectGroup", {"objectGroup": _ACTION_GROUP})
        if clip is not None:
            params["clip"] = clip
            params["captureBeyondViewport"] = True
        result = self._call("Page.captureScreenshot", params, deadline, what="снимок страницы")
        try:
            data = base64.b64decode(result.get("data") or "")
        except (binascii.Error, ValueError):
            raise BrowserError("Chrome вернул повреждённый снимок; повторите") from None
        sink.write(data)
        width_px, height_px = _image_size(data)
        if width_px is None and clip is not None:
            scale = float(self._browser.options.device_scale_factor or 1)
            width_px = int(round(float(clip["width"]) * scale))
            height_px = int(round(float(clip["height"]) * scale))
        return CaptureOutcome(bytes=len(data), width=width_px, height=height_px)

    def _print_pdf(self, request: CaptureRequest, sink: BinarySink, deadline: Deadline) -> CaptureOutcome:
        params: dict[str, Any] = {
            "landscape": bool(request.landscape),
            "printBackground": bool(request.print_background),
            "preferCSSPageSize": request.paper is None,
            "transferMode": "ReturnAsStream",
        }
        if request.scale is not None:
            params["scale"] = float(request.scale)
        if request.paper:
            size = _PAPER_SIZES.get(request.paper)
            if size is not None:
                params["paperWidth"], params["paperHeight"] = size
        if request.page_ranges:
            params["pageRanges"] = request.page_ranges
        result = self._call("Page.printToPDF", params, deadline, what="печать в PDF")
        stream = result.get("stream")
        total = 0
        if not stream:
            try:
                data = base64.b64decode(result.get("data") or "")
            except (binascii.Error, ValueError):
                raise BrowserError("Chrome вернул повреждённый PDF; повторите") from None
            sink.write(data)
            return CaptureOutcome(bytes=len(data), width=None, height=None)
        try:
            while True:
                chunk = self._call("IO.read", {"handle": stream, "size": _IO_CHUNK}, deadline, what="чтение PDF")
                data_text = chunk.get("data") or ""
                if chunk.get("base64Encoded"):
                    try:
                        raw = base64.b64decode(data_text)
                    except (binascii.Error, ValueError):
                        raise BrowserError("Chrome вернул повреждённый PDF; повторите") from None
                else:
                    raw = str(data_text).encode("utf-8", "surrogatepass")
                if raw:
                    sink.write(raw)
                    total += len(raw)
                if chunk.get("eof"):
                    break
        finally:
            self._send("IO.close", {"handle": stream})
        return CaptureOutcome(bytes=total, width=None, height=None)

    # -- network, console

    def network(self) -> list[dict[str, Any]]:
        with self._cond:
            return [entry.public() for entry in self._net]

    def console(self) -> list[dict[str, Any]]:
        with self._cond:
            return [
                {"level": entry["level"], "text": entry["text"], "url": entry["url"], "line": entry["line"]}
                for entry in self._console
            ]

    def response_body(self, entry_id: str) -> tuple[bytes, str | None]:
        self._ensure_usable()
        wanted = normalize_ref(entry_id)
        with self._cond:
            entry = next((item for item in self._net if item.id == wanted), None)
        if entry is None:
            raise BrowserNotFound(f"Запрос {wanted} не найден; список запросов: browser_read(view='network')")
        try:
            result = self._call("Network.getResponseBody", {"requestId": entry.request_id})
        except CdpError:
            raise BrowserNotFound(E_BODY_GONE.format(id=wanted)) from None
        body = result.get("body") or ""
        if result.get("base64Encoded"):
            try:
                return base64.b64decode(body), entry.charset
            except (binascii.Error, ValueError):
                raise BrowserNotFound(E_BODY_GONE.format(id=wanted)) from None
        return str(body).encode("utf-8", "surrogatepass"), "utf-8"

    def close(self) -> None:
        browser = self._browser
        with self._cond:
            if self._closed:
                return
            self._closed = True
            browser._forget(self)
            self._cond.notify_all()
        if browser.alive():
            browser.conn.send_nowait("Target.closeTarget", {"targetId": self.target_id})



# ---------------------------------------------------------------------------
# Browser


class ChromeBrowser:
    """The Chrome process of one browser session: targets, guard, downloads, cookies."""

    def __init__(self, launched: chrome_launch.LaunchedChrome, conn: CdpConnection, options: ChromeOptions) -> None:
        self._launched = launched
        self.conn = conn
        self.options = options
        self.binary = launched.binary
        self.pid = launched.pid
        self.profile_dir = launched.profile_dir
        self.downloads_dir = launched.downloads_dir
        self.version = ""
        self.user_agent = ""
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._closed = False
        self._close_reason: str | None = None
        self._lost_reason: str | None = None
        self._engines_by_target: dict[str, ChromeTabEngine] = {}
        self._engines_by_session: dict[str, ChromeTabEngine] = {}
        self._children: dict[str, tuple[str | None, str]] = {}
        self._unclaimed: dict[str, ChromeTabEngine] = {}
        self._awaiting: set[str] = set()
        self._failed: dict[str, str] = {}
        self._popups: list[ChromeTabEngine] = []
        self._popup_seq = 0
        self._popups_pending = 0
        self._setups_pending = 0
        self._pending_pages: dict[str, ChromeTabEngine] = {}
        self._events: list[tuple[str, dict[str, Any]]] = []
        self._downloads: dict[str, dict[str, Any]] = {}
        self._finished: list[dict[str, Any]] = []
        self._auth_seen: set[str] = set()
        self._proxy_auth = chrome_launch.proxy_credentials(str(options.proxy or ""))
        self._queue: queue.Queue = queue.Queue()
        self._pool = ThreadPoolExecutor(max_workers=_GUARD_WORKERS, thread_name_prefix="logos-browser-guard")
        self._worker = threading.Thread(target=self._attach_loop, name="logos-browser-attach", daemon=True)
        self._worker.start()

    # -- lifecycle

    @classmethod
    def launch(
        cls,
        *,
        binary: chrome_launch.ChromeBinary,
        options: ChromeOptions,
        workdir: str,
        deadline: Deadline,
    ) -> ChromeBrowser:
        """Start Chrome with a fresh profile and prepare its first page."""
        launched = chrome_launch.launch(binary, options, workdir, deadline)
        conn: CdpConnection | None = None
        browser: ChromeBrowser | None = None
        try:
            conn = CdpConnection(launched.ws_url, deadline=deadline)
            browser = cls(launched, conn, options)
            conn.subscribe(browser._on_event)
            conn.on_lost = browser._on_connection_lost
            version = conn.call("Browser.getVersion", {}, deadline=deadline, what="запуск Chrome")
            product = str(version.get("product") or "")
            browser.version = product.split("/", 1)[1] if "/" in product else product
            browser.user_agent = str(version.get("userAgent") or "").replace("HeadlessChrome", "Chrome")
            conn.call("Target.setDiscoverTargets", {"discover": True}, deadline=deadline, what="запуск Chrome")
            conn.call("Target.setAutoAttach", dict(_AUTO_ATTACH), deadline=deadline, what="запуск Chrome")
            conn.call(
                "Browser.setDownloadBehavior",
                {"behavior": "allowAndName", "downloadPath": launched.downloads_dir, "eventsEnabled": True},
                deadline=deadline,
                what="запуск Chrome",
            )
            if options.ignore_tls_errors:
                try:
                    conn.call(
                        "Security.setIgnoreCertificateErrors", {"ignore": True}, deadline=deadline, what="запуск Chrome"
                    )
                except CdpError as exc:
                    # --ignore-certificate-errors is on the command line as well.
                    logger.debug("Security.setIgnoreCertificateErrors failed: %s", type(exc).__name__)
            targets = conn.call("Target.getTargets", {}, deadline=deadline, what="запуск Chrome")
            for info in targets.get("targetInfos") or []:
                if not isinstance(info, dict) or info.get("type") != "page":
                    continue
                target_id = str(info.get("targetId") or "")
                with browser._cond:
                    known = target_id in browser._engines_by_target
                if target_id and not known:
                    try:
                        conn.call(
                            "Target.attachToTarget",
                            {"targetId": target_id, "flatten": True},
                            deadline=deadline,
                            what="запуск Chrome",
                        )
                    except CdpError as exc:
                        logger.debug("initial page not attached: %s", type(exc).__name__)
        except BaseException:
            if browser is not None:
                browser.close("запуск Chrome не удался")
            else:
                if conn is not None:
                    conn.close("запуск Chrome не удался")
                chrome_launch.terminate(launched)
            raise
        logger.debug("chrome %s ready, pid %s", browser.version, browser.pid)
        return browser

    def alive(self) -> bool:
        if self._closed:
            return False
        if self._launched.proc.poll() is not None:
            return False
        return bool(self.conn.alive)

    def exit_info(self) -> str:
        if self._closed:
            return f"закрыт: {self._close_reason or 'по запросу'}"
        code = self._launched.proc.poll()
        if code is None:
            if not self.conn.alive:
                return self._lost_reason or "соединение DevTools закрыто"
            return "работает"
        if code < 0:
            try:
                name = signal.Signals(-code).name
            except ValueError:
                name = str(-code)
            return f"остановлен сигналом {name}"
        return f"код выхода {code}"

    def _on_connection_lost(self, reason: str) -> None:
        with self._cond:
            self._lost_reason = reason
            self._cond.notify_all()
        logger.debug("chrome pid %s connection lost: %s", self.pid, reason)

    def close(self, reason: str) -> None:
        """Stop Chrome (its whole process group) and remove the profile; idempotent."""
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._close_reason = reason
            for engine in list(self._engines_by_target.values()):
                if not engine._closed and not engine.lost:
                    engine.lost = True
                    engine.lost_reason = reason
            unfinished = [
                os.path.join(self.downloads_dir, guid)
                for guid, record in self._downloads.items()
                if record.get("state") == "inProgress"
            ]
            self._cond.notify_all()
        self._queue.put(None)
        try:
            self.conn.close(reason)
        except Exception as exc:
            logger.debug("cdp close failed: %s", type(exc).__name__)
        self._pool.shutdown(wait=False, cancel_futures=True)
        for note in chrome_launch.terminate(self._launched):
            logger.debug("chrome pid %s: %s", self.pid, note)
        for path in unfinished:
            try:
                os.unlink(path)
            except OSError:
                pass
        logger.debug("chrome pid %s closed (%s)", self.pid, redact_text(reason))

    # -- tabs

    def _forget(self, engine: ChromeTabEngine) -> None:
        """Drop an engine from every registry (browser lock held)."""
        self._engines_by_target.pop(engine.target_id, None)
        self._engines_by_session.pop(engine.session_id, None)
        self._unclaimed.pop(engine.target_id, None)
        if engine in self._popups:
            self._popups.remove(engine)

    def new_tab(self, tab_id: str, *, deadline: Deadline) -> ChromeTabEngine:
        """A prepared page for a tab: the spare initial page first, else a new target."""
        if not self.alive():
            raise ChromeClosed(E_CHROME_DIED.format(exit=self.exit_info()))
        what = "открытие вкладки Chrome"
        engine: ChromeTabEngine | None = None
        with self._cond:
            while self._setups_pending - self._popups_pending > 0 and self.alive():
                remaining = deadline.remaining()
                if remaining is not None and remaining <= 0:
                    raise BrowserTimeout(timeout_message(deadline.seconds, what))
                self._cond.wait(remaining)
            for target_id, candidate in list(self._unclaimed.items()):
                del self._unclaimed[target_id]
                if not candidate.lost and not candidate._closed:
                    engine = candidate
                    break
        if engine is None:
            created = self.conn.call("Target.createTarget", {"url": "about:blank"}, deadline=deadline, what=what)
            target_id = str(created.get("targetId") or "")
            with self._cond:
                self._awaiting.add(target_id)
                try:
                    while target_id not in self._unclaimed:
                        failure = self._failed.pop(target_id, None)
                        if failure:
                            raise BrowserError(failure)
                        if not self.alive():
                            raise ChromeClosed(E_CHROME_DIED.format(exit=self.exit_info()))
                        remaining = deadline.remaining()
                        if remaining is not None and remaining <= 0:
                            raise BrowserTimeout(timeout_message(deadline.seconds, what))
                        self._cond.wait(remaining)
                    engine = self._unclaimed.pop(target_id)
                finally:
                    self._awaiting.discard(target_id)
        with self._cond:
            engine.tab_id = tab_id
            engine._has_page = False
            engine._initial_blank = True
        logger.debug("chrome tab %s is target %s", tab_id, engine.target_id)
        return engine

    def take_popups(self) -> list[ChromeTabEngine]:
        """Pages the tabs opened (window.open, target=_blank) since the last call."""
        with self._cond:
            popups = [engine for engine in self._popups if not engine.lost and not engine._closed]
            self._popups = []
        return popups

    # -- events (reader thread)

    def _on_event(self, method: str, params: dict, session_id: str | None) -> None:
        if method == "Target.attachedToTarget":
            self._on_attached(params, session_id)
            return
        if method == "Target.detachedFromTarget":
            self._on_detached(params)
            return
        if session_id is None:
            if method == "Target.targetDestroyed":
                self._on_target_gone(params.get("targetId"), "вкладка закрыта (страница закрыла окно)")
            elif method == "Target.targetCrashed":
                status = params.get("status") or "?"
                self._on_target_gone(params.get("targetId"), f"вкладка Chrome аварийно завершилась ({status})")
            elif method == "Browser.downloadWillBegin":
                self._download_begin(params)
            elif method == "Browser.downloadProgress":
                self._download_progress(params)
            return
        if method == "Fetch.requestPaused":
            self._submit(self._guard_request, session_id, params)
            return
        if method == "Fetch.authRequired":
            self._submit(self._auth_request, session_id, params)
            return
        with self._cond:
            engine = self._engines_by_session.get(session_id)
        if engine is None:
            return
        if method == "Page.javascriptDialogOpening":
            self._submit(self._handle_dialog, engine, params)
            return
        if method == "Inspector.targetCrashed":
            self._on_target_gone(engine.target_id, "вкладка Chrome аварийно завершилась")
            return
        if method == "Network.webSocketCreated":
            self._submit(self._check_websocket, engine, str(params.get("url") or ""))
        with self._cond:
            engine._on_event(method, params)
            self._cond.notify_all()

    def _on_attached(self, params: dict, parent_session: str | None) -> None:
        info = params.get("targetInfo") or {}
        session = params.get("sessionId")
        if not isinstance(session, str) or not session or not isinstance(info, dict):
            return
        target_id = str(info.get("targetId") or "")
        kind = str(info.get("type") or "")
        with self._cond:
            if self._closed:
                return
            if kind == "page" and parent_session is None:
                if target_id in self._engines_by_target:
                    self._queue.put(("detach", session))
                    return
                engine = ChromeTabEngine(
                    self,
                    target_id,
                    session,
                    opener_id=str(info.get("openerId") or "") or None,
                    url=str(info.get("url") or ""),
                )
                self._engines_by_target[target_id] = engine
                self._engines_by_session[session] = engine
                self._pending_pages[session] = engine
                self._setups_pending += 1
                if engine.opener_id:
                    self._popup_seq += 1
                    self._popups_pending += 1
            else:
                self._children[session] = (parent_session, kind)
            self._cond.notify_all()
        self._queue.put(("attach", (session, kind)))

    def _on_detached(self, params: dict) -> None:
        session = params.get("sessionId")
        with self._cond:
            if session in self._children:
                del self._children[session]
                return
            engine = self._engines_by_session.get(session) if isinstance(session, str) else None
        if engine is not None:
            self._on_target_gone(engine.target_id, "вкладка Chrome отсоединилась")

    def _on_target_gone(self, target_id: Any, reason: str) -> None:
        with self._cond:
            engine = self._engines_by_target.get(str(target_id or ""))
            if engine is None:
                return
            if not engine._closed and not engine.lost:
                engine.lost = True
                engine.lost_reason = reason
            if engine.target_id in self._awaiting:
                self._failed[engine.target_id] = f"Вкладка Chrome закрылась при открытии: {reason}"
            self._forget(engine)
            self._cond.notify_all()

    def _submit(self, function: Callable[..., None], *args: Any) -> None:
        try:
            self._pool.submit(self._run_safely, function, *args)
        except RuntimeError:
            # The pool is shut down: Chrome is closing.
            logger.debug("chrome task dropped: browser closing")

    @staticmethod
    def _run_safely(function: Callable[..., None], *args: Any) -> None:
        try:
            function(*args)
        except Exception as exc:
            logger.debug("chrome task %s failed: %s", getattr(function, "__name__", "?"), type(exc).__name__)

    # -- target preparation (attach worker)

    def _attach_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            kind, payload = item
            try:
                if kind == "attach":
                    self._setup(*payload)
                elif kind == "detach":
                    self.conn.send_nowait("Target.detachFromTarget", {"sessionId": payload})
                elif kind == "download":
                    self._finish_download(payload)
            except Exception as exc:
                logger.debug("chrome worker step %s failed: %s", kind, type(exc).__name__)

    def _fetch_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]}
        if self._proxy_auth:
            params["handleAuthRequests"] = True
        return params

    def _setup(self, session: str, kind: str) -> None:
        with self._cond:
            # Popped even for a page already gone, so the pending counters always settle.
            engine = self._pending_pages.pop(session, None)
        if engine is not None:
            self._setup_page(engine)
        elif kind == "tab":
            self.conn.send_nowait("Runtime.runIfWaitingForDebugger", {}, session_id=session)
        else:
            self._setup_child(session, kind)

    def _emulation(self, engine: ChromeTabEngine, options: ChromeOptions) -> list[str]:
        """Apply the session settings to a page; returns problems (none are fatal)."""
        session = engine.session_id
        user_agent = options.user_agent or self.user_agent
        width = max(1, int(options.viewport_width or 1))
        height = max(1, int(options.viewport_height or 1))
        scale = float(options.device_scale_factor or 1)
        steps: list[tuple[str, dict[str, Any]]] = [
            ("Network.setExtraHTTPHeaders", {"headers": _headers_dict(options.extra_headers)}),
        ]
        if user_agent:
            metadata, platform = _ua_metadata(user_agent, branded=bool(options.user_agent))
            agent: dict[str, Any] = {"userAgent": user_agent, "acceptLanguage": options.accept_language}
            if platform:
                agent["platform"] = platform
            if metadata:
                agent["userAgentMetadata"] = metadata
            steps.append(("Emulation.setUserAgentOverride", agent))
        steps.extend(
            [
                (
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": width,
                        "height": height,
                        "deviceScaleFactor": scale,
                        "mobile": bool(options.mobile),
                        "screenWidth": width,
                        "screenHeight": height,
                    },
                ),
                (
                    "Emulation.setTouchEmulationEnabled",
                    {"enabled": True, "maxTouchPoints": 5} if options.mobile else {"enabled": False},
                ),
                ("Emulation.setScriptExecutionDisabled", {"value": not options.javascript}),
                ("Emulation.setFocusEmulationEnabled", {"enabled": True}),
            ]
        )
        problems: list[str] = []
        for method, params in steps:
            try:
                self.conn.call(method, params, session_id=session)
            except ChromeClosed:
                raise
            except BrowserError as exc:
                failure: BrowserError = exc
                if method == "Emulation.setUserAgentOverride" and len(params) > 2:
                    # An older Chrome may reject the client hints: keep at least the UA and language.
                    try:
                        self.conn.call(
                            method,
                            {"userAgent": params["userAgent"], "acceptLanguage": params["acceptLanguage"]},
                            session_id=session,
                        )
                        continue
                    except ChromeClosed:
                        raise
                    except BrowserError as retry:
                        failure = retry
                problems.append(f"Chrome не применил настройку «{_EMULATION_WORDS.get(method, method)}»: {failure}")
        return problems

    def _setup_page(self, engine: ChromeTabEngine) -> None:
        session = engine.session_id
        conn = self.conn
        error: str | None = None
        problems: list[str] = []
        try:
            try:
                conn.call("Fetch.enable", self._fetch_params(), session_id=session)
            except BrowserError as exc:
                logger.debug("fetch interception not enabled for a page: %s", type(exc).__name__)
                error = str(E_GUARD_SETUP)
            if error is None:
                for method, params in (
                    ("Network.enable", {}),
                    ("Network.setBlockedURLs", {"urls": list(guard.LITERAL_LINK_LOCAL_PATTERNS)}),
                ):
                    try:
                        conn.call(method, params, session_id=session)
                    except BrowserError as exc:
                        logger.debug("%s failed for a page: %s", method, type(exc).__name__)
                try:
                    conn.call("Page.enable", {}, session_id=session)
                    conn.call("Page.setLifecycleEventsEnabled", {"enabled": True}, session_id=session)
                except BrowserError as exc:
                    error = f"Не удалось подготовить вкладку Chrome: {exc}"
            if error is None:
                for method in ("Runtime.enable", "Log.enable"):
                    try:
                        conn.call(method, {}, session_id=session)
                    except BrowserError as exc:
                        logger.debug("%s failed for a page: %s", method, type(exc).__name__)
                try:
                    tree = conn.call("Page.getFrameTree", {}, session_id=session)
                    frame = (tree.get("frameTree") or {}).get("frame") or {}
                except BrowserError:
                    frame = {}
                with self._cond:
                    if isinstance(frame.get("id"), str):
                        engine._frame_id = frame["id"]
                    loader = frame.get("loaderId")
                    if isinstance(loader, str) and loader and engine._loader is None:
                        engine._loader = loader
                        engine._net_document = loader
                    if not engine._url and frame.get("url"):
                        engine._url = str(frame.get("url"))
                problems = self._emulation(engine, self.options)
                try:
                    # Frames in other processes and workers must be guarded too: required.
                    conn.call("Target.setAutoAttach", dict(_AUTO_ATTACH), session_id=session)
                except BrowserError as exc:
                    logger.debug("auto-attach not enabled for a page: %s", type(exc).__name__)
                    error = str(E_GUARD_SETUP)
            if error is None:
                conn.call("Runtime.runIfWaitingForDebugger", {}, session_id=session)
        except BrowserError as exc:
            error = error or f"Не удалось подготовить вкладку Chrome: {exc}"
        if error is not None:
            conn.send_nowait("Target.closeTarget", {"targetId": engine.target_id})
        with self._cond:
            self._setups_pending -= 1
            if engine.opener_id:
                self._popups_pending -= 1
            if problems:
                engine._notes.extend(problems)
            if error is None and not engine.lost and not engine._closed:
                engine.ready = True
                if engine.opener_id and engine.target_id not in self._awaiting:
                    self._popups.append(engine)
                else:
                    self._unclaimed[engine.target_id] = engine
            else:
                engine.setup_error = error or engine.lost_reason or "вкладка Chrome закрылась"
                if not engine.opener_id:
                    self._failed[engine.target_id] = engine.setup_error
                self._forget(engine)
            self._cond.notify_all()

    def _setup_child(self, session: str, kind: str) -> None:
        conn = self.conn
        try:
            conn.call("Fetch.enable", self._fetch_params(), session_id=session)
        except BrowserError as exc:
            # Fail closed: the frame or worker stays paused, so it sends no request unchecked.
            logger.debug("fetch interception not enabled for a %s target: %s", kind, type(exc).__name__)
            root = self._root_engine(session)
            if root is not None:
                with self._cond:
                    note = (
                        f"Фоновый поток/фрейм страницы ({kind or 'другое'}) остановлен: не удалось "
                        "включить проверку адресов"
                    )
                    if note not in root._notes:
                        root._notes.append(note)
            return
        for method, params in (
            ("Network.setBlockedURLs", {"urls": list(guard.LITERAL_LINK_LOCAL_PATTERNS)}),
            ("Target.setAutoAttach", dict(_AUTO_ATTACH)),
        ):
            try:
                conn.call(method, params, session_id=session)
            except BrowserError as exc:
                logger.debug("%s failed for a %s target: %s", method, kind, type(exc).__name__)
        conn.send_nowait("Runtime.runIfWaitingForDebugger", {}, session_id=session)

    def _root_engine(self, session: str | None) -> ChromeTabEngine | None:
        """The page a frame or worker session belongs to."""
        seen: set[str] = set()
        with self._cond:
            current = session
            while current is not None and current not in seen:
                seen.add(current)
                engine = self._engines_by_session.get(current)
                if engine is not None:
                    return engine
                parent = self._children.get(current)
                if parent is None:
                    return None
                current = parent[0]
        return None

    # -- request guard and dialogs (pool)

    def _guard_request(self, session: str, params: dict) -> None:
        request_id = params.get("requestId")
        request = params.get("request") or {}
        url = str(request.get("url") or "")
        try:
            blocked = guard.is_blocked(url)
            if blocked:
                # Recorded first: Chrome answers the navigation as soon as the request fails.
                self._record_block(session, url, params)
                self.conn.send_nowait(
                    "Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"}, session_id=session
                )
                return
            if not self.options.load_images and params.get("resourceType") == "Image":
                self.conn.send_nowait(
                    "Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"}, session_id=session
                )
                return
            self.conn.send_nowait("Fetch.continueRequest", {"requestId": request_id}, session_id=session)
        except Exception as exc:
            # Fail closed: a request that could not be decided is not sent.
            logger.debug("request decision failed: %s", type(exc).__name__)
            self.conn.send_nowait(
                "Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"}, session_id=session
            )

    def _record_block(self, session: str, url: str, params: dict) -> None:
        host = guard.blocked_host(url)
        logger.debug("chrome request to link-local host %s blocked", host)
        engine = self._root_engine(session)
        with self._cond:
            if engine is None:
                self._events.append(
                    ("browser_blocked", {"tab": "", "host": host, "stage": "subresource", "count": 1})
                )
            else:
                plain = _strip_fragment(url)
                engine._blocked.append(url)
                engine._blocked_urls.add(plain)
                for entry in engine._net:
                    if entry.plain_url == plain and entry.failure is not None:
                        entry.blocked = True
                if (
                    session == engine.session_id
                    and params.get("resourceType") == "Document"
                    and params.get("frameId") == engine._frame_id
                ):
                    engine._nav_blocked = url
            self._cond.notify_all()

    def _auth_request(self, session: str, params: dict) -> None:
        request_id = str(params.get("requestId") or "")
        challenge = params.get("authChallenge") or {}
        response: dict[str, Any] = {"response": "Default"}
        if challenge.get("source") == "Proxy" and self._proxy_auth:
            with self._cond:
                repeated = request_id in self._auth_seen
                if repeated:
                    self._auth_seen.discard(request_id)
                else:
                    self._auth_seen.add(request_id)
            if repeated:
                # The proxy refused these credentials once already.
                response = {"response": "CancelAuth"}
            else:
                user, password = self._proxy_auth
                response = {"response": "ProvideCredentials", "username": user, "password": password}
        self.conn.send_nowait(
            "Fetch.continueWithAuth", {"requestId": request_id, "authChallengeResponse": response}, session_id=session
        )

    def _handle_dialog(self, engine: ChromeTabEngine, params: dict) -> None:
        kind = str(params.get("type") or "alert")
        message = str(params.get("message") or "")
        options = self.options
        if kind == "beforeunload" and engine.own_navigation:
            accept = True
        else:
            accept = options.dialogs != "dismiss"
        payload: dict[str, Any] = {"accept": accept}
        answer = None
        if kind == "prompt" and accept:
            answer = options.dialog_prompt_text if options.dialog_prompt_text is not None else str(params.get("defaultPrompt") or "")
            payload["promptText"] = answer
        self.conn.send_nowait("Page.handleJavaScriptDialog", payload, session_id=engine.session_id)
        text = f"Страница показала {_DIALOG_WORDS.get(kind, kind)} «{message}» — {'принят' if accept else 'отклонён'}"
        if answer is not None:
            text += f", ответ «{answer}»"
        with self._cond:
            engine._dialogs.append(text)
            self._cond.notify_all()

    def _check_websocket(self, engine: ChromeTabEngine, url: str) -> None:
        if not url or not guard.is_blocked(url):
            return
        host = guard.blocked_host(url)
        with self._cond:
            engine._notes.append(
                f"Внимание: страница открыла WebSocket к {host}, а это адрес link-local. WebSocket-соединения "
                "Chrome не даёт перехватить: для них действует только список заблокированных адресов Chrome"
            )

    # -- downloads

    def _download_begin(self, params: dict) -> None:
        guid = params.get("guid")
        if not isinstance(guid, str) or not guid:
            return
        with self._cond:
            engine = self._engines_by_target.get(str(params.get("frameId") or ""))
            self._downloads[guid] = {
                "guid": guid,
                "url": str(params.get("url") or ""),
                "suggested": str(params.get("suggestedFilename") or ""),
                "state": "inProgress",
                "received": 0,
                "total": None,
                "engine": engine,
                "finishing": False,
            }
            if engine is not None:
                engine._download_seq += 1
                engine._active_downloads.add(guid)
            self._cond.notify_all()

    def _download_progress(self, params: dict) -> None:
        guid = params.get("guid")
        with self._cond:
            record = self._downloads.get(guid) if isinstance(guid, str) else None
            if record is None:
                return
            record["received"] = _int(params.get("receivedBytes")) or record["received"]
            record["total"] = _int(params.get("totalBytes")) or record["total"]
            state = str(params.get("state") or "")
            if state in ("completed", "canceled") and not record["finishing"]:
                record["finishing"] = True
                record["state"] = state
                self._queue.put(("download", guid))
            self._cond.notify_all()

    def _finish_download(self, guid: str) -> None:
        with self._cond:
            record = self._downloads.get(guid)
            if record is None:
                return
            url = record["url"]
            suggested = record["suggested"]
            state = record["state"]
            received = record["received"]
            engine = record["engine"]
        source = os.path.join(self.downloads_dir, guid)
        path = source
        name = guid
        size: int | None = received
        if state == "completed":
            name = sanitize_filename(suggested) if suggested else filename_from_response(None, url, None)[0]
            try:
                target = unique_name(self.downloads_dir, name)
                os.replace(source, target)
                path = target
                name = os.path.basename(target)
            except OSError as exc:
                logger.debug("download %s not renamed: %s", guid, type(exc).__name__)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = received
        else:
            try:
                os.unlink(source)
            except OSError:
                pass
        info = {
            "name": name,
            "path": path,
            "bytes": size,
            "state": "completed" if state == "completed" else "canceled",
            "url": display_url(url),
        }
        with self._cond:
            record["state"] = info["state"]
            record["path"] = path
            self._finished.append(dict(info))
            if engine is not None:
                engine._downloads.append(dict(info))
                engine._active_downloads.discard(guid)
            self._cond.notify_all()
        logger.debug("chrome download %s finished: %s bytes", info["state"], size)

    def take_downloads(self) -> list[dict[str, Any]]:
        """Downloads finished since the last call: name, path, bytes, state, url."""
        with self._cond:
            finished, self._finished = self._finished, []
        return finished

    def take_events(self) -> list[tuple[str, dict[str, Any]]]:
        """Trace events recorded off the calling thread (blocked requests of no tab)."""
        with self._cond:
            events, self._events = self._events, []
        return events

    # -- settings and cookies

    def apply_options(self, options: ChromeOptions) -> list[str]:
        """Apply changed session settings to the running Chrome; returns notes."""
        notes: list[str] = []
        old = self.options
        self.options = options
        if str(old.proxy or "") != str(options.proxy or ""):
            notes.append(
                "Новый прокси начнёт действовать в Chrome после его перезапуска: закройте вкладки chrome "
                "(browser_tabs close) или browser_session(action='reset')"
            )
        if bool(old.ignore_tls_errors) != bool(options.ignore_tls_errors):
            try:
                self.conn.call("Security.setIgnoreCertificateErrors", {"ignore": bool(options.ignore_tls_errors)})
            except BrowserError as exc:
                notes.append(f"Chrome не применил настройку проверки TLS: {exc}")
        with self._cond:
            engines = [
                engine
                for engine in self._engines_by_target.values()
                if engine.ready and not engine.lost and not engine._closed
            ]
        for engine in engines:
            for problem in self._emulation(engine, options):
                if problem not in notes:
                    notes.append(problem)
        return notes

    def get_cookies(self) -> list[dict[str, Any]]:
        result = self.conn.call("Storage.getCookies", {}, what="чтение cookies Chrome")
        cookies = result.get("cookies")
        return [cookie for cookie in cookies if isinstance(cookie, dict)] if isinstance(cookies, list) else []

    def set_cookies(self, cookies: list[dict[str, Any]]) -> None:
        """Replace Chrome's cookies with ``cookies``."""
        self.conn.call("Storage.clearCookies", {}, what="запись cookies в Chrome")
        items = [cookie for cookie in cookies or [] if isinstance(cookie, dict)]
        if items:
            self.conn.call("Storage.setCookies", {"cookies": items}, what="запись cookies в Chrome")

    def info(self) -> dict[str, Any]:
        with self._cond:
            tabs = sum(
                1
                for engine in self._engines_by_target.values()
                if engine.tab_id and not engine.lost and not engine._closed
            )
        return {
            "путь": self.binary.path,
            "найден через": _SOURCE_WORDS.get(self.binary.source, self.binary.source),
            "версия": self.version,
            "pid": self.pid,
            "вкладок": tabs,
            "папка загрузок": self.downloads_dir,
        }


__all__ = ["ChromeBrowser", "ChromeTabEngine"]
