"""Calling the application in-process: a request becomes a WSGI call, no socket opened."""

from __future__ import annotations

import io
import json as _json
import sys
from collections.abc import Iterator
from typing import Any
from urllib.parse import unquote_to_bytes, urlencode, urlsplit

from webframe.datastructures import Headers, MultiDict
from webframe.wrappers import Response

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class TestResponse(Response):
    """The response of an in-process call; the body is read when it is asked for."""

    def __init__(self, app_iter: Any, status: str, headers: list[tuple[str, str]]) -> None:
        super().__init__(app_iter, status=status, headers=headers)


def make_environ(
    path: str = "/",
    *,
    method: str = "GET",
    data: Any = None,
    json: Any = None,
    query_string: Any = None,
    headers: Any = None,
    content_type: str | None = None,
    environ_base: dict[str, Any] | None = None,
    environ_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A WSGI environ for one request, as the server would build it."""
    path, _, query_from_path = path.partition("?")
    if query_string is None:
        query = query_from_path
    elif isinstance(query_string, (dict, MultiDict)):
        items = query_string.items(multi=True) if isinstance(query_string, MultiDict) else query_string.items()
        query = urlencode(list(items), doseq=True)
    else:
        query = str(query_string)
    request_headers = Headers(headers)
    if json is not None:
        body = _json.dumps(json).encode("utf-8")
        content_type = content_type or "application/json"
    elif data is None:
        body = b""
    elif isinstance(data, str):
        body = data.encode("utf-8")
    elif isinstance(data, (bytes, bytearray, memoryview)):
        body = bytes(data)
    elif isinstance(data, dict):
        body = urlencode(data, doseq=True).encode("utf-8")
        content_type = content_type or "application/x-www-form-urlencoded"
    elif hasattr(data, "read"):
        body = data.read()
    else:
        body = b"".join(
            piece.encode("utf-8") if isinstance(piece, str) else bytes(piece)
            for piece in data
        )
    if content_type is not None:
        request_headers["Content-Type"] = content_type
    environ: dict[str, Any] = {
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(body),
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": True,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "wsgi.input_terminated": True,
        "REQUEST_METHOD": method.upper(),
        "SCRIPT_NAME": "",
        "PATH_INFO": unquote_to_bytes(path or "/").decode("latin-1"),
        "QUERY_STRING": query,
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "REMOTE_ADDR": "127.0.0.1",
        "CONTENT_LENGTH": str(len(body)),
    }
    if environ_base:
        environ.update(environ_base)
    for name, value in request_headers.items():
        key = name.upper().replace("-", "_")
        if key == "CONTENT_TYPE":
            environ["CONTENT_TYPE"] = value
        elif key == "CONTENT_LENGTH":
            continue
        else:
            environ["HTTP_" + key] = value
    environ.setdefault("HTTP_HOST", "localhost")
    if environ_overrides:
        environ.update(environ_overrides)
    return environ


class _StartedIterable:
    """An app iterable whose first piece was already read to trigger ``start_response``."""

    def __init__(self, first: list[Any], rest: Iterator[Any], source: Any) -> None:
        self._first = first
        self._rest = rest
        self._source = source

    def __iter__(self) -> Iterator[Any]:
        yield from self._first
        yield from self._rest

    def close(self) -> None:
        close = getattr(self._source, "close", None)
        if callable(close):
            close()


def run_wsgi_app(app: Any, environ: dict[str, Any], buffered: bool = False) -> TestResponse:
    """Call ``app`` once and wrap what it answered."""
    started: list[Any] = []

    def start_response(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> Any:
        started[:] = [status, list(headers)]
        return lambda data: None

    app_iter = app(environ, start_response)
    if not started:
        iterator = iter(app_iter)
        first = []
        for piece in iterator:
            first.append(piece)
            break
        app_iter = _StartedIterable(first, iterator, app_iter)
    if not started:
        raise RuntimeError("the application never called start_response")
    response = TestResponse(app_iter, started[0], started[1])
    if buffered:
        response.make_sequence()
        response.close()
    return response


class Client:
    """``app.test_client()``: ``get``/``post``/``open`` run requests through the application.

    ``open`` takes ``json=`` or ``data=`` for the body, ``query_string=`` (a dict or
    a string), ``headers=`` (a dict or pairs) and ``follow_redirects=``. With
    ``buffered=False`` a streamed body stays a stream to iterate.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        return None

    def open(
        self,
        path: str = "/",
        *,
        method: str = "GET",
        data: Any = None,
        json: Any = None,
        query_string: Any = None,
        headers: Any = None,
        content_type: str | None = None,
        follow_redirects: bool = False,
        buffered: bool = False,
        environ_base: dict[str, Any] | None = None,
        environ_overrides: dict[str, Any] | None = None,
    ) -> TestResponse:
        for _ in range(21):
            environ = make_environ(
                path,
                method=method,
                data=data,
                json=json,
                query_string=query_string,
                headers=headers,
                content_type=content_type,
                environ_base=environ_base,
                environ_overrides=environ_overrides,
            )
            response = run_wsgi_app(self.app, environ, buffered=buffered)
            if (
                not follow_redirects
                or response.status_code not in _REDIRECT_STATUSES
                or "Location" not in response.headers
            ):
                return response
            location = urlsplit(response.headers["Location"])
            response.close()
            path = location.path or "/"
            query_string = location.query
            if response.status_code in (301, 302, 303):
                method = "GET"
                data = json = None
                content_type = None
        raise RuntimeError("too many redirects")

    def get(self, path: str = "/", **kwargs: Any) -> TestResponse:
        return self.open(path, method="GET", **kwargs)

    def post(self, path: str = "/", **kwargs: Any) -> TestResponse:
        return self.open(path, method="POST", **kwargs)

    def put(self, path: str = "/", **kwargs: Any) -> TestResponse:
        return self.open(path, method="PUT", **kwargs)

    def patch(self, path: str = "/", **kwargs: Any) -> TestResponse:
        return self.open(path, method="PATCH", **kwargs)

    def delete(self, path: str = "/", **kwargs: Any) -> TestResponse:
        return self.open(path, method="DELETE", **kwargs)

    def head(self, path: str = "/", **kwargs: Any) -> TestResponse:
        return self.open(path, method="HEAD", **kwargs)

    def options(self, path: str = "/", **kwargs: Any) -> TestResponse:
        return self.open(path, method="OPTIONS", **kwargs)


__all__ = ["Client", "TestResponse", "make_environ", "run_wsgi_app"]
