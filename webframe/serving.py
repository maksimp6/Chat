"""The HTTP server: one thread per connection, HTTP/1.1 keep-alive, streamed bodies.

``serve(app, host, port)`` runs a WSGI application. A response without a
Content-Length is sent chunked, flushed after every piece, so server-sent
events reach the browser as they are produced. A client that goes away closes
the response iterator, which ends the generator that produced it.
"""

from __future__ import annotations

import logging
import socket
import socketserver
import sys
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, BinaryIO
from urllib.parse import unquote_to_bytes, urlsplit

log = logging.getLogger("logos.webframe.server")

SERVER_SOFTWARE = "Logos-webframe/1.0"
_MAX_DRAIN = 1024 * 1024
_NO_BODY_STATUSES = frozenset({204, 304})


class _BodyReader:
    """A request body bounded by its Content-Length."""

    def __init__(self, rfile: BinaryIO, length: int) -> None:
        self._rfile = rfile
        self.remaining = max(0, length)

    def read(self, size: int = -1) -> bytes:
        if self.remaining <= 0:
            return b""
        to_read = (
            self.remaining if size is None or size < 0 else min(size, self.remaining)
        )
        data = self._rfile.read(to_read)
        self.remaining -= len(data)
        if not data:
            self.remaining = 0
        return data

    def readline(self, size: int = -1) -> bytes:
        if self.remaining <= 0:
            return b""
        if size is None or size < 0 or size > self.remaining:
            size = self.remaining
        data = self._rfile.readline(size)
        self.remaining -= len(data)
        if not data:
            self.remaining = 0
        return data

    @property
    def done(self) -> bool:
        return self.remaining <= 0


class _ChunkedReader:
    """A request body sent with ``Transfer-Encoding: chunked``, read as plain bytes."""

    def __init__(self, rfile: BinaryIO) -> None:
        self._rfile = rfile
        self._chunk_left = 0
        self.done = False

    def _next_chunk(self) -> bool:
        line = self._rfile.readline(65537)
        if not line:
            self.done = True
            return False
        size_text = line.split(b";", 1)[0].strip()
        try:
            size = int(size_text, 16)
        except ValueError:
            self.done = True
            raise OSError("malformed chunk size") from None
        if size == 0:
            # Trailer headers up to the empty line.
            while True:
                trailer = self._rfile.readline(65537)
                if not trailer or trailer in (b"\r\n", b"\n"):
                    break
            self.done = True
            return False
        self._chunk_left = size
        return True

    def read(self, size: int = -1) -> bytes:
        pieces: list[bytes] = []
        wanted = None if size is None or size < 0 else size
        while wanted is None or wanted > 0:
            if self.done:
                break
            if self._chunk_left == 0 and not self._next_chunk():
                break
            take = self._chunk_left if wanted is None else min(wanted, self._chunk_left)
            data = self._rfile.read(take)
            if not data:
                self.done = True
                break
            pieces.append(data)
            self._chunk_left -= len(data)
            if wanted is not None:
                wanted -= len(data)
            if self._chunk_left == 0:
                self._rfile.readline(3)  # the CRLF after the chunk
        return b"".join(pieces)

    def readline(self, size: int = -1) -> bytes:
        pieces: list[bytes] = []
        while size < 0 or len(pieces) < size:
            byte = self.read(1)
            if not byte:
                break
            pieces.append(byte)
            if byte == b"\n":
                break
        return b"".join(pieces)

    @property
    def remaining(self) -> int:
        return 0 if self.done else 1


class WSGIRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = SERVER_SOFTWARE
    sys_version = ""
    disable_nagle_algorithm = True
    server: WSGIServer

    def handle(self) -> None:
        self.close_connection = True
        self.handle_one_request()
        while not self.close_connection:
            self.handle_one_request()

    def handle_one_request(self) -> None:
        try:
            self.connection.settimeout(self.server.idle_timeout)
            self.raw_requestline = self.rfile.readline(65537)
            self.connection.settimeout(None)
            if len(self.raw_requestline) > 65536:
                self.requestline = ""
                self.request_version = ""
                self.command = ""
                self.send_error(414)
                self.close_connection = True
                return
            if not self.raw_requestline:
                self.close_connection = True
                return
            if not self.parse_request():
                self.close_connection = True
                return
            self.run_wsgi()
        except TimeoutError:
            self.close_connection = True
        except (ConnectionError, OSError) as error:
            log.debug("connection %s dropped: %s", self.client_address, error)
            self.close_connection = True

    # the environ

    def make_environ(self) -> dict[str, Any]:
        target = self.path
        if "://" in target.split("?", 1)[0]:
            split = urlsplit(target)
            path, query = split.path, split.query
        else:
            path, _, query = target.partition("?")
        path_info = unquote_to_bytes(path).decode("latin-1")
        server_name, server_port = self.server.server_address[:2]
        environ: dict[str, Any] = {
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.errors": sys.stderr,
            "wsgi.multithread": self.server.multithreaded,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
            "wsgi.input_terminated": True,
            "REQUEST_METHOD": self.command,
            "SCRIPT_NAME": "",
            "PATH_INFO": path_info,
            "QUERY_STRING": query,
            "SERVER_NAME": str(server_name),
            "SERVER_PORT": str(server_port),
            "SERVER_PROTOCOL": self.request_version,
            "SERVER_SOFTWARE": SERVER_SOFTWARE,
            "REMOTE_ADDR": self.client_address[0],
            "REMOTE_PORT": str(self.client_address[1]),
        }
        for name, value in self.headers.items():
            key = name.upper().replace("-", "_")
            if key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
                environ.setdefault(key, value)
                continue
            key = "HTTP_" + key
            if key in environ:
                environ[key] = environ[key] + "," + value
            else:
                environ[key] = value
        transfer = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in transfer:
            environ.pop("CONTENT_LENGTH", None)
            body: Any = _ChunkedReader(self.rfile)
        else:
            raw_length = environ.get("CONTENT_LENGTH") or "0"
            try:
                length = int(raw_length)
            except ValueError:
                raise ValueError("invalid Content-Length") from None
            if length < 0:
                raise ValueError("invalid Content-Length")
            body = _BodyReader(self.rfile, length)
        environ["wsgi.input"] = body
        return environ

    # running the application

    def run_wsgi(self) -> None:
        try:
            environ = self.make_environ()
        except ValueError:
            self.send_error(400)
            self.close_connection = True
            return
        body_reader = environ["wsgi.input"]
        request_method = self.command
        http11 = self.request_version == "HTTP/1.1"
        status_sent: list[str] = []
        headers_set: list[Any] = []
        state = {"headers_sent": False, "chunked": False, "status": "-"}

        def write_headers(status: str, response_headers: list[tuple[str, str]]) -> None:
            code = int(status.split(" ", 1)[0])
            state["status"] = status.split(" ", 1)[0]
            names = {name.lower() for name, _ in response_headers}
            lines = [f"HTTP/1.1 {status}\r\n"]
            for name, value in response_headers:
                lines.append(f"{name}: {value}\r\n")
            if "date" not in names:
                lines.append(f"Date: {self.date_time_string()}\r\n")
            if "server" not in names:
                lines.append(f"Server: {SERVER_SOFTWARE}\r\n")
            has_body = (
                request_method != "HEAD"
                and code not in _NO_BODY_STATUSES
                and not 100 <= code < 200
            )
            if has_body and "content-length" not in names:
                if http11:
                    state["chunked"] = True
                    lines.append("Transfer-Encoding: chunked\r\n")
                else:
                    self.close_connection = True
            if not http11:
                self.close_connection = True
            if self.close_connection and "connection" not in names:
                lines.append("Connection: close\r\n")
            lines.append("\r\n")
            self.wfile.write("".join(lines).encode("latin-1"))
            state["headers_sent"] = True

        def write(data: bytes) -> None:
            if not state["headers_sent"]:
                write_headers(status_sent[0], headers_set[0])
            if not data:
                return
            if request_method == "HEAD":
                return
            if state["chunked"]:
                self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            else:
                self.wfile.write(data)

        def start_response(
            status: str, response_headers: list[tuple[str, str]], exc_info: Any = None
        ) -> Callable[[bytes], None]:
            if exc_info is not None:
                try:
                    if state["headers_sent"]:
                        raise exc_info[1].with_traceback(exc_info[2])
                finally:
                    exc_info = None
            elif status_sent:
                raise AssertionError("start_response called twice")
            status_sent[:] = [status]
            headers_set[:] = [list(response_headers)]
            return write

        started = time.monotonic()
        app_iter: Any = None
        try:
            app_iter = self.server.app(environ, start_response)
            try:
                for piece in app_iter:
                    if piece:
                        write(piece)
                if not state["headers_sent"]:
                    write(b"")
                if state["chunked"]:
                    self.wfile.write(b"0\r\n\r\n")
            finally:
                close = getattr(app_iter, "close", None)
                if callable(close):
                    close()
        except (ConnectionError, TimeoutError) as error:
            log.debug("client %s went away: %s", self.client_address, error)
            self.close_connection = True
            return
        except OSError as error:
            log.debug("connection %s failed: %s", self.client_address, error)
            self.close_connection = True
            return
        except Exception:
            log.exception("The application failed on %s %s", request_method, self.path)
            self.close_connection = True
            if not state["headers_sent"]:
                try:
                    body = b"Internal Server Error"
                    write_headers(
                        "500 INTERNAL SERVER ERROR",
                        [
                            ("Content-Type", "text/plain; charset=utf-8"),
                            ("Content-Length", str(len(body))),
                        ],
                    )
                    self.wfile.write(body)
                except OSError:
                    pass
            return
        finally:
            self._log_request(state["status"], time.monotonic() - started)
        if not self.close_connection:
            self._drain(body_reader)

    def _drain(self, body_reader: Any) -> None:
        """Consume what the application left of the body, or give up the connection."""
        remaining = getattr(body_reader, "remaining", 0)
        if not remaining:
            return
        if isinstance(body_reader, _ChunkedReader) or remaining > _MAX_DRAIN:
            self.close_connection = True
            return
        try:
            while body_reader.remaining > 0:
                if not body_reader.read(min(65536, body_reader.remaining)):
                    break
        except OSError:
            self.close_connection = True

    def _log_request(self, status: str, elapsed: float) -> None:
        log.info(
            '%s - "%s" %s %.3fs',
            self.client_address[0],
            self.requestline,
            status,
            elapsed,
        )

    def log_message(self, format: str, *args: Any) -> None:
        # Parse errors reported by the base class.
        log.info("%s - %s", self.client_address[0], format % args)


class WSGIServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128
    multithreaded = True

    def __init__(
        self, app: Any, host: str, port: int, idle_timeout: float | None = 120.0
    ) -> None:
        self.app = app
        self.idle_timeout = idle_timeout
        try:
            info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror:
            info = []
        if info:
            self.address_family = info[0][0]
        super().__init__((host, port), WSGIRequestHandler)

    def server_bind(self) -> None:
        # Bind without the base class's reverse DNS lookup of the host name.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


class SingleThreadedWSGIServer(HTTPServer):
    allow_reuse_address = True
    multithreaded = False

    def __init__(
        self, app: Any, host: str, port: int, idle_timeout: float | None = 120.0
    ) -> None:
        self.app = app
        self.idle_timeout = idle_timeout
        super().__init__((host, port), WSGIRequestHandler)

    server_bind = WSGIServer.server_bind


def make_server(app: Any, host: str, port: int, threaded: bool = True) -> HTTPServer:
    cls = WSGIServer if threaded else SingleThreadedWSGIServer
    return cls(app, host, port)


def serve(
    app: Any, host: str = "127.0.0.1", port: int = 5000, threaded: bool = True
) -> None:
    """Serve ``app`` until the process is interrupted."""
    server = make_server(app, host, port, threaded=threaded)
    shown_host = host if ":" not in host else f"[{host}]"
    log.info(
        "Serving on http://%s:%s (press Ctrl+C to quit)", shown_host, server.server_port
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


__all__ = ["WSGIRequestHandler", "WSGIServer", "make_server", "serve"]
