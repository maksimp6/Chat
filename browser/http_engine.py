"""HTTP engine of the Logos browser: a requests client that behaves like a browser.

``HttpClient`` sends browser-shaped requests (realistic headers, a per-session
cookie jar, the referrer policy) and follows redirects itself, so that every
hop passes the link-local guard, a real redirect loop is recognised by its
state, and the caller's timeout covers the whole exchange, body included
(a watchdog shuts the socket down when the deadline passes). Bodies are never
cut silently: ``max_bytes`` is the caller's bound (0 means none), and binary
or attachment bodies are not loaded at all; downloads stream to disk.

The module also encodes HTML form submissions (urlencoded, text/plain and
multipart streamed through a spooled temporary file), computes Referer and
Sec-Fetch-Site values, and converts the cookie jar to and from the Chrome
DevTools cookie format.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import html as html_lib
import importlib.util
import ipaddress
import logging
import mimetypes
import os
import re
import secrets
import socket
import stat
import tempfile
import threading
import time
import warnings
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Iterator
from urllib.parse import (
    quote,
    unquote,
    unquote_to_bytes,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

import requests
from requests import cookies as requests_cookies
from requests.auth import AuthBase, HTTPBasicAuth
from requests.utils import get_auth_from_url, requote_uri, select_proxy
from urllib3.exceptions import InsecureRequestWarning, ProtocolError
from urllib3.util import SKIP_HEADER
from urllib3.util.request import ACCEPT_ENCODING

from browser import guard
from browser.charsets import GENERIC_MIMES, classify, decode_body, parse_content_type
from browser.engine import BinaryInfo, HeadersInfo, Hop
from browser.errors import (
    E_BAD_REQUEST,
    E_BODY_CUT,
    E_BROKEN_READ,
    E_CONNECT,
    E_COOKIE_PLACEHOLDER,
    E_DNS,
    E_DOWNLOAD_STATUS,
    E_FILE_MISSING,
    E_MAX_BYTES,
    E_PROXY,
    E_REDIRECT_CYCLE,
    E_SOCKS,
    E_TLS,
    E_TOO_MANY_REDIRECTS,
    E_URL_SCHEME,
    BrowserBlocked,
    BrowserError,
    BrowserNetworkError,
    BrowserNotFound,
    BrowserTimeout,
    Deadline,
    timeout_message,
)
from browser.files import (
    AtomicWriter,
    disk_free,
    disk_full_error,
    extension_for,
    filename_from_response,
    resolve_target,
)
from browser.host import resolve_tool_path
from browser.paging import human_bytes
from browser.redact import display_url, fingerprint, mask_headers, redact_text
from browser.settings import DOCUMENT_ACCEPT, SessionSettings

if TYPE_CHECKING:
    from browser.forms import FormSubmission

logger = logging.getLogger("logos.browser.http")

REDIRECT_STATUSES = (301, 302, 303, 307, 308)
HTML_MIMES = frozenset({"text/html", "application/xhtml+xml"})
_CHUNK = 65536
_REFRESH_PEEK = 8192
_REFRESH_SCAN = 65536
_CONTENT_HEADERS = ("content-type", "content-length", "content-encoding", "transfer-encoding")
_SKIPPABLE_REMOVALS = {"user-agent": "User-Agent", "accept-encoding": "Accept-Encoding"}
_DNS_MARKERS = re.compile(
    r"NameResolution|getaddrinfo|Name or service not known|nodename nor servname|"
    r"Temporary failure in name resolution|No address associated with hostname|"
    r"Failed to resolve|Name does not resolve",
    re.I,
)
_OBJECT_REPR = re.compile(r"<[^<>]*? object at 0x[0-9a-fA-F]+>:?\s*")
_POOL_WRAPPER = re.compile(
    r"^\w*ConnectionPool\(host=[^)]*\): Max retries exceeded with url: \S+ \(Caused by \w+\((.*)\)\)$",
    re.S,
)
_META_TAG = re.compile(r"<meta\b[^>]*>", re.I)
_ATTRIBUTE = re.compile(
    r"""([^\s"'>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?""", re.S
)
_COOKIE_PLACEHOLDER = re.compile(r"\{\{\s*cookie\s*:\s*([^{}]+?)\s*\}\}")
_REFERRER_POLICIES = {
    "no-referrer",
    "no-referrer-when-downgrade",
    "same-origin",
    "origin",
    "strict-origin",
    "origin-when-cross-origin",
    "strict-origin-when-cross-origin",
    "unsafe-url",
}
_LEGACY_POLICIES = {
    "never": "no-referrer",
    "default": "strict-origin-when-cross-origin",
    "always": "unsafe-url",
    "origin-when-crossorigin": "origin-when-cross-origin",
}
_DEFAULT_POLICY = "strict-origin-when-cross-origin"
# Second-level labels under which registrations happen (co.uk, com.ru…): a small
# approximation of the public suffix list, enough for Sec-Fetch-Site.
_SECOND_LEVEL_LABELS = frozenset(
    {"co", "com", "net", "org", "gov", "edu", "ac", "or", "ne", "go", "mil", "msk", "spb", "nom", "ltd", "plc"}
)


# ---------------------------------------------------------------------------
# Data types


@dataclass
class RequestSpec:
    """One logical request; the client turns it into one or more HTTP exchanges."""

    url: str
    method: str = "GET"
    headers: list[tuple[str, str]] = field(default_factory=list)
    params: list[tuple[str, str]] = field(default_factory=list)
    body: bytes | None = None
    body_file: Any = None
    body_length: int | None = None
    content_type: str | None = None
    referrer: str | None = None
    origin: str | None = None
    mode: str = "navigate"
    user_initiated: bool = True
    use_cookies: bool = True
    follow_redirects: bool = True
    follow_refresh: bool = False
    max_redirects: int = 0
    max_bytes: int = 0
    deadline: Deadline = field(default_factory=lambda: Deadline(None))
    rebuild: Callable[[], "RequestSpec"] | None = None


@dataclass
class LoadedDocument:
    """A fetched document: decoded text or a description of a binary body."""

    url: str
    status: int | None
    reason: str
    method: str
    mime: str
    content_type: str
    kind: str
    charset: str
    charset_source: str
    text: str | None
    size: int | None
    binary: BinaryInfo | None
    headers: HeadersInfo
    notes: list[str]
    elapsed_ms: int | None
    fetched_at: float
    spec: RequestSpec
    referrer_policy: str | None = None


class _UrlCredentialsAuth(AuthBase):
    """Basic credentials from the request URL only.

    Being a truthy session auth, it also stops requests from reading ~/.netrc:
    the browser never picks up the owner's stored credentials.
    """

    def __call__(self, request):
        if "Authorization" in request.headers:
            return request
        try:
            user, password = get_auth_from_url(request.url)
        except Exception:
            return request
        if user or password:
            HTTPBasicAuth(user, password)(request)
        return request


class _BodyStream:
    """A file body with a known length, so requests sends Content-Length and streams it."""

    def __init__(self, fileobj: Any, length: int | None) -> None:
        self._file = fileobj
        if length is None:
            position = fileobj.tell()
            fileobj.seek(0, os.SEEK_END)
            length = fileobj.tell()
            fileobj.seek(position)
        self._length = int(length)

    def __len__(self) -> int:
        return self._length

    def __iter__(self) -> Iterator[bytes]:
        while True:
            chunk = self._file.read(_CHUNK)
            if not chunk:
                return
            yield chunk

    def read(self, size: int = -1) -> bytes:
        return self._file.read(size)

    def tell(self) -> int:
        return self._file.tell()

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        return self._file.seek(offset, whence)


class _HeaderSet:
    """Ordered, case-insensitive request headers where later values win and "" removes."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[str, str]] = {}
        self.removed: set[str] = set()

    def set(self, name: str, value: Any) -> None:
        name = str(name or "").strip()
        if not name:
            return
        key = name.lower()
        text = "" if value is None else str(value)
        if text == "":
            self._items.pop(key, None)
            self.removed.add(key)
        else:
            self._items[key] = (name, text)
            self.removed.discard(key)

    def get(self, name: str) -> str | None:
        item = self._items.get(name.lower())
        return item[1] if item else None

    def discard(self, name: str) -> None:
        self._items.pop(name.lower(), None)

    def copy(self) -> "_HeaderSet":
        other = _HeaderSet()
        other._items = dict(self._items)
        other.removed = set(self.removed)
        return other

    def to_request(self) -> dict[str, str]:
        result = {name: value for name, value in self._items.values()}
        for key, canonical in _SKIPPABLE_REMOVALS.items():
            if key in self.removed and key not in self._items:
                result[canonical] = SKIP_HEADER
        return result


# ---------------------------------------------------------------------------
# Small helpers


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or "?"
    except ValueError:
        return "?"


def _strip_fragment(url: str) -> str:
    return url.split("#", 1)[0]


def _with_params(url: str, params: list[tuple[str, str]]) -> str:
    if not params:
        return url
    parts = urlsplit(url)
    extra = urlencode([(str(name), str(value)) for name, value in params])
    query = f"{parts.query}&{extra}" if parts.query else extra
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _http_version(raw: Any) -> str | None:
    text = getattr(raw, "version_string", None)
    if isinstance(text, str) and text and text != "HTTP/?":
        return text
    number = getattr(raw, "version", None)
    return {9: "HTTP/0.9", 10: "HTTP/1.0", 11: "HTTP/1.1", 20: "HTTP/2", 30: "HTTP/3"}.get(number)


def _clean_reason(text: str) -> str:
    text = _OBJECT_REPR.sub("", text).strip()
    match = _POOL_WRAPPER.match(text)
    if match:
        text = match.group(1).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    return redact_text(text.strip())


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    stack: list[BaseException] = [exc]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        for candidate in (
            getattr(current, "reason", None),
            current.args[0] if current.args else None,
            current.__cause__,
            current.__context__,
        ):
            if isinstance(candidate, BaseException) and id(candidate) not in seen:
                stack.append(candidate)
    return chain


def _reason(exc: BaseException) -> str:
    """The most specific, readable reason in an exception chain, credentials redacted."""
    chain = _exception_chain(exc)
    for current in reversed(chain):
        text = str(current).strip()
        if text:
            cleaned = _clean_reason(text)
            if cleaned:
                return cleaned
    return type(exc).__name__


def _chain_text(exc: BaseException) -> str:
    return " ".join(f"{type(item).__name__}: {item}" for item in _exception_chain(exc))


def _timeout_error(deadline: Deadline, what: str, elapsed: float) -> BrowserTimeout:
    seconds = deadline.seconds if deadline.seconds else round(elapsed, 1)
    return BrowserTimeout(timeout_message(seconds, what))


def _socks_available() -> bool:
    try:
        return importlib.util.find_spec("socks") is not None
    except (ImportError, ValueError):
        return False


def parse_refresh(value: str | None) -> tuple[float, str | None] | None:
    """Parse a Refresh header or meta content: (delay seconds, target URL or None)."""
    if value is None:
        return None
    text = html_lib.unescape(str(value)).strip()
    match = re.match(r"\s*(\d*(?:\.\d*)?)\s*(.*)$", text, re.S)
    if not match:
        return None
    number, rest = match.group(1), match.group(2).strip()
    try:
        delay = float(number) if number not in ("", ".") else 0.0
    except ValueError:
        delay = 0.0
    if not number and not rest:
        return None
    rest = rest.lstrip(";,").strip()
    if not rest:
        return delay, None
    url_match = re.match(r"(?i)url\s*=\s*(.*)$", rest, re.S)
    target = url_match.group(1) if url_match else rest
    target = target.strip()
    if len(target) >= 1 and target[0] in "'\"":
        quote_char = target[0]
        end = target.find(quote_char, 1)
        target = target[1:end] if end > 0 else target[1:]
    target = target.strip()
    return delay, (target or None)


def meta_refresh(html_text: str) -> tuple[float, str | None] | None:
    """The first <meta http-equiv="refresh"> in ``html_text``, parsed."""
    for tag in _META_TAG.finditer(html_text):
        attrs: dict[str, str] = {}
        for match in _ATTRIBUTE.finditer(tag.group(0)[5:]):
            name = match.group(1).lower()
            value = next((g for g in match.group(2, 3, 4) if g is not None), "")
            attrs.setdefault(name, value)
        if attrs.get("http-equiv", "").strip().lower() == "refresh" and "content" in attrs:
            parsed = parse_refresh(attrs["content"])
            if parsed is not None:
                return parsed
    return None


def hop_lines(hops: list[Hop], final_url: str) -> list[str]:
    """Redirect chain as "301 → <next address>" lines (display URLs)."""
    lines: list[str] = []
    for index, hop in enumerate(hops):
        target = hops[index + 1].url if index + 1 < len(hops) else display_url(final_url)
        lines.append(f"{hop.status} → {target}")
    return lines


# ---------------------------------------------------------------------------
# Referrer policy and Sec-Fetch-Site


def normalize_referrer_policy(value: str | None) -> str | None:
    """The effective policy of a Referrer-Policy value (last recognised token wins)."""
    if not value:
        return None
    result = None
    for token in str(value).split(","):
        name = token.strip().lower()
        name = _LEGACY_POLICIES.get(name, name)
        if name in _REFERRER_POLICIES:
            result = name
    return result


def _origin_tuple(parts) -> tuple[str, str, int | None]:
    scheme = (parts.scheme or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is None:
        port = {"http": 80, "https": 443}.get(scheme)
    return scheme, (parts.hostname or "").lower(), port


def _netloc(parts) -> str:
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    return f"{host}:{port}" if port else host


def origin_of(url: str | None) -> str | None:
    """Serialized origin of an http(s) URL ("https://host[:port]"), "null" otherwise."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return "null"
    if (parts.scheme or "").lower() not in ("http", "https"):
        return "null"
    return f"{parts.scheme.lower()}://{_netloc(parts)}"


def referrer_value(
    from_url: str | None,
    to_url: str,
    *,
    policy: str | None = None,
    noreferrer: bool = False,
) -> str | None:
    """Referer header for a request from ``from_url`` to ``to_url`` under ``policy``."""
    if noreferrer or not from_url:
        return None
    try:
        source = urlsplit(from_url)
        target = urlsplit(to_url)
    except ValueError:
        return None
    if (source.scheme or "").lower() not in ("http", "https"):
        return None
    name = normalize_referrer_policy(policy) or _DEFAULT_POLICY
    netloc = _netloc(source)
    scheme = source.scheme.lower()
    full = urlunsplit((scheme, netloc, source.path or "/", source.query, ""))
    origin = f"{scheme}://{netloc}/"
    same_origin = _origin_tuple(source) == _origin_tuple(target)
    downgrade = scheme == "https" and (target.scheme or "").lower() != "https"
    if name == "no-referrer":
        return None
    if name == "unsafe-url":
        return full
    if name == "origin":
        return origin
    if name == "same-origin":
        return full if same_origin else None
    if name == "no-referrer-when-downgrade":
        return None if downgrade else full
    if name == "strict-origin":
        return None if downgrade else origin
    if name == "origin-when-cross-origin":
        return full if same_origin else origin
    # strict-origin-when-cross-origin
    if same_origin:
        return full
    return None if downgrade else origin


def _site_of(host: str) -> str:
    host = host.lower().rstrip(".")
    try:
        ipaddress.ip_address(host.strip("[]"))
        return host
    except ValueError:
        pass
    labels = [label for label in host.split(".") if label]
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL_LABELS and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def fetch_site(from_url: str | None, to_url: str) -> str:
    """Sec-Fetch-Site value: none, same-origin, same-site or cross-site."""
    if not from_url:
        return "none"
    try:
        source = urlsplit(from_url)
        target = urlsplit(to_url)
    except ValueError:
        return "cross-site"
    if _origin_tuple(source) == _origin_tuple(target):
        return "same-origin"
    if (source.scheme or "").lower() == (target.scheme or "").lower() and _site_of(
        source.hostname or ""
    ) == _site_of(target.hostname or ""):
        return "same-site"
    return "cross-site"


# ---------------------------------------------------------------------------
# Responses


class HttpResponse:
    """A response whose body has not been read yet."""

    def __init__(
        self,
        *,
        client: "HttpClient",
        response: requests.Response,
        method: str,
        request_headers: list[tuple[str, str]],
        hops: list[Hop],
        notes: list[str],
        deadline: Deadline,
        started: float,
    ) -> None:
        self._client = client
        self.raw = response
        self.status: int = int(response.status_code)
        self.reason: str = str(response.reason or "")
        self.http_version = _http_version(response.raw)
        self.url: str = str(response.url or "")
        self.method = method
        self.request_headers = request_headers
        try:
            self.headers: list[tuple[str, str]] = [
                (str(name), str(value)) for name, value in response.raw.headers.items()
            ]
        except Exception:
            self.headers = [(str(k), str(v)) for k, v in response.headers.items()]
        self.hops = hops
        self.notes = notes
        self.deadline = deadline
        self.elapsed_ms = int((time.monotonic() - started) * 1000)
        self._started = started
        self.content_type = self.header("content-type") or ""
        self.mime = parse_content_type(self.content_type)[0]
        self.bytes_read = 0
        self.timed_out = False
        self.closed = False
        self._prefix = b""
        self._iterator: Iterator[bytes] | None = None
        self._exhausted = False
        self._lock = threading.Lock()
        self.remote_address = self._peer()
        self._timer: threading.Timer | None = None
        remaining = deadline.remaining()
        if remaining is not None:
            self._timer = threading.Timer(max(0.0, remaining), self._on_deadline)
            self._timer.daemon = True
            self._timer.start()
        client._track(self)

    # -- headers

    def header(self, name: str) -> str | None:
        key = name.lower()
        for header_name, value in self.headers:
            if header_name.lower() == key:
                return value
        return None

    def header_values(self, name: str) -> list[str]:
        key = name.lower()
        return [value for header_name, value in self.headers if header_name.lower() == key]

    @property
    def content_length(self) -> int | None:
        value = self.header("content-length")
        try:
            length = int(str(value).strip()) if value is not None else None
        except ValueError:
            return None
        return length if length is not None and length >= 0 else None

    @property
    def encoded(self) -> bool:
        encoding = (self.header("content-encoding") or "").strip().lower()
        return encoding not in ("", "identity")

    def headers_info(
        self,
        *,
        charset: str | None = None,
        charset_source: str | None = None,
        size: int | None = None,
    ) -> HeadersInfo:
        return HeadersInfo(
            method=self.method,
            url=display_url(self.url),
            status=self.status,
            reason=self.reason,
            http_version=self.http_version,
            request_headers=mask_headers(self.request_headers),
            response_headers=mask_headers(self.headers),
            redirects=list(self.hops),
            mime=self.mime,
            charset=charset,
            charset_source=charset_source,
            bytes=size,
            elapsed_ms=self.elapsed_ms,
            remote_address=self.remote_address,
        )

    # -- socket and deadline

    def _socket(self) -> Any:
        raw = getattr(self.raw, "raw", None)
        connection = getattr(raw, "connection", None) or getattr(raw, "_connection", None)
        return getattr(connection, "sock", None)

    def _peer(self) -> str | None:
        try:
            sock = self._socket()
            if sock is None:
                return None
            address = sock.getpeername()
        except (OSError, AttributeError, ValueError):
            return None
        if isinstance(address, tuple) and address:
            return str(address[0])
        return None

    def shutdown_socket(self) -> None:
        sock = self._socket()
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _on_deadline(self) -> None:
        self.timed_out = True
        self.shutdown_socket()

    def _what(self) -> str:
        return f"загрузка {display_url(self.url)}"

    def _read_error(self, exc: BaseException) -> BrowserError:
        if self.timed_out:
            return _timeout_error(self.deadline, self._what(), time.monotonic() - self._started)
        if isinstance(exc, BrowserError):
            return exc
        if isinstance(exc, requests.exceptions.Timeout):
            return _timeout_error(self.deadline, self._what(), time.monotonic() - self._started)
        logger.debug("body read of %s failed: %s", display_url(self.url), type(exc).__name__)
        return BrowserNetworkError(
            E_BROKEN_READ.format(url=display_url(self.url), n=self.bytes_read)
        )

    # -- body

    def _next_chunk(self) -> bytes | None:
        if self._exhausted:
            return None
        if self._iterator is None:
            self._iterator = self.raw.iter_content(chunk_size=_CHUNK)
        try:
            self.deadline.check(self._what())
        except BrowserTimeout:
            self.close()
            raise
        try:
            data = next(self._iterator)
        except StopIteration:
            self._exhausted = True
            return None
        except Exception as exc:
            error = self._read_error(exc)
            self.close()
            raise error from None
        if self.timed_out:
            self.close()
            raise _timeout_error(self.deadline, self._what(), time.monotonic() - self._started)
        self.bytes_read += len(data)
        return data

    def peek(self, size: int) -> bytes:
        """Read ahead at least ``size`` bytes (or to the end) without consuming them."""
        while len(self._prefix) < size:
            data = self._next_chunk()
            if data is None:
                break
            self._prefix += data
        return self._prefix[:size]

    def iter_body(self, chunk: int = _CHUNK) -> Iterator[bytes]:
        """Decoded body chunks; the deadline is checked between chunks."""
        if self._prefix:
            data, self._prefix = self._prefix, b""
            yield data
        while True:
            data = self._next_chunk()
            if data is None:
                return
            if data:
                yield data

    def read_body(self, max_bytes: int) -> tuple[bytes, dict | None]:
        """The body up to ``max_bytes`` (0: all) and the cut description when it was cut."""
        buffer = bytearray()
        cut: dict | None = None
        limit = int(max_bytes or 0)
        try:
            for data in self.iter_body():
                if limit and len(buffer) + len(data) > limit:
                    buffer += data[: limit - len(buffer)]
                    cut = {
                        "reason": "max_bytes",
                        "bytes_read": len(buffer),
                        "content_length": None if self.encoded else self.content_length,
                    }
                    break
                buffer += data
        finally:
            self.close()
        return bytes(buffer), cut

    def close(self) -> None:
        with self._lock:
            if self.closed:
                return
            self.closed = True
        if self._timer is not None:
            self._timer.cancel()
        try:
            self.raw.close()
        except Exception:
            pass
        self._client._untrack(self)


# ---------------------------------------------------------------------------
# Client


class HttpClient:
    """The HTTP side of one browser session: connection pool, cookie jar, settings."""

    def __init__(self, settings: Callable[[], SessionSettings]) -> None:
        self._settings = settings
        self.session = requests.Session()
        self.session.trust_env = True
        self.session.headers.clear()
        self.session.auth = _UrlCredentialsAuth()
        self.cookies = self.session.cookies
        self.closed = False
        self._active: set[HttpResponse] = set()
        self._active_lock = threading.Lock()

    # -- lifecycle

    def _track(self, response: HttpResponse) -> None:
        with self._active_lock:
            self._active.add(response)

    def _untrack(self, response: HttpResponse) -> None:
        with self._active_lock:
            self._active.discard(response)

    def abort(self) -> None:
        """Shut down every active socket and refuse further requests (any thread)."""
        self.closed = True
        with self._active_lock:
            active = list(self._active)
        for response in active:
            response.shutdown_socket()
        try:
            self.session.close()
        except Exception:
            pass

    def close(self) -> None:
        self.closed = True
        with self._active_lock:
            active = list(self._active)
        for response in active:
            response.close()
        try:
            self.session.close()
        except Exception:
            pass

    def _check_open(self) -> None:
        if self.closed:
            raise BrowserNetworkError("HTTP-клиент браузерной сессии закрыт; повторите действие")

    # -- headers

    def _base_headers(self, spec: RequestSpec, settings: SessionSettings, url: str) -> _HeaderSet:
        headers = _HeaderSet()
        headers.set("User-Agent", settings.http_user_agent())
        for name, value in settings.client_hints():
            headers.set(name, value)
        headers.set("Accept", "*/*" if spec.mode == "api" else DOCUMENT_ACCEPT)
        headers.set("Accept-Language", settings.accept_language)
        headers.set("Accept-Encoding", "identity" if spec.mode == "download" else ACCEPT_ENCODING)
        if spec.mode == "navigate":
            headers.set("Upgrade-Insecure-Requests", "1")
            headers.set("Sec-Fetch-Dest", "document")
            headers.set("Sec-Fetch-Mode", "navigate")
            headers.set("Sec-Fetch-Site", fetch_site(spec.referrer, url))
            if spec.user_initiated:
                headers.set("Sec-Fetch-User", "?1")
        elif spec.mode == "api":
            headers.set("Sec-Fetch-Mode", "cors")
            headers.set("Sec-Fetch-Dest", "empty")
        if spec.referrer:
            headers.set("Referer", spec.referrer)
        if spec.origin:
            headers.set("Origin", spec.origin)
        for name, value in settings.extra_headers or []:
            headers.set(name, value)
        for name, value in spec.headers or []:
            headers.set(name, value)
        if spec.content_type:
            headers.set("Content-Type", spec.content_type)
        return headers

    def _proxies(self, settings: SessionSettings) -> dict[str, str]:
        proxy = (settings.proxy or "").strip()
        if not proxy:
            return {}
        if proxy.lower().startswith("socks") and not _socks_available():
            raise BrowserError(E_SOCKS)
        return {"http": proxy, "https": proxy}

    def _prepare(
        self, method: str, url: str, headers: _HeaderSet, body: Any, jar: Any
    ) -> requests.PreparedRequest:
        prepared = requests.PreparedRequest()
        try:
            prepared.prepare(
                method=method,
                url=url,
                headers=headers.to_request(),
                data=body,
                auth=self.session.auth,
                cookies=jar,
            )
        except (
            requests.exceptions.InvalidURL,
            requests.exceptions.MissingSchema,
            requests.exceptions.InvalidSchema,
            requests.exceptions.InvalidHeader,
            requests.exceptions.URLRequired,
            ValueError,
            UnicodeError,
        ) as exc:
            raise BrowserError(E_BAD_REQUEST.format(reason=_reason(exc))) from None
        return prepared

    def _map_error(
        self, exc: BaseException, url: str, deadline: Deadline, proxy: str | None, elapsed: float
    ) -> BaseException:
        if isinstance(exc, BrowserError):
            return exc
        host = _host(url)
        errors = requests.exceptions
        if isinstance(exc, errors.SSLError):
            return BrowserNetworkError(E_TLS.format(host=host, reason=_reason(exc)))
        if isinstance(exc, errors.ProxyError):
            return BrowserNetworkError(
                E_PROXY.format(proxy=display_url(proxy or "из окружения"), reason=_reason(exc))
            )
        if isinstance(exc, errors.Timeout):
            return _timeout_error(deadline, f"запрос {display_url(url)}", elapsed)
        if isinstance(exc, errors.ConnectionError):
            if self.closed:
                return BrowserNetworkError(E_CONNECT.format(host=host, reason="сессия прервана"))
            if _DNS_MARKERS.search(_chain_text(exc)):
                return BrowserNetworkError(E_DNS.format(host=host))
            return BrowserNetworkError(E_CONNECT.format(host=host, reason=_reason(exc)))
        if isinstance(exc, (errors.ChunkedEncodingError, errors.ContentDecodingError, ProtocolError)):
            return BrowserNetworkError(E_BROKEN_READ.format(url=display_url(url), n=0))
        if isinstance(exc, errors.InvalidSchema) and "socks" in str(exc).lower():
            return BrowserError(E_SOCKS)
        if isinstance(
            exc,
            (
                errors.InvalidURL,
                errors.MissingSchema,
                errors.InvalidSchema,
                errors.InvalidHeader,
                errors.URLRequired,
            ),
        ):
            return BrowserError(E_BAD_REQUEST.format(reason=_reason(exc)))
        if isinstance(exc, OSError) and self.closed:
            return BrowserNetworkError(E_CONNECT.format(host=host, reason="сессия прервана"))
        return exc

    def _transmit(
        self,
        prepared: requests.PreparedRequest,
        settings: SessionSettings,
        deadline: Deadline,
        started: float,
    ) -> requests.Response:
        url = str(prepared.url)
        what = f"запрос {display_url(url)}"
        remaining = deadline.remaining()
        if remaining is not None and remaining <= 0:
            deadline.check(what)
            raise _timeout_error(deadline, what, time.monotonic() - started)
        timeout = None if remaining is None else (remaining, remaining)
        proxies = self._proxies(settings)
        verify_option = False if settings.ignore_tls_errors else None
        env = self.session.merge_environment_settings(url, dict(proxies), True, verify_option, None)
        proxy_used = None
        try:
            proxy_used = select_proxy(url, env.get("proxies") or {})
        except Exception:
            proxy_used = None
        try:
            adapter = self.session.get_adapter(url)
            if settings.ignore_tls_errors:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", InsecureRequestWarning)
                    return adapter.send(
                        prepared,
                        stream=True,
                        timeout=timeout,
                        verify=False,
                        cert=env.get("cert"),
                        proxies=env.get("proxies"),
                    )
            return adapter.send(
                prepared,
                stream=True,
                timeout=timeout,
                verify=env.get("verify", True),
                cert=env.get("cert"),
                proxies=env.get("proxies"),
            )
        except Exception as exc:
            mapped = self._map_error(exc, url, deadline, proxy_used, time.monotonic() - started)
            if mapped is exc:
                raise
            raise mapped from None

    @staticmethod
    def _state(method: str, url: str, jar: Any) -> tuple[str, str, str]:
        digest = hashlib.sha1()
        try:
            items = sorted(
                (c.domain or "", c.path or "", c.name or "", c.value or "") for c in jar
            )
        except Exception:
            items = []
        for item in items:
            digest.update("\x1f".join(item).encode("utf-8", "surrogatepass"))
            digest.update(b"\x1e")
        return method, _strip_fragment(url), digest.hexdigest()

    # -- sending

    def send(self, spec: RequestSpec) -> HttpResponse:
        """Send ``spec`` and follow redirects; the body of the result is not read."""
        self._check_open()
        settings = self._settings()
        deadline = spec.deadline or Deadline(None)
        started = time.monotonic()
        url = _with_params(str(spec.url).strip(), list(spec.params or []))
        method = (spec.method or "GET").strip().upper() or "GET"
        headers = self._base_headers(spec, settings, url)
        body: Any = spec.body
        stream = _BodyStream(spec.body_file, spec.body_length) if spec.body_file is not None else None
        jar = self.cookies if spec.use_cookies else requests_cookies.RequestsCookieJar()
        hops: list[Hop] = []
        notes: list[str] = []
        try:
            fragment = urlsplit(url).fragment
        except ValueError as exc:
            raise BrowserError(E_BAD_REQUEST.format(reason=str(exc))) from None
        seen = {self._state(method, url, jar)}
        while True:
            if stream is not None:
                stream.seek(0)
            prepared = self._prepare(method, url, headers, stream if stream is not None else body, jar)
            guard.ensure_allowed(str(prepared.url), hop=len(hops) or None)
            response = self._transmit(prepared, settings, deadline, started)
            try:
                requests_cookies.extract_cookies_to_jar(jar, prepared, response.raw)
            except Exception as exc:
                logger.debug("cookie extraction failed: %s", type(exc).__name__)
            status = int(response.status_code)
            location = None
            if spec.follow_redirects and status in REDIRECT_STATUSES:
                try:
                    location = self.session.get_redirect_target(response)
                except Exception:
                    location = None
            if location:
                current = str(prepared.url)
                if location.startswith("//"):
                    location = f"{urlsplit(current).scheme}:{location}"
                try:
                    target = requote_uri(urljoin(current, location.strip()))
                    target_parts = urlsplit(target)
                except ValueError as exc:
                    response.close()
                    raise BrowserError(E_BAD_REQUEST.format(reason=str(exc))) from None
                if target_parts.fragment:
                    fragment = target_parts.fragment
                elif fragment:
                    target = f"{target}#{fragment}"
                scheme = (target_parts.scheme or "").lower()
                if scheme not in ("http", "https"):
                    notes.append(
                        f"Перенаправление на {scheme or '?'}: не выполняется (поддерживаются http и https)"
                    )
                    return self._wrap(response, prepared, method, hops, notes, deadline, started)
                new_method = method
                if status == 303 and method != "HEAD":
                    new_method = "GET"
                elif status in (301, 302) and method == "POST":
                    new_method = "GET"
                next_hops = len(hops) + 1
                if spec.max_redirects and next_hops > spec.max_redirects:
                    response.close()
                    raise BrowserNetworkError(
                        E_TOO_MANY_REDIRECTS.format(n=spec.max_redirects, url=display_url(target))
                    )
                state = self._state(new_method, target, jar)
                if state in seen:
                    response.close()
                    raise BrowserNetworkError(
                        E_REDIRECT_CYCLE.format(url=display_url(target), n=next_hops)
                    )
                seen.add(state)
                hops.append(Hop(status=status, url=display_url(current), method=method))
                response.close()
                if new_method == "GET" and method != "GET":
                    body = None
                    stream = None
                    for name in _CONTENT_HEADERS:
                        headers.discard(name)
                if self.session.should_strip_auth(current, target):
                    headers.discard("Authorization")
                    headers.discard("Proxy-Authorization")
                    headers.discard("Cookie")
                method = new_method
                url = target
                continue
            result = self._wrap(response, prepared, method, hops, notes, deadline, started)
            if spec.follow_refresh and spec.mode == "navigate" and 200 <= status < 300:
                target = self._refresh_target(result)
                if target:
                    next_hops = len(hops) + 1
                    if spec.max_redirects and next_hops > spec.max_redirects:
                        result.close()
                        raise BrowserNetworkError(
                            E_TOO_MANY_REDIRECTS.format(n=spec.max_redirects, url=display_url(target[0]))
                        )
                    state = self._state("GET", target[0], jar)
                    if state in seen:
                        result.close()
                        raise BrowserNetworkError(
                            E_REDIRECT_CYCLE.format(url=display_url(target[0]), n=next_hops)
                        )
                    seen.add(state)
                    hops.append(Hop(status="refresh", url=display_url(str(prepared.url)), method=method))
                    delay = target[1]
                    notes.append(
                        f"Выполнено обновление Refresh (задержка {delay:g} с) → {display_url(target[0])}"
                    )
                    result.close()
                    method = "GET"
                    body = None
                    stream = None
                    for name in _CONTENT_HEADERS:
                        headers.discard(name)
                    url = target[0]
                    fragment = urlsplit(url).fragment
                    continue
            return result

    def _wrap(
        self,
        response: requests.Response,
        prepared: requests.PreparedRequest,
        method: str,
        hops: list[Hop],
        notes: list[str],
        deadline: Deadline,
        started: float,
    ) -> HttpResponse:
        request_headers = [
            (str(name), str(value))
            for name, value in prepared.headers.items()
            if value != SKIP_HEADER
        ]
        result = HttpResponse(
            client=self,
            response=response,
            method=method,
            request_headers=request_headers,
            hops=list(hops),
            notes=list(notes),
            deadline=deadline,
            started=started,
        )
        logger.debug(
            "%s %s -> %s (%d hops)", method, display_url(result.url), result.status, len(hops)
        )
        return result

    def _refresh_target(self, result: HttpResponse) -> tuple[str, float] | None:
        """A Refresh header or early meta refresh pointing to another http(s) URL."""
        parsed = parse_refresh(result.header("refresh"))
        if (parsed is None or parsed[1] is None) and result.mime in HTML_MIMES:
            head = result.peek(_REFRESH_PEEK)
            if head:
                text = decode_body(head, result.content_type, "html")[0]
                parsed = meta_refresh(text)
        if parsed is None or not parsed[1]:
            return None
        try:
            target = requote_uri(urljoin(result.url, parsed[1]))
            scheme = (urlsplit(target).scheme or "").lower()
        except ValueError:
            return None
        if scheme not in ("http", "https"):
            return None
        if _strip_fragment(target) == _strip_fragment(result.url):
            return None
        return target, parsed[0]

    # -- documents

    def load(self, spec: RequestSpec) -> LoadedDocument:
        """Fetch a document for a tab: http(s), file:, data: or about:blank."""
        url = str(spec.url or "").strip()
        lowered = url.lower()
        if lowered == "about:blank" or lowered.startswith("about:blank#"):
            return blank_document(spec)
        try:
            scheme = (urlsplit(url).scheme or "").lower()
        except ValueError as exc:
            raise BrowserError(E_BAD_REQUEST.format(reason=str(exc))) from None
        if scheme == "file":
            return load_file_url(url, spec=spec)
        if scheme == "data":
            return load_data_url(url, spec=spec)
        if scheme not in ("http", "https"):
            raise BrowserError(E_URL_SCHEME)
        started = time.monotonic()
        fetched_at = time.time()
        response = self.send(spec)
        try:
            return self._document(response, spec, started, fetched_at)
        finally:
            response.close()

    def _document(
        self, response: HttpResponse, spec: RequestSpec, started: float, fetched_at: float
    ) -> LoadedDocument:
        content_type = response.content_type
        mime = response.mime
        disposition = response.header("content-disposition")
        attachment = bool(
            disposition and disposition.split(";", 1)[0].strip().lower() == "attachment"
        )
        notes = list(response.notes)
        binary: BinaryInfo | None = None
        text: str | None = None
        charset = ""
        charset_source = ""
        size: int | None
        if response.method == "HEAD":
            kind = "empty"
            text = ""
            size = 0
            response.close()
        else:
            head = response.peek(512) if mime in GENERIC_MIMES else None
            kind = classify(mime, head)
            if kind == "binary" or (attachment and kind != "empty"):
                name, _source = filename_from_response(disposition, response.url, content_type)
                binary = BinaryInfo(
                    mime=mime or "application/octet-stream",
                    length=response.content_length,
                    filename=name,
                    disposition=disposition,
                )
                kind = "binary"
                size = response.content_length
                response.close()
                notes.append(
                    f"Тело ответа не загружено: это файл ({binary.mime}); скачайте его через browser_download"
                )
            else:
                raw, cut = response.read_body(spec.max_bytes)
                size = len(raw)
                if not raw:
                    kind = "empty"
                    text = ""
                else:
                    text, charset, charset_source, had_errors = decode_body(raw, content_type, kind)
                    if had_errors:
                        notes.append(f"в тексте есть неразборчивые байты ({charset})")
                if cut:
                    total = cut.get("content_length")
                    notes.append(
                        E_BODY_CUT.format(
                            read=cut["bytes_read"],
                            total=total if total is not None else "неизвестно скольких",
                            n=spec.max_bytes,
                        )
                    )
                if kind == "html" and text:
                    refresh = meta_refresh(text[:_REFRESH_SCAN])
                    if refresh and refresh[1]:
                        try:
                            target = urljoin(response.url, refresh[1])
                        except ValueError:
                            target = refresh[1]
                        if _strip_fragment(target) != _strip_fragment(response.url):
                            notes.append(
                                f"На странице есть meta refresh → {display_url(target)} "
                                f"(через {refresh[0]:g} с); он не выполнен: browser_open(url='{display_url(target)}')"
                            )
        if response.status >= 400:
            notes.append(f"Сервер ответил {response.status} {response.reason}".rstrip())
        if response.status in (429, 503):
            retry_after = response.header("retry-after")
            if retry_after:
                notes.append(f"Сервер просит повторить позже: Retry-After {retry_after.strip()}")
        headers = response.headers_info(
            charset=charset or None, charset_source=charset_source or None, size=size
        )
        logger.debug(
            "loaded %s: %s %s, %s bytes", display_url(response.url), response.status, kind, size
        )
        return LoadedDocument(
            url=response.url,
            status=response.status,
            reason=response.reason,
            method=response.method,
            mime=mime,
            content_type=content_type,
            kind=kind,
            charset=charset,
            charset_source=charset_source,
            text=text,
            size=size,
            binary=binary,
            headers=headers,
            notes=notes,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            fetched_at=fetched_at,
            spec=spec,
            referrer_policy=normalize_referrer_policy(
                ",".join(response.header_values("referrer-policy")) or None
            ),
        )

    def stream_to_file(self, spec: RequestSpec, path: str, *, overwrite: bool) -> dict:
        """Stream a response body to ``path`` (file or directory) through an atomic writer."""
        started = time.monotonic()
        spec = replace(spec, mode="download")
        response = self.send(spec)
        try:
            if response.status >= 400:
                error_type = BrowserNotFound if response.status in (404, 410) else BrowserNetworkError
                raise error_type(
                    E_DOWNLOAD_STATUS.format(
                        status=response.status,
                        reason=response.reason,
                        url=display_url(response.url),
                    )
                )
            length = None if response.encoded else response.content_length
            if spec.max_bytes and length is not None and length > spec.max_bytes:
                raise BrowserError(E_MAX_BYTES.format(n=spec.max_bytes, got=human_bytes(length)))
            disposition = response.header("content-disposition")
            name, source = filename_from_response(disposition, response.url, response.content_type)
            final = resolve_target(
                path, suggested_name=name, content_type=response.content_type, overwrite=overwrite
            )
            free = disk_free(os.path.dirname(final))
            if length is not None and free is not None and length > free:
                raise disk_full_error(final, length)
            with AtomicWriter(final, overwrite=overwrite) as writer:
                for chunk in response.iter_body():
                    if spec.max_bytes and writer.bytes + len(chunk) > spec.max_bytes:
                        raise BrowserError(
                            E_MAX_BYTES.format(
                                n=spec.max_bytes,
                                got=f"больше {human_bytes(writer.bytes + len(chunk))}",
                            )
                        )
                    writer.write(chunk)
                size, sha256 = writer.commit()
        finally:
            response.close()
        logger.debug("downloaded %s -> %s (%d bytes)", display_url(response.url), final, size)
        return {
            "path": final,
            "bytes": size,
            "sha256": sha256,
            "content_type": response.content_type,
            "url": display_url(response.url),
            "status": response.status,
            "filename_source": source,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "free_bytes": disk_free(os.path.dirname(final)),
            "redirects": hop_lines(response.hops, response.url),
        }


# ---------------------------------------------------------------------------
# Non-network documents


def _empty_headers(method: str, url: str, mime: str, size: int | None) -> HeadersInfo:
    response_headers: list[tuple[str, str]] = []
    if mime:
        response_headers.append(("Content-Type", mime))
    if size is not None:
        response_headers.append(("Content-Length", str(size)))
    return HeadersInfo(
        method=method,
        url=display_url(url),
        status=None,
        reason="",
        http_version=None,
        request_headers=[],
        response_headers=response_headers,
        redirects=[],
        mime=mime,
        charset=None,
        charset_source=None,
        bytes=size,
        elapsed_ms=0,
        remote_address=None,
    )


def blank_document(spec: RequestSpec) -> LoadedDocument:
    return LoadedDocument(
        url="about:blank",
        status=None,
        reason="",
        method="GET",
        mime="text/html",
        content_type="text/html",
        kind="empty",
        charset="utf-8",
        charset_source="",
        text="",
        size=0,
        binary=None,
        headers=_empty_headers("GET", "about:blank", "text/html", 0),
        notes=[],
        elapsed_ms=0,
        fetched_at=time.time(),
        spec=spec,
    )


def file_url(path: str) -> str:
    """file:// URL of a local absolute path."""
    return "file://" + quote(path, safe="/:@!$&'()*+,;=~-._")


def _local_path(url: str) -> str:
    parts = urlsplit(url)
    netloc = (parts.netloc or "").lower()
    if netloc and netloc != "localhost":
        raise BrowserNotFound(E_FILE_MISSING.format(path=url))
    return unquote(parts.path or "")


def _listing_html(directory: str) -> str:
    title = f"Папка {directory}"
    rows: list[str] = []
    parent = os.path.dirname(directory.rstrip(os.sep)) or os.sep
    if parent != directory:
        rows.append(f'<li><a href="{html_lib.escape(file_url(parent))}">../</a> — на уровень выше</li>')
    entries: list[tuple[int, str, str]] = []
    try:
        with os.scandir(directory) as iterator:
            for entry in iterator:
                try:
                    info = entry.stat(follow_symlinks=True)
                    is_dir = stat.S_ISDIR(info.st_mode)
                    size = info.st_size
                    modified = datetime.fromtimestamp(info.st_mtime, timezone.utc).strftime(
                        "%Y-%m-%d %H:%M UTC"
                    )
                except OSError:
                    is_dir, size, modified = False, 0, "?"
                label = entry.name + ("/" if is_dir else "")
                detail = (
                    f"папка, изменена {modified}" if is_dir else f"{size} байт, изменён {modified}"
                )
                row = (
                    f'<li><a href="{html_lib.escape(file_url(entry.path))}">'
                    f"{html_lib.escape(label)}</a> — {html_lib.escape(detail)}</li>"
                )
                entries.append((0 if is_dir else 1, entry.name.casefold(), row))
    except OSError as exc:
        rows.append(f"<li>Не удалось прочитать папку: {html_lib.escape(str(exc))}</li>")
    entries.sort(key=lambda item: (item[0], item[1]))
    rows.extend(item[2] for item in entries)
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<title>{html_lib.escape(title)}</title></head><body>"
        f"<h1>{html_lib.escape(title)}</h1><ul>{''.join(rows)}</ul></body></html>"
    )


def load_file_url(url: str, *, spec: RequestSpec | None = None) -> LoadedDocument:
    """A local file or directory listing for a file:// URL (path rules of the file tools)."""
    started = time.monotonic()
    spec = spec or RequestSpec(url=url)
    local = _local_path(url)
    try:
        absolute = resolve_tool_path(local)
    except PermissionError as exc:
        raise BrowserBlocked(str(exc)) from None
    real_url = file_url(absolute)
    notes: list[str] = []
    if os.path.isdir(absolute):
        text = _listing_html(absolute)
        size = len(text.encode("utf-8"))
        return LoadedDocument(
            url=real_url,
            status=None,
            reason="",
            method="GET",
            mime="text/html",
            content_type="text/html; charset=utf-8",
            kind="html",
            charset="utf-8",
            charset_source="header",
            text=text,
            size=size,
            binary=None,
            headers=_empty_headers("GET", real_url, "text/html", size),
            notes=notes,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            fetched_at=time.time(),
            spec=spec,
        )
    if not os.path.isfile(absolute):
        raise BrowserNotFound(E_FILE_MISSING.format(path=absolute))
    size = os.path.getsize(absolute)
    mime = mimetypes.guess_type(absolute, strict=False)[0] or ""
    with open(absolute, "rb") as handle:
        head = handle.read(512)
        kind = classify(mime, head)
        if kind == "binary":
            binary = BinaryInfo(
                mime=mime or "application/octet-stream",
                length=size,
                filename=os.path.basename(absolute),
                disposition=None,
            )
            notes.append(
                f"Файл не загружен в страницу: это {binary.mime}; скопируйте его файловыми инструментами"
            )
            return LoadedDocument(
                url=real_url,
                status=None,
                reason="",
                method="GET",
                mime=binary.mime,
                content_type=binary.mime,
                kind="binary",
                charset="",
                charset_source="",
                text=None,
                size=size,
                binary=binary,
                headers=_empty_headers("GET", real_url, binary.mime, size),
                notes=notes,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                fetched_at=time.time(),
                spec=spec,
            )
        handle.seek(0)
        limit = int(spec.max_bytes or 0)
        raw = handle.read(limit) if limit else handle.read()
    if limit and size > limit:
        notes.append(E_BODY_CUT.format(read=len(raw), total=size, n=limit))
    text, charset, source, had_errors = ("", "utf-8", "", False)
    if raw:
        text, charset, source, had_errors = decode_body(raw, mime, kind)
    else:
        kind = "empty"
    if had_errors:
        notes.append(f"в тексте есть неразборчивые байты ({charset})")
    return LoadedDocument(
        url=real_url,
        status=None,
        reason="",
        method="GET",
        mime=mime,
        content_type=mime,
        kind=kind,
        charset=charset,
        charset_source=source,
        text=text,
        size=size,
        binary=None,
        headers=_empty_headers("GET", real_url, mime, size),
        notes=notes,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        fetched_at=time.time(),
        spec=spec,
    )


def decode_data_url(url: str) -> tuple[bytes, str]:
    """Bytes and media type of a data: URL (default text/plain;charset=US-ASCII)."""
    text = str(url or "")
    _scheme, _sep, rest = text.partition(":")
    rest = rest.split("#", 1)[0]
    if "," not in rest:
        raise BrowserError(
            E_BAD_REQUEST.format(reason="в адресе data: нет запятой между типом и данными")
        )
    meta, data = rest.split(",", 1)
    meta = meta.strip()
    is_base64 = False
    match = re.search(r";\s*base64\s*$", meta, re.I)
    if match:
        is_base64 = True
        meta = meta[: match.start()]
    media_type = meta.strip()
    if not media_type:
        media_type = "text/plain;charset=US-ASCII"
    elif media_type.startswith(";"):
        media_type = "text/plain" + media_type
    payload = unquote_to_bytes(data)
    if is_base64:
        compact = re.sub(rb"[\t\n\f\r ]+", b"", payload)
        compact += b"=" * (-len(compact) % 4)
        try:
            payload = base64.b64decode(compact, altchars=None, validate=False)
        except (binascii.Error, ValueError) as exc:
            raise BrowserError(
                E_BAD_REQUEST.format(reason=f"данные base64 в адресе data: повреждены ({exc})")
            ) from None
    return payload, media_type


def load_data_url(url: str, *, spec: RequestSpec | None = None) -> LoadedDocument:
    spec = spec or RequestSpec(url=url)
    payload, media_type = decode_data_url(url)
    mime = parse_content_type(media_type)[0]
    kind = classify(mime, payload[:512])
    notes: list[str] = []
    size = len(payload)
    limit = int(spec.max_bytes or 0)
    if kind == "binary":
        binary = BinaryInfo(
            mime=mime or "application/octet-stream",
            length=size,
            filename="download" + extension_for(mime),
            disposition=None,
        )
        notes.append(
            f"Тело не загружено в страницу: это файл ({binary.mime}); сохраните его через browser_download"
        )
        return LoadedDocument(
            url=url,
            status=None,
            reason="",
            method="GET",
            mime=binary.mime,
            content_type=media_type,
            kind="binary",
            charset="",
            charset_source="",
            text=None,
            size=size,
            binary=binary,
            headers=_empty_headers("GET", url, binary.mime, size),
            notes=notes,
            elapsed_ms=0,
            fetched_at=time.time(),
            spec=spec,
        )
    if limit and size > limit:
        notes.append(E_BODY_CUT.format(read=limit, total=size, n=limit))
        payload = payload[:limit]
    text, charset, source, had_errors = ("", "utf-8", "", False)
    if payload:
        text, charset, source, had_errors = decode_body(payload, media_type, kind)
    if had_errors:
        notes.append(f"в тексте есть неразборчивые байты ({charset})")
    return LoadedDocument(
        url=url,
        status=None,
        reason="",
        method="GET",
        mime=mime,
        content_type=media_type,
        kind=kind,
        charset=charset,
        charset_source=source,
        text=text,
        size=size,
        binary=None,
        headers=_empty_headers("GET", url, mime, size),
        notes=notes,
        elapsed_ms=0,
        fetched_at=time.time(),
        spec=spec,
    )


# ---------------------------------------------------------------------------
# Form submissions


def _escape_part_name(name: str) -> str:
    return str(name).replace('"', "%22").replace("\r", "%0D").replace("\n", "%0A")


def encode_multipart(
    fields: list[tuple[str, str]],
    files: list[tuple],
    charset: str = "utf-8",
) -> tuple[Any, str, int]:
    """multipart/form-data body in a spooled temporary file: (file, content type, length).

    ``files`` items are (field, local path or None, content type or None[, file name]);
    a None path is an empty file input (filename="" and application/octet-stream).
    """
    encoding = charset or "utf-8"
    boundary = "----LogosFormBoundary" + secrets.token_hex(12)
    out = tempfile.SpooledTemporaryFile(max_size=1 << 20)
    try:
        for name, value in fields:
            head = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{_escape_part_name(name)}"\r\n\r\n'
            )
            out.write(head.encode(encoding, "xmlcharrefreplace"))
            out.write(str(value).encode(encoding, "xmlcharrefreplace"))
            out.write(b"\r\n")
        for item in files:
            field_name = item[0]
            path = item[1] if len(item) > 1 else None
            content_type = item[2] if len(item) > 2 else None
            override = item[3] if len(item) > 3 else None
            if path:
                filename = override or os.path.basename(path)
                content_type = (
                    content_type
                    or mimetypes.guess_type(filename, strict=False)[0]
                    or "application/octet-stream"
                )
            else:
                filename = override or ""
                content_type = content_type or "application/octet-stream"
            head = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{_escape_part_name(field_name)}"; '
                f'filename="{_escape_part_name(filename)}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            )
            out.write(head.encode(encoding, "xmlcharrefreplace"))
            if path:
                try:
                    with open(path, "rb") as handle:
                        while True:
                            chunk = handle.read(_CHUNK)
                            if not chunk:
                                break
                            out.write(chunk)
                except FileNotFoundError:
                    raise BrowserNotFound(E_FILE_MISSING.format(path=path)) from None
            out.write(b"\r\n")
        out.write(f"--{boundary}--\r\n".encode("ascii"))
        length = out.tell()
        out.seek(0)
    except BaseException:
        out.close()
        raise
    return out, f"multipart/form-data; boundary={boundary}", length


def _submission_pairs(sub: FormSubmission) -> list[tuple[str, str]]:
    """Entry list for encodings without files: a file input contributes its file name."""
    pairs = [(str(name), str(value)) for name, value in sub.fields]
    for item in sub.files:
        path = item[1] if len(item) > 1 else None
        pairs.append((str(item[0]), os.path.basename(path) if path else ""))
    return pairs


def encode_submission(
    sub: FormSubmission,
    *,
    referrer: str | None,
    origin: str | None,
    deadline: Deadline,
    max_redirects: int = 0,
    max_bytes: int = 0,
) -> RequestSpec:
    """RequestSpec of a form submission (navigation; Origin only for POST)."""
    method = (sub.method or "GET").strip().upper()
    charset = sub.charset or "utf-8"
    common: dict[str, Any] = {
        "referrer": referrer,
        "mode": "navigate",
        "user_initiated": True,
        "follow_refresh": True,
        "deadline": deadline,
        "max_redirects": max_redirects,
        "max_bytes": max_bytes,
    }
    if method != "POST":
        parts = urlsplit(sub.url)
        query = urlencode(_submission_pairs(sub), encoding=charset, errors="xmlcharrefreplace")
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
        return RequestSpec(url=url, method="GET", origin=None, **common)
    enctype = (sub.enctype or "application/x-www-form-urlencoded").strip().lower()
    if enctype == "multipart/form-data":

        def rebuild() -> RequestSpec:
            return encode_submission(
                sub,
                referrer=referrer,
                origin=origin,
                deadline=deadline,
                max_redirects=max_redirects,
                max_bytes=max_bytes,
            )

        body_file, content_type, length = encode_multipart(list(sub.fields), list(sub.files), charset)
        return RequestSpec(
            url=sub.url,
            method="POST",
            origin=origin,
            body_file=body_file,
            body_length=length,
            content_type=content_type,
            rebuild=rebuild,
            **common,
        )
    if enctype == "text/plain":
        text = "".join(f"{name}={value}\r\n" for name, value in _submission_pairs(sub))
        return RequestSpec(
            url=sub.url,
            method="POST",
            origin=origin,
            body=text.encode(charset, "xmlcharrefreplace"),
            content_type="text/plain",
            **common,
        )
    body = urlencode(_submission_pairs(sub), encoding=charset, errors="xmlcharrefreplace")
    return RequestSpec(
        url=sub.url,
        method="POST",
        origin=origin,
        body=body.encode("ascii"),
        content_type="application/x-www-form-urlencoded",
        **common,
    )


def replay_spec(spec: RequestSpec, *, deadline: Deadline, **changes: Any) -> RequestSpec:
    """The same request again with a fresh deadline (multipart bodies re-encoded)."""
    fresh = spec.rebuild() if spec.rebuild is not None else spec
    if fresh.body_file is not None and fresh is spec:
        try:
            fresh.body_file.seek(0)
        except (OSError, ValueError, AttributeError):
            pass
    return replace(fresh, deadline=deadline, **changes)


# ---------------------------------------------------------------------------
# Cookies


def _rest(cookie: Any) -> dict:
    rest = getattr(cookie, "_rest", None)
    return rest if isinstance(rest, dict) else {}


def _http_only(cookie: Any) -> bool:
    return any(str(key).lower() == "httponly" for key in _rest(cookie))


def _same_site(cookie: Any) -> str | None:
    for key, value in _rest(cookie).items():
        if str(key).lower() == "samesite" and value:
            text = str(value).strip().capitalize()
            return text if text in ("Strict", "Lax", "None") else str(value)
    return None


def _domain_matches(cookie_domain: str, wanted: str) -> bool:
    domain = (cookie_domain or "").lower().lstrip(".")
    target = (wanted or "").strip().lower().lstrip(".")
    if not target:
        return True
    return domain == target or domain.endswith("." + target)


def _cookie_matches_url(cookie: Any, url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    domain = (cookie.domain or "").lower()
    bare = domain.lstrip(".")
    if bare:
        if domain.startswith("."):
            if not (host == bare or host.endswith("." + bare)):
                return False
        elif host != bare:
            return False
    path = parts.path or "/"
    cookie_path = cookie.path or "/"
    if not (
        path == cookie_path
        or (path.startswith(cookie_path) and (cookie_path.endswith("/") or path[len(cookie_path)] == "/"))
    ):
        return False
    if cookie.secure and (parts.scheme or "").lower() != "https":
        return False
    try:
        if cookie.is_expired():
            return False
    except Exception:
        pass
    return True


def cookie_rows(jar: Any, *, domain: str | None = None, url: str | None = None) -> list[dict]:
    """Cookie descriptions without values: length and a sha256 fingerprint only."""
    rows: list[dict] = []
    for cookie in list(jar):
        if domain and not _domain_matches(cookie.domain, domain):
            continue
        if url and not _cookie_matches_url(cookie, url):
            continue
        value = cookie.value or ""
        rows.append(
            {
                "name": cookie.name,
                "domain": cookie.domain,
                "path": cookie.path,
                "secure": bool(cookie.secure),
                "http_only": _http_only(cookie),
                "same_site": _same_site(cookie),
                "expires": cookie.expires,
                "session": cookie.expires is None or bool(cookie.discard),
                "length": len(value),
                "fp": fingerprint(value),
            }
        )
    rows.sort(key=lambda row: (str(row["domain"]).lstrip("."), row["path"] or "", row["name"] or ""))
    return rows


def set_cookie(
    jar: Any,
    *,
    name: str,
    value: str,
    domain: str | None = None,
    url: str | None = None,
    path: str = "/",
    secure: bool = False,
    http_only: bool = False,
    same_site: str | None = None,
    expires: float | None = None,
) -> None:
    """Store a cookie: with ``url`` a host-only cookie, with ``domain`` a domain cookie."""
    if url:
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            host = ""
        if not host:
            raise BrowserError(E_BAD_REQUEST.format(reason=f"в адресе cookie нет имени сервера: {display_url(url)}"))
        cookie_domain = host
    else:
        bare = str(domain or "").strip().lower().lstrip(".")
        if not bare:
            raise BrowserError(E_BAD_REQUEST.format(reason="для cookie нужен domain или url"))
        cookie_domain = "." + bare
    rest: dict[str, Any] = {}
    if http_only:
        rest["HttpOnly"] = None
    if same_site:
        rest["SameSite"] = same_site
    expiry = int(expires) if expires else None
    cookie = requests_cookies.create_cookie(
        name,
        value,
        domain=cookie_domain,
        path=path or "/",
        secure=bool(secure),
        expires=expiry,
        discard=expiry is None,
        rest=rest,
    )
    jar.set_cookie(cookie)


def delete_cookies(
    jar: Any, *, name: str | None = None, domain: str | None = None, path: str | None = None
) -> int:
    count = 0
    for cookie in list(jar):
        if name is not None and cookie.name != name:
            continue
        if domain and not _domain_matches(cookie.domain, domain):
            continue
        if path is not None and cookie.path != path:
            continue
        try:
            jar.clear(cookie.domain, cookie.path, cookie.name)
            count += 1
        except KeyError:
            continue
    return count


def clear_cookies(jar: Any, *, domain: str | None = None) -> int:
    if domain:
        return delete_cookies(jar, domain=domain)
    count = len(list(jar))
    jar.clear()
    return count


def cookie_value(jar: Any, name: str, url: str) -> str | None:
    """Value of the cookie ``name`` that a request to ``url`` would carry (longest path wins)."""
    best = None
    for cookie in list(jar):
        if cookie.name != name or not _cookie_matches_url(cookie, url):
            continue
        if best is None or len(cookie.path or "") > len(best.path or ""):
            best = cookie
    return None if best is None else (best.value or "")


def substitute_cookie_placeholders(
    text: str, jar: Any, url: str, on_value: Callable[[str], Any] | None = None
) -> str:
    """Replace {{cookie:NAME}} with the cookie value for ``url`` without exposing it."""
    if not text or "{{" not in text:
        return text

    def replace_one(match: re.Match) -> str:
        name = match.group(1).strip()
        value = cookie_value(jar, name, url)
        if value is None:
            raise BrowserNotFound(E_COOKIE_PLACEHOLDER.format(name=name, host=_host(url)))
        if on_value is not None:
            on_value(value)
        return value

    return _COOKIE_PLACEHOLDER.sub(replace_one, text)


def jar_to_cdp(jar: Any) -> list[dict]:
    """Cookie jar contents as Network.CookieParam objects (host-only cookies by url)."""
    result: list[dict] = []
    for cookie in list(jar):
        domain = cookie.domain or ""
        if not domain or not cookie.name:
            continue
        item: dict[str, Any] = {
            "name": cookie.name,
            "value": cookie.value or "",
            "path": cookie.path or "/",
            "secure": bool(cookie.secure),
            "httpOnly": _http_only(cookie),
        }
        same_site = _same_site(cookie)
        if same_site in ("Strict", "Lax", "None"):
            item["sameSite"] = same_site
        if cookie.expires is not None and not cookie.discard:
            item["expires"] = float(cookie.expires)
        if domain.startswith("."):
            item["domain"] = domain
        else:
            scheme = "https" if cookie.secure else "http"
            host = domain if ":" not in domain else f"[{domain}]"
            item["url"] = f"{scheme}://{host}{cookie.path or '/'}"
        result.append(item)
    return result


def cdp_to_jar(jar: Any, cookies: list[dict]) -> None:
    """Replace the jar contents with Chrome's cookies (Network.Cookie objects)."""
    jar.clear()
    for item in cookies or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if name is None:
            continue
        domain = str(item.get("domain") or "")
        expires_raw = item.get("expires")
        try:
            expires_value = float(expires_raw) if expires_raw is not None else -1.0
        except (TypeError, ValueError):
            expires_value = -1.0
        session_cookie = bool(item.get("session")) or expires_value <= 0
        rest: dict[str, Any] = {}
        if item.get("httpOnly"):
            rest["HttpOnly"] = None
        if item.get("sameSite"):
            rest["SameSite"] = str(item["sameSite"])
        cookie = requests_cookies.create_cookie(
            str(name),
            str(item.get("value") or ""),
            domain=domain,
            path=str(item.get("path") or "/"),
            secure=bool(item.get("secure")),
            expires=None if session_cookie else int(expires_value),
            discard=session_cookie,
            rest=rest,
        )
        jar.set_cookie(cookie)


__all__ = [
    "HTML_MIMES",
    "REDIRECT_STATUSES",
    "HttpClient",
    "HttpResponse",
    "LoadedDocument",
    "RequestSpec",
    "blank_document",
    "cdp_to_jar",
    "clear_cookies",
    "cookie_rows",
    "cookie_value",
    "decode_data_url",
    "delete_cookies",
    "encode_multipart",
    "encode_submission",
    "fetch_site",
    "file_url",
    "hop_lines",
    "jar_to_cdp",
    "load_data_url",
    "load_file_url",
    "meta_refresh",
    "normalize_referrer_policy",
    "origin_of",
    "parse_refresh",
    "referrer_value",
    "replay_spec",
    "set_cookie",
    "substitute_cookie_placeholders",
]
