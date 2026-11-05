"""Request bodies as forms: ``application/x-www-form-urlencoded`` and ``multipart/form-data``.

A multipart body is read piece by piece; file parts go to a spooled temporary
file (memory up to 512 KiB, then disk), so an upload never has to fit in memory
twice.
"""

from __future__ import annotations

import tempfile
from typing import Any, BinaryIO
from urllib.parse import parse_qsl

from webframe.datastructures import FileStorage, Headers, MultiDict
from webframe.exceptions import BadRequest
from webframe.http import parse_options_header

_CHUNK = 64 * 1024
_MAX_PART_HEADERS = 64 * 1024
_SPOOL = 512 * 1024


def parse_form_data(
    stream: BinaryIO, content_type: str | None
) -> tuple[MultiDict, MultiDict]:
    """``(form, files)`` of a request body; both empty when the type is not a form."""
    mimetype, options = parse_options_header(content_type)
    mimetype = mimetype.lower()
    if mimetype == "application/x-www-form-urlencoded":
        data = stream.read()
        pairs = parse_qsl(
            data.decode("utf-8", "replace"), keep_blank_values=True, errors="replace"
        )
        return MultiDict(pairs), MultiDict()
    if mimetype == "multipart/form-data":
        boundary = options.get("boundary")
        if not boundary:
            raise BadRequest("Missing boundary")
        return MultipartParser(stream, boundary.encode("latin-1")).parse()
    return MultiDict(), MultiDict()


class MultipartParser:
    """A streaming parser of one multipart body."""

    def __init__(self, stream: BinaryIO, boundary: bytes) -> None:
        self._stream = stream
        self._delimiter = b"--" + boundary
        self._buffer = b""
        self._eof = False

    def _fill(self) -> bool:
        if self._eof:
            return False
        chunk = self._stream.read(_CHUNK)
        if not chunk:
            self._eof = True
            return False
        self._buffer += chunk
        return True

    def _skip_preamble(self) -> bool:
        """Position after the first delimiter; False when the body ends at once."""
        while True:
            index = self._buffer.find(self._delimiter)
            if index >= 0:
                self._buffer = self._buffer[index + len(self._delimiter) :]
                return self._after_delimiter()
            keep = len(self._delimiter) - 1
            if len(self._buffer) > keep:
                self._buffer = self._buffer[-keep:]
            if not self._fill():
                raise BadRequest("Multipart body has no boundary")

    def _after_delimiter(self) -> bool:
        """After ``--boundary``: ``--`` closes the body; a line break opens a part."""
        while len(self._buffer) < 2 and self._fill():
            pass
        if self._buffer.startswith(b"--"):
            return False
        if self._buffer.startswith(b"\r\n"):
            self._buffer = self._buffer[2:]
        elif self._buffer.startswith(b"\n"):
            self._buffer = self._buffer[1:]
        elif self._buffer:
            raise BadRequest("Malformed multipart boundary")
        return True

    def _read_headers(self) -> Headers:
        while True:
            end = self._buffer.find(b"\r\n\r\n")
            length = 4
            if end < 0:
                end = self._buffer.find(b"\n\n")
                length = 2
            if end >= 0:
                raw = self._buffer[:end]
                self._buffer = self._buffer[end + length :]
                break
            if len(self._buffer) > _MAX_PART_HEADERS:
                raise BadRequest("Multipart part headers are too long")
            if not self._fill():
                raise BadRequest("Multipart body ended inside part headers")
        headers = Headers()
        for line in raw.decode("utf-8", "replace").splitlines():
            if not line.strip():
                continue
            name, sep, value = line.partition(":")
            if not sep:
                raise BadRequest("Malformed multipart part header")
            headers.add(name.strip(), value.strip())
        return headers

    def _read_body(self, write: Any) -> bool:
        """Copy a part body to ``write`` up to the next delimiter; True when parts follow."""
        marker = b"\r\n" + self._delimiter
        alt_marker = b"\n" + self._delimiter
        while True:
            index = self._buffer.find(marker)
            cut = len(marker)
            if index < 0:
                index = self._buffer.find(alt_marker)
                cut = len(alt_marker)
            if index >= 0:
                write(self._buffer[:index])
                self._buffer = self._buffer[index + cut :]
                return self._after_delimiter()
            keep = len(marker) + 1
            if len(self._buffer) > keep:
                write(self._buffer[:-keep])
                self._buffer = self._buffer[-keep:]
            if not self._fill():
                raise BadRequest("Multipart body ended inside a part")

    def parse(self) -> tuple[MultiDict, MultiDict]:
        form = MultiDict()
        files = MultiDict()
        more = self._skip_preamble()
        while more:
            headers = self._read_headers()
            _, disposition = parse_options_header(headers.get("Content-Disposition"))
            name = disposition.get("name", "")
            filename = disposition.get("filename")
            if filename is not None:
                spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL, mode="w+b")
                more = self._read_body(spool.write)
                spool.seek(0)
                files.add(
                    name,
                    FileStorage(
                        spool,
                        filename=filename,
                        name=name,
                        content_type=headers.get("Content-Type"),
                        headers=headers,
                    ),
                )
            else:
                pieces: list[bytes] = []
                more = self._read_body(pieces.append)
                form.add(name, b"".join(pieces).decode("utf-8", "replace"))
        return form, files


__all__ = ["MultipartParser", "parse_form_data"]
