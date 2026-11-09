"""HTTP wire details: status phrases, dates, entity tags, byte ranges, media types."""

from __future__ import annotations

import re
import time
import unicodedata
from datetime import UTC, date, datetime
from email.utils import formatdate, parsedate_to_datetime
from http import HTTPStatus
from typing import Any
from urllib.parse import quote, unquote

from webframe.datastructures import Headers

_CHARSET_MIMETYPES = frozenset(
    {
        "application/ecmascript",
        "application/javascript",
        "application/sql",
        "application/xml",
        "application/xml-dtd",
    }
)

# Headers that describe the body; a 304 answer carries none of them.
_ENTITY_HEADERS = frozenset(
    {
        "allow",
        "content-encoding",
        "content-language",
        "content-length",
        "content-location",
        "content-md5",
        "content-range",
        "content-type",
        "expires",
        "last-modified",
    }
)

_TOKEN_CHARS = frozenset(
    "!#$%&'*+-.0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ^_`abcdefghijklmnopqrstuvwxyz|~"
)


def status_phrase(code: int) -> str:
    try:
        return HTTPStatus(code).phrase
    except ValueError:
        return "UNKNOWN"


def status_line(status: int | str) -> str:
    """``"200 OK"`` for an int or a string that may already carry a phrase."""
    if isinstance(status, int):
        return f"{status} {status_phrase(status)}"
    text = str(status).strip()
    if text.isdigit():
        return f"{int(text)} {status_phrase(int(text))}"
    return text


def status_code(status: int | str) -> int:
    if isinstance(status, int):
        return status
    head = str(status).strip().split(None, 1)[0]
    try:
        return int(head)
    except ValueError:
        raise ValueError(f"invalid status {status!r}") from None


def http_date(value: Any = None) -> str:
    """An HTTP date (RFC 7231) for now (``None``), a timestamp, a datetime or a struct_time."""
    if value is None:
        return formatdate(usegmt=True)
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return formatdate(value.timestamp(), usegmt=True)
    if isinstance(value, time.struct_time):
        return formatdate(time.mktime(value), usegmt=True)
    return formatdate(float(value), usegmt=True)


def parse_http_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def quote_etag(etag: str, weak: bool = False) -> str:
    if etag.startswith(('"', 'W/"')):
        return etag
    quoted = f'"{etag}"'
    return f"W/{quoted}" if weak else quoted


def unquote_etag(etag: str | None) -> tuple[str | None, bool]:
    if not etag:
        return None, False
    etag = etag.strip()
    weak = False
    if etag.startswith(("W/", "w/")):
        weak = True
        etag = etag[2:]
    if etag[:1] == etag[-1:] == '"':
        etag = etag[1:-1]
    return etag, weak


def etags_match(header: str | None, etag: str) -> bool:
    """Whether ``If-None-Match`` names ``etag`` (weak comparison) or everything (``*``)."""
    if not header:
        return False
    for item in header.split(","):
        item = item.strip()
        if item == "*":
            return True
        value, _ = unquote_etag(item)
        if value == etag:
            return True
    return False


def parse_range(header: str | None, length: int) -> tuple[int, int] | None:
    """The single byte range ``[start, end)`` a ``Range`` header asks for.

    ``None`` when the header is absent, malformed or asks for several ranges (the
    whole body is sent); ``ValueError`` when the range cannot be satisfied.
    """
    if not header:
        return None
    units, _, spec = header.strip().partition("=")
    if units.strip().lower() != "bytes" or "," in spec:
        return None
    start_text, dash, end_text = spec.strip().partition("-")
    if not dash:
        return None
    start_text, end_text = start_text.strip(), end_text.strip()
    if not start_text and not end_text:
        return None
    if start_text and not start_text.isdigit():
        return None
    if end_text and not end_text.isdigit():
        return None
    if not start_text:
        suffix = int(end_text)
        if suffix == 0:
            raise ValueError("unsatisfiable range")
        return max(0, length - suffix), length
    start = int(start_text)
    if start >= length:
        raise ValueError("unsatisfiable range")
    end = length if not end_text else min(int(end_text) + 1, length)
    if end <= start:
        raise ValueError("unsatisfiable range")
    return start, end


def get_content_type(mimetype: str, charset: str = "utf-8") -> str:
    """The ``Content-Type`` for a media type: text and script types name their charset."""
    lowered = mimetype.lower()
    if ";" in lowered:
        return mimetype
    if lowered.startswith("text/") or lowered in _CHARSET_MIMETYPES:
        return f"{mimetype}; charset={charset}"
    return mimetype


def remove_entity_headers(
    headers: Headers, allowed: tuple[str, ...] = ("expires", "content-location")
) -> None:
    for name in list(headers.keys()):
        lower = name.lower()
        if lower in _ENTITY_HEADERS and lower not in allowed:
            headers.remove(name)


_OPTION_RE = re.compile(
    r"""\s*;\s*(?P<name>[^\s=;]+)\s*"""
    r"""(?:=\s*(?:"(?P<quoted>(?:[^"\\]|\\.)*)"|(?P<plain>[^;]*)))?""",
    re.DOTALL,
)


def parse_options_header(value: str | None) -> tuple[str, dict[str, str]]:
    """``("multipart/form-data", {"boundary": "x"})`` for a header with parameters.

    RFC 2231 ``name*=charset''value`` parameters are decoded under their plain name.
    """
    if not value:
        return "", {}
    main, sep, rest = value.partition(";")
    options: dict[str, str] = {}
    if sep:
        for match in _OPTION_RE.finditer(";" + rest):
            name = match.group("name").lower()
            if match.group("quoted") is not None:
                text = re.sub(r"\\(.)", r"\1", match.group("quoted"))
            else:
                text = (match.group("plain") or "").strip()
            if name.endswith("*"):
                name = name[:-1]
                charset, _, encoded = text.partition("'")
                _, _, encoded = encoded.partition("'")
                try:
                    text = unquote(
                        encoded, encoding=charset or "utf-8", errors="replace"
                    )
                except LookupError:
                    text = unquote(encoded, errors="replace")
                options[name] = text
            elif name not in options:
                options[name] = text
    return main.strip(), options


def dump_options_header(header: str, options: dict[str, str | None]) -> str:
    parts = [header]
    for name, value in options.items():
        if value is None:
            parts.append(name)
        elif name.endswith("*"):
            parts.append(f"{name}={value}")
        elif value and all(char in _TOKEN_CHARS for char in value):
            parts.append(f"{name}={value}")
        else:
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            parts.append(f'{name}="{escaped}"')
    return "; ".join(parts)


def content_disposition(disposition: str, filename: str | None) -> str:
    """``inline``/``attachment`` with a file name, in ASCII plus RFC 5987 when needed."""
    if filename is None:
        return disposition
    options: dict[str, str | None]
    try:
        filename.encode("ascii")
    except UnicodeEncodeError:
        simple = (
            unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode()
        )
        options = {
            "filename": simple or "file",
            "filename*": "UTF-8''" + quote(filename, safe="!#$&+-.^_`|~"),
        }
    else:
        options = {"filename": filename}
    return dump_options_header(disposition, options)


__all__ = [
    "content_disposition",
    "dump_options_header",
    "etags_match",
    "get_content_type",
    "http_date",
    "parse_http_date",
    "parse_options_header",
    "parse_range",
    "quote_etag",
    "remove_entity_headers",
    "status_code",
    "status_line",
    "status_phrase",
    "unquote_etag",
]
