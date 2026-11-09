"""The request a view reads and the response it returns."""

from __future__ import annotations

import io
import json as _json
from collections.abc import Callable, Iterable, Iterator
from http.cookies import SimpleCookie
from typing import Any, BinaryIO, Self
from urllib.parse import parse_qsl, quote

from webframe.datastructures import Accept, Headers, MultiDict
from webframe.exceptions import (
    BadRequest,
    ClientDisconnected,
    RequestedRangeNotSatisfiable,
    UnsupportedMediaType,
)
from webframe.http import (
    etags_match,
    get_content_type,
    http_date,
    parse_http_date,
    parse_options_header,
    parse_range,
    quote_etag,
    remove_entity_headers,
    status_code,
    status_line,
    unquote_etag,
)

_missing = object()


def _wsgi_decode(value: str) -> str:
    """A WSGI string (bytes carried as latin-1) as the UTF-8 text it is."""
    try:
        return value.encode("latin-1").decode("utf-8", "replace")
    except UnicodeEncodeError:
        return value


class LimitedStream:
    """``size`` bytes of an underlying stream; reading past them returns nothing.

    A stream that ends before ``size`` bytes arrived means the client went away:
    ``ClientDisconnected`` is raised then, so a handler never mistakes a broken
    upload for a complete one.
    """

    def __init__(self, stream: BinaryIO, size: int) -> None:
        self._stream = stream
        self.limit = max(0, size)
        self._position = 0

    @property
    def remaining(self) -> int:
        return self.limit - self._position

    @property
    def is_exhausted(self) -> bool:
        return self._position >= self.limit

    def read(self, size: int = -1) -> bytes:
        remaining = self.remaining
        if remaining <= 0:
            return b""
        to_read = remaining if size is None or size < 0 else min(size, remaining)
        pieces: list[bytes] = []
        got = 0
        while got < to_read:
            chunk = self._stream.read(to_read - got)
            if not chunk:
                self._position = self.limit
                raise ClientDisconnected()
            pieces.append(chunk)
            got += len(chunk)
        self._position += got
        return b"".join(pieces)

    def readline(self, size: int = -1) -> bytes:
        remaining = self.remaining
        if remaining <= 0:
            return b""
        if size is None or size < 0 or size > remaining:
            size = remaining
        line = self._stream.readline(size)
        if not line:
            self._position = self.limit
            raise ClientDisconnected()
        self._position += len(line)
        return line

    def readlines(self) -> list[bytes]:
        lines = []
        while True:
            line = self.readline()
            if not line:
                return lines
            lines.append(line)

    def __iter__(self) -> Iterator[bytes]:
        while True:
            line = self.readline()
            if not line:
                return
            yield line

    def exhaust(self, chunk_size: int = 64 * 1024) -> None:
        while self.remaining > 0:
            self.read(min(chunk_size, self.remaining))

    def close(self) -> None:
        pass


class Request:
    """One HTTP request, read from its WSGI environ."""

    def __init__(self, environ: dict[str, Any]) -> None:
        self.environ = environ
        self.method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        self.path = "/" + _wsgi_decode(environ.get("PATH_INFO", "")).lstrip("/")
        self.script_root = _wsgi_decode(environ.get("SCRIPT_NAME", "")).rstrip("/")
        self.query_string = str(environ.get("QUERY_STRING", "")).encode("latin-1")
        self.headers = Headers(_environ_headers(environ))
        self.remote_addr: str | None = environ.get("REMOTE_ADDR")
        self.scheme = environ.get("wsgi.url_scheme", "http")
        # Set by the application once a rule matched (or failed to).
        self.endpoint: str | None = None
        self.view_args: dict[str, Any] | None = None
        self.url_rule: Any = None
        self.routing_exception: Exception | None = None
        self._args: MultiDict | None = None
        self._stream: Any = None
        self._cached_data: bytes | None = None
        self._cached_json: dict[bool, Any] = {}
        self._form: MultiDict | None = None
        self._files: MultiDict | None = None
        self._cookies: dict[str, str] | None = None

    # the URL

    @property
    def args(self) -> MultiDict:
        if self._args is None:
            text = self.query_string.decode("utf-8", "replace")
            self._args = MultiDict(
                parse_qsl(text, keep_blank_values=True, errors="replace")
            )
        return self._args

    @property
    def full_path(self) -> str:
        query = self.query_string.decode("utf-8", "replace")
        return f"{self.path}?{query}"

    @property
    def host(self) -> str:
        host = self.headers.get("Host")
        if host:
            return host
        name = self.environ.get("SERVER_NAME", "localhost")
        port = str(self.environ.get("SERVER_PORT", ""))
        default = "443" if self.scheme == "https" else "80"
        return name if port in ("", default) else f"{name}:{port}"

    @property
    def host_url(self) -> str:
        return f"{self.scheme}://{self.host}/"

    @property
    def root_url(self) -> str:
        return f"{self.scheme}://{self.host}{self.script_root}/"

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}{self.script_root}{quote(self.path)}"

    @property
    def url(self) -> str:
        if self.query_string:
            return f"{self.base_url}?{self.query_string.decode('latin-1')}"
        return self.base_url

    @property
    def blueprint(self) -> str | None:
        if self.endpoint and "." in self.endpoint:
            return self.endpoint.rpartition(".")[0]
        return None

    # headers

    @property
    def content_type(self) -> str | None:
        return self.headers.get("Content-Type")

    @property
    def mimetype(self) -> str:
        return parse_options_header(self.content_type)[0].lower()

    @property
    def mimetype_params(self) -> dict[str, str]:
        return parse_options_header(self.content_type)[1]

    @property
    def content_length(self) -> int | None:
        raw = self.headers.get("Content-Length")
        if raw is None:
            return None
        try:
            value = int(raw)
        except ValueError:
            return None
        return value if value >= 0 else None

    @property
    def accept_encodings(self) -> Accept:
        return Accept(self.headers.get("Accept-Encoding"))

    @property
    def accept_mimetypes(self) -> Accept:
        return Accept(self.headers.get("Accept"))

    @property
    def cookies(self) -> dict[str, str]:
        if self._cookies is None:
            jar: SimpleCookie = SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie", ""))
            except Exception:
                pass
            self._cookies = {key: morsel.value for key, morsel in jar.items()}
        return self._cookies

    @property
    def is_json(self) -> bool:
        mimetype = self.mimetype
        return mimetype == "application/json" or (
            mimetype.startswith("application/") and mimetype.endswith("+json")
        )

    @property
    def is_secure(self) -> bool:
        return self.scheme == "https"

    # the body

    @property
    def stream(self) -> Any:
        """The request body as a stream, bounded by its Content-Length."""
        if self._stream is None:
            raw = self.environ.get("wsgi.input")
            length = self.content_length
            if raw is None:
                self._stream = io.BytesIO()
            elif length is not None:
                self._stream = LimitedStream(raw, length)
            elif self.environ.get("wsgi.input_terminated"):
                self._stream = raw
            else:
                self._stream = io.BytesIO()
        return self._stream

    def get_data(
        self, cache: bool = True, as_text: bool = False, parse_form_data: bool = False
    ) -> Any:
        """The whole body, read once; ``as_text`` decodes it as UTF-8."""
        if self._cached_data is None:
            # A form parser that consumed the stream left it exhausted: nothing more
            # is read; a form check on a JSON body consumed nothing.
            data = self.stream.read()
            if cache:
                self._cached_data = data
        else:
            data = self._cached_data
        if as_text:
            return data.decode("utf-8", "replace")
        return data

    @property
    def data(self) -> bytes:
        return self.get_data()

    def get_json(
        self, force: bool = False, silent: bool = False, cache: bool = True
    ) -> Any:
        """The body decoded as JSON.

        Without ``force`` a body that is not ``application/json`` is a 415 and
        unreadable JSON a 400; with ``silent`` both are ``None`` instead.
        """
        if cache and silent in self._cached_json:
            return self._cached_json[silent]
        if not (force or self.is_json):
            if silent:
                rv = None
                if cache:
                    self._cached_json[silent] = rv
                return rv
            raise UnsupportedMediaType(
                "Did not attempt to load JSON data because the request "
                "Content-Type was not 'application/json'."
            )
        data = self.get_data(cache=cache)
        try:
            rv = _loads(data)
        except ValueError as error:
            if not silent:
                raise BadRequest(f"Failed to decode JSON object: {error}") from None
            rv = None
        if cache:
            self._cached_json[silent] = rv
        return rv

    @property
    def json(self) -> Any:
        return self.get_json()

    def _load_form(self) -> None:
        if self._form is not None:
            return
        from webframe.formparser import parse_form_data

        if self._cached_data is not None:
            source: Any = io.BytesIO(self._cached_data)
        else:
            source = self.stream
        self._form, self._files = parse_form_data(source, self.content_type)

    @property
    def form(self) -> MultiDict:
        self._load_form()
        return self._form  # type: ignore[return-value]

    @property
    def files(self) -> MultiDict:
        self._load_form()
        return self._files  # type: ignore[return-value]

    @property
    def values(self) -> MultiDict:
        combined = MultiDict(self.args)
        for key, value in self.form.items(multi=True):
            combined.add(key, value)
        return combined

    def close(self) -> None:
        if self._files is not None:
            for storage in self._files.values():
                storage.close()

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.method} {self.path!r}>"


def _environ_headers(environ: dict[str, Any]) -> list[tuple[str, str]]:
    headers: list[tuple[str, str]] = []
    for key, value in environ.items():
        if key.startswith("HTTP_") and key not in (
            "HTTP_CONTENT_TYPE",
            "HTTP_CONTENT_LENGTH",
        ):
            name = key[5:].replace("_", "-").title()
            headers.append((name, _wsgi_decode(str(value))))
        elif key in ("CONTENT_TYPE", "CONTENT_LENGTH") and value:
            headers.append((key.replace("_", "-").title(), str(value)))
    return headers


def _loads(data: bytes) -> Any:
    from webframe.globals import _app_ctx, _request_ctx

    ctx = _request_ctx.get()
    app = ctx.app if ctx is not None else _app_ctx.get()
    if app is not None:
        return app.json.loads(data)
    return _json.loads(data)


class HeaderSet:
    """The comma-separated values of one header (``Vary``), kept in sync with it."""

    def __init__(self, headers: Headers, name: str) -> None:
        self._headers = headers
        self._name = name

    def _values(self) -> list[str]:
        raw = self._headers.get(self._name, "")
        return [item.strip() for item in raw.split(",") if item.strip()]

    def add(self, value: str) -> None:
        values = self._values()
        if value.lower() not in {item.lower() for item in values}:
            values.append(value)
            self._headers[self._name] = ", ".join(values)

    def remove(self, value: str) -> None:
        values = [item for item in self._values() if item.lower() != value.lower()]
        if values:
            self._headers[self._name] = ", ".join(values)
        else:
            self._headers.remove(self._name)

    def __contains__(self, value: object) -> bool:
        return isinstance(value, str) and value.lower() in {
            item.lower() for item in self._values()
        }

    def __iter__(self) -> Iterator[str]:
        return iter(self._values())

    def __len__(self) -> int:
        return len(self._values())

    def to_header(self) -> str:
        return ", ".join(self._values())

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._values()!r})"


class FileIterator:
    """A file read in pieces as a response body; ``close`` closes the file."""

    def __init__(self, file: BinaryIO, buffer_size: int = 8192) -> None:
        self.file = file
        self.buffer_size = buffer_size

    def seekable(self) -> bool:
        seekable = getattr(self.file, "seekable", None)
        return bool(seekable()) if callable(seekable) else hasattr(self.file, "seek")

    def seek(self, *args: Any) -> Any:
        return self.file.seek(*args)

    def tell(self) -> int:
        return self.file.tell()

    def close(self) -> None:
        close = getattr(self.file, "close", None)
        if callable(close):
            close()

    def __iter__(self) -> Iterator[bytes]:
        while True:
            data = self.file.read(self.buffer_size)
            if not data:
                return
            yield data


class _RangeIterator:
    """``length`` bytes of an iterable body starting at ``start``."""

    def __init__(self, body: Any, start: int, length: int) -> None:
        self._body = body
        self._start = start
        self._length = length

    def close(self) -> None:
        close = getattr(self._body, "close", None)
        if callable(close):
            close()

    def __iter__(self) -> Iterator[bytes]:
        remaining = self._length
        if remaining <= 0:
            return
        body = self._body
        seekable = getattr(body, "seekable", None)
        if callable(seekable) and seekable() and hasattr(body, "file"):
            body.seek(self._start)
            size = getattr(body, "buffer_size", 8192)
            while remaining > 0:
                data = body.file.read(min(size, remaining))
                if not data:
                    return
                remaining -= len(data)
                yield data
            return
        skip = self._start
        for piece in body:
            data = piece.encode("utf-8") if isinstance(piece, str) else bytes(piece)
            if skip:
                if len(data) <= skip:
                    skip -= len(data)
                    continue
                data = data[skip:]
                skip = 0
            if len(data) > remaining:
                data = data[:remaining]
            remaining -= len(data)
            if data:
                yield data
            if remaining <= 0:
                return


class _ClosingIterable:
    def __init__(self, iterable: Iterable[bytes], close: Callable[[], None]) -> None:
        self._iterable = iterable
        self._close = close

    def __iter__(self) -> Iterator[bytes]:
        return iter(self._iterable)

    def close(self) -> None:
        self._close()


class Response:
    """One HTTP response: status, headers and a body that is bytes or a stream.

    ``response`` is a list of bytes for a body built at once, or any iterable
    (a generator, a file) for a streamed one; ``is_streamed`` tells them apart.
    """

    default_mimetype: str | None = "text/html"
    default_status = 200
    automatically_set_content_length = True

    def __init__(
        self,
        response: Any = None,
        status: int | str | None = None,
        headers: Any = None,
        mimetype: str | None = None,
        content_type: str | None = None,
        direct_passthrough: bool = False,
    ) -> None:
        self.headers = (
            headers.copy() if isinstance(headers, Headers) else Headers(headers)
        )
        if content_type is None:
            if mimetype is None and "Content-Type" not in self.headers:
                mimetype = self.default_mimetype
            if mimetype is not None:
                content_type = get_content_type(mimetype)
        if content_type is not None:
            self.headers["Content-Type"] = content_type
        self._status = ""
        self._status_code = 0
        self.status = self.default_status if status is None else status
        self.direct_passthrough = direct_passthrough
        self._on_close: list[Callable[[], Any]] = []
        self.response: Any
        if response is None:
            self.response = []
        elif isinstance(response, (str, bytes, bytearray, memoryview)):
            self.set_data(response)
        else:
            self.response = response

    # status

    @property
    def status(self) -> str:
        return self._status

    @status.setter
    def status(self, value: int | str) -> None:
        self._status = status_line(value)
        self._status_code = status_code(self._status)

    @property
    def status_code(self) -> int:
        return self._status_code

    @status_code.setter
    def status_code(self, code: int) -> None:
        self.status = int(code)

    # body

    @property
    def is_sequence(self) -> bool:
        return isinstance(self.response, (list, tuple))

    @property
    def is_streamed(self) -> bool:
        return not self.is_sequence

    def iter_encoded(self) -> Iterator[bytes]:
        for item in self.response:
            if isinstance(item, str):
                yield item.encode("utf-8")
            else:
                yield bytes(item)

    def __iter__(self) -> Iterator[bytes]:
        return self.iter_encoded()

    def make_sequence(self) -> None:
        """Read a streamed body into memory, so it can be inspected or changed."""
        if self.is_sequence:
            return
        if self.direct_passthrough:
            raise RuntimeError(
                "Attempted implicit sequence conversion but the response object "
                "is in direct passthrough mode."
            )
        close = getattr(self.response, "close", None)
        self.response = list(self.iter_encoded())
        if callable(close):
            self.call_on_close(close)

    def get_data(self, as_text: bool = False) -> Any:
        self.make_sequence()
        data = b"".join(self.iter_encoded())
        return data.decode("utf-8", "replace") if as_text else data

    def set_data(self, value: Any) -> None:
        if isinstance(value, str):
            value = value.encode("utf-8")
        else:
            value = bytes(value)
        self.response = [value]
        if self.automatically_set_content_length:
            self.headers["Content-Length"] = str(len(value))

    @property
    def data(self) -> bytes:
        return self.get_data()

    @data.setter
    def data(self, value: Any) -> None:
        self.set_data(value)

    @property
    def text(self) -> str:
        return self.get_data(as_text=True)

    def calculate_content_length(self) -> int | None:
        if not self.is_sequence:
            return None
        return sum(len(piece) for piece in self.iter_encoded())

    def call_on_close(self, func: Callable[[], Any]) -> Callable[[], Any]:
        self._on_close.append(func)
        return func

    def close(self) -> None:
        close = getattr(self.response, "close", None)
        if callable(close):
            close()
        for func in self._on_close:
            func()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # headers

    @property
    def mimetype(self) -> str | None:
        content_type = self.headers.get("Content-Type")
        if content_type is None:
            return None
        return content_type.split(";", 1)[0].strip()

    @mimetype.setter
    def mimetype(self, value: str) -> None:
        self.headers["Content-Type"] = get_content_type(value)

    @property
    def content_type(self) -> str | None:
        return self.headers.get("Content-Type")

    @content_type.setter
    def content_type(self, value: str) -> None:
        self.headers["Content-Type"] = value

    @property
    def content_length(self) -> int | None:
        return self.headers.get("Content-Length", type=int)

    @content_length.setter
    def content_length(self, value: int | None) -> None:
        if value is None:
            self.headers.pop("Content-Length", None)
        else:
            self.headers["Content-Length"] = str(int(value))

    @property
    def location(self) -> str | None:
        return self.headers.get("Location")

    @location.setter
    def location(self, value: str) -> None:
        self.headers["Location"] = value

    @property
    def vary(self) -> HeaderSet:
        return HeaderSet(self.headers, "Vary")

    @property
    def content_range(self) -> str | None:
        return self.headers.get("Content-Range")

    @content_range.setter
    def content_range(self, value: str | None) -> None:
        if value is None:
            self.headers.pop("Content-Range", None)
        else:
            self.headers["Content-Range"] = value

    def get_etag(self) -> tuple[str | None, bool]:
        return unquote_etag(self.headers.get("ETag"))

    def set_etag(self, etag: str, weak: bool = False) -> None:
        self.headers["ETag"] = quote_etag(etag, weak)

    @property
    def last_modified(self) -> Any:
        return parse_http_date(self.headers.get("Last-Modified"))

    @last_modified.setter
    def last_modified(self, value: Any) -> None:
        if value is None:
            self.headers.pop("Last-Modified", None)
        else:
            self.headers["Last-Modified"] = http_date(value)

    # JSON

    @property
    def is_json(self) -> bool:
        mimetype = (self.mimetype or "").lower()
        return mimetype == "application/json" or (
            mimetype.startswith("application/") and mimetype.endswith("+json")
        )

    def get_json(self, force: bool = False, silent: bool = False) -> Any:
        if not (force or self.is_json):
            return None
        try:
            return _json.loads(self.get_data())
        except ValueError:
            if silent:
                return None
            raise

    @property
    def json(self) -> Any:
        return self.get_json()

    # conditional answers

    def make_conditional(
        self,
        request_or_environ: Any,
        accept_ranges: bool | str = False,
        complete_length: int | None = None,
    ) -> Response:
        """Answer ``If-None-Match``/``If-Modified-Since`` with 304 and ``Range`` with 206.

        Only a 200 answer to GET or HEAD changes; a range needs ``accept_ranges`` and
        the body's ``complete_length``. An unsatisfiable range raises a 416.
        """
        environ = getattr(request_or_environ, "environ", request_or_environ)
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        if method not in ("GET", "HEAD"):
            return self
        if accept_ranges:
            self.headers["Accept-Ranges"] = (
                "bytes" if accept_ranges is True else str(accept_ranges)
            )
        if "Date" not in self.headers:
            self.headers["Date"] = http_date()
        if self.status_code != 200:
            return self
        etag, _ = self.get_etag()
        if not self._process_range(environ, accept_ranges, complete_length, etag):
            if not self._modified(environ, etag):
                # The body stays attached: nothing of it is sent for a 304, and
                # closing the response still closes the file it came from.
                self.status_code = 304
                remove_entity_headers(self.headers)
                return self
            if (
                self.automatically_set_content_length
                and self.is_sequence
                and "Content-Length" not in self.headers
            ):
                self.headers["Content-Length"] = str(self.calculate_content_length())
        return self

    def _modified(self, environ: dict[str, Any], etag: str | None) -> bool:
        if_none_match = environ.get("HTTP_IF_NONE_MATCH")
        if etag is not None and if_none_match:
            return not etags_match(if_none_match, etag)
        if_modified_since = parse_http_date(environ.get("HTTP_IF_MODIFIED_SINCE"))
        last_modified = self.last_modified
        if if_modified_since is not None and last_modified is not None:
            return last_modified.replace(microsecond=0) > if_modified_since
        return True

    def _process_range(
        self,
        environ: dict[str, Any],
        accept_ranges: bool | str,
        complete_length: int | None,
        etag: str | None,
    ) -> bool:
        if not accept_ranges or complete_length is None:
            return False
        header = environ.get("HTTP_RANGE")
        if not header:
            return False
        if_range = environ.get("HTTP_IF_RANGE")
        if if_range:
            wanted, _ = unquote_etag(if_range)
            if etag is None or wanted != etag:
                return False
        try:
            found = parse_range(header, complete_length)
        except ValueError:
            raise RequestedRangeNotSatisfiable(complete_length) from None
        if found is None:
            return False
        start, end = found
        length = end - start
        self.headers["Content-Length"] = str(length)
        self.content_range = f"bytes {start}-{end - 1}/{complete_length}"
        self.status_code = 206
        self.response = _RangeIterator(self.response, start, length)
        return True

    # WSGI

    def get_wsgi_headers(self, environ: dict[str, Any]) -> Headers:
        headers = self.headers.copy()
        status = self.status_code
        if (
            self.automatically_set_content_length
            and self.is_sequence
            and "Content-Length" not in headers
            and status not in (204, 304)
            and not 100 <= status < 200
        ):
            headers["Content-Length"] = str(self.calculate_content_length())
        return headers

    def get_app_iter(self, environ: dict[str, Any]) -> Any:
        status = self.status_code
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        if method == "HEAD" or status in (204, 304) or 100 <= status < 200:
            return _ClosingIterable([], self.close)
        if self.is_sequence:
            return _ClosingIterable(list(self.iter_encoded()), self.close)
        return _ClosingIterable(self.iter_encoded(), self.close)

    def get_wsgi_response(
        self, environ: dict[str, Any]
    ) -> tuple[str, list[tuple[str, str]], Any]:
        headers = self.get_wsgi_headers(environ)
        return self.status, headers.to_wsgi_list(), self.get_app_iter(environ)

    def __call__(self, environ: dict[str, Any], start_response: Any) -> Any:
        status, headers, app_iter = self.get_wsgi_response(environ)
        start_response(status, headers)
        return app_iter

    def __repr__(self) -> str:
        body = (
            "streamed"
            if self.is_streamed
            else f"{self.calculate_content_length()} bytes"
        )
        return f"<{type(self).__name__} {body} [{self.status}]>"


__all__ = ["FileIterator", "HeaderSet", "LimitedStream", "Request", "Response"]
