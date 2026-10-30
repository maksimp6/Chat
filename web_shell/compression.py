"""Fewer bytes on a slow link: gzip for text responses, JSON without ASCII escapes.

At 10 kb/s (1.25 KB/s) every byte counts: the scripts of the page shrink about four
times, and Cyrillic JSON stops costing six bytes per letter. A streamed response (live
answer events, a history stream) is compressed piece by piece with a sync flush after
each one, so every event still reaches the browser at once.
"""

from __future__ import annotations

import gzip
import re
import zlib
from typing import Any, Iterable, Iterator

from flask import Flask, Response, request
from flask.json.provider import DefaultJSONProvider

# Media types worth compressing; everything else (pictures, audio, archives, fonts) is
# already compressed or binary.
_COMPRESSIBLE_TYPES = frozenset(
    {
        "application/javascript",
        "application/json",
        "application/x-javascript",
        "application/x-ndjson",
        "application/xml",
        "image/svg+xml",
    }
)

# Responses that can carry a secret (provider keys, IAM keys, OAuth state) are sent as
# they are: compressing a secret next to text an attacker can inject leaks it by size
# (BREACH).
_SECRET_PATH_PREFIXES = (
    "/api/provider-credentials",
    "/api/cloudru/iam",
    "/api/mcp-servers",
    "/api/mcp/oauth",
)
# An environment's own app is reached under /environments/<id>/…; its secret paths count too.
_ENVIRONMENT_PATH = re.compile(r"^/environments/[^/]+(/.*)?$")


def _secret_path(path: str) -> bool:
    inner = _ENVIRONMENT_PATH.match(path)
    candidates = (path, inner.group(1) or "/") if inner else (path,)
    return any(candidate.startswith(_SECRET_PATH_PREFIXES) for candidate in candidates)


def _compressible(response: Response) -> bool:
    mimetype = (response.mimetype or "").lower()
    if not (
        mimetype.startswith("text/")
        or mimetype in _COMPRESSIBLE_TYPES
        or mimetype.endswith("+json")
        or mimetype.endswith("+xml")
    ):
        return False
    if request.method == "HEAD" or _secret_path(request.path):
        return False
    if response.status_code < 200 or response.status_code in (204, 206, 304):
        return False
    if "Content-Encoding" in response.headers or "Content-Range" in response.headers:
        return False
    return request.accept_encodings["gzip"] > 0


def _gzip_stream(pieces: Iterable[Any]) -> Iterator[bytes]:
    """The pieces gzip-compressed; each one is flushed, so a reader gets it at once."""
    compressor = zlib.compressobj(9, zlib.DEFLATED, 31)
    try:
        for piece in pieces:
            data = piece.encode("utf-8") if isinstance(piece, str) else bytes(piece)
            out = compressor.compress(data) + compressor.flush(zlib.Z_SYNC_FLUSH)
            if out:
                yield out
        yield compressor.flush()
    finally:
        # A reader that disconnects closes this generator: the stream it wraps must end too
        # (a live answer's event reader, a stream_with_context request context).
        close = getattr(pieces, "close", None)
        if callable(close):
            close()


def _mark_compressed(response: Response) -> None:
    response.headers["Content-Encoding"] = "gzip"
    response.vary.add("Accept-Encoding")
    # Byte ranges would address the uncompressed body.
    response.headers.pop("Accept-Ranges", None)
    # The compressed bytes are another representation of the same content.
    etag, weak = response.get_etag()
    if etag and not weak:
        response.set_etag(etag, weak=True)


def compress_response(response: Response) -> Response:
    """gzip the response when the client accepts it and it is worth it."""
    if not _compressible(response):
        return response
    # A static file is read once and sent compressed whole, with its length. Any other
    # stream (a live answer, a proxied environment) stays a stream.
    if response.is_streamed and request.endpoint != "static":
        response.response = _gzip_stream(response.response)
        response.headers.pop("Content-Length", None)
        _mark_compressed(response)
        return response
    response.direct_passthrough = False
    data = response.get_data()
    compressed = gzip.compress(data, compresslevel=9, mtime=0)
    if len(compressed) >= len(data):
        return response
    response.set_data(compressed)
    _mark_compressed(response)
    return response


class Utf8JSONProvider(DefaultJSONProvider):
    """JSON as UTF-8 text instead of ASCII escapes, so a Cyrillic letter costs 2 bytes, not 6.

    A text that cannot be UTF-8 (a lone surrogate, as in a file name that was not UTF-8
    on disk) is sent with escapes, as before, rather than failing the response.
    """

    ensure_ascii = False

    def dumps(self, obj: Any, **kwargs: Any) -> str:
        text = super().dumps(obj, **kwargs)
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            text = super().dumps(obj, **{**kwargs, "ensure_ascii": True})
        return text


def install(app: Flask) -> None:
    """JSON without ASCII escapes, and gzip for every compressible response."""
    app.json = Utf8JSONProvider(app)
    app.after_request(compress_response)
