"""Content classification and character-set detection for the HTTP engine.

The functions here decide what a response body is (HTML, XML, JSON, text,
binary or empty) and how its bytes become text. Charset labels follow the
WHATWG Encoding Standard aliases, so a page that declares ``latin1`` is decoded
as windows-1252 exactly as a browser would.
"""

from __future__ import annotations

import codecs
import json
import logging
import re
from typing import Any

from requests import compat as _requests_compat

logger = logging.getLogger("logos.browser.charsets")

# WHATWG labels whose Python codec differs from the label's literal meaning.
_ALIASES = {
    "latin1": "cp1252",
    "latin-1": "cp1252",
    "l1": "cp1252",
    "iso-8859-1": "cp1252",
    "iso8859-1": "cp1252",
    "iso88591": "cp1252",
    "iso_8859-1": "cp1252",
    "iso_8859-1:1987": "cp1252",
    "iso-ir-100": "cp1252",
    "csisolatin1": "cp1252",
    "ibm819": "cp1252",
    "cp819": "cp1252",
    "ascii": "cp1252",
    "us-ascii": "cp1252",
    "ansi_x3.4-1968": "cp1252",
    "windows-1252": "cp1252",
    "x-cp1252": "cp1252",
    "utf8": "utf-8",
    "unicode-1-1-utf-8": "utf-8",
    "unicode11utf8": "utf-8",
    "unicode20utf8": "utf-8",
    "x-unicode20utf8": "utf-8",
    "win-1251": "cp1251",
    "windows-1251": "cp1251",
    "x-cp1251": "cp1251",
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "x-gbk": "gb18030",
    "gb_2312": "gb18030",
    "gb_2312-80": "gb18030",
    "chinese": "gb18030",
    "csgb2312": "gb18030",
    "csiso58gb231280": "gb18030",
    "iso-ir-58": "gb18030",
    "x-mac-cyrillic": "mac_cyrillic",
    "x-mac-ukrainian": "mac_cyrillic",
    "x-mac-roman": "mac_roman",
    "macintosh": "mac_roman",
    "mac": "mac_roman",
    "csmacintosh": "mac_roman",
    "iso-8859-9": "cp1254",
    "iso8859-9": "cp1254",
    "iso88599": "cp1254",
    "latin5": "cp1254",
    "l5": "cp1254",
    "windows-1254": "cp1254",
    "x-cp1254": "cp1254",
    "tis-620": "cp874",
    "iso-8859-11": "cp874",
    "windows-874": "cp874",
    "dos-874": "cp874",
    "euc-kr": "cp949",
    "ks_c_5601-1987": "cp949",
    "ks_c_5601-1989": "cp949",
    "ksc5601": "cp949",
    "ksc_5601": "cp949",
    "korean": "cp949",
    "windows-949": "cp949",
    "shift_jis": "cp932",
    "shift-jis": "cp932",
    "sjis": "cp932",
    "x-sjis": "cp932",
    "ms_kanji": "cp932",
    "ms932": "cp932",
    "windows-31j": "cp932",
    "csshiftjis": "cp932",
    "big5": "big5hkscs",
    "big5-hkscs": "big5hkscs",
    "cn-big5": "big5hkscs",
    "x-x-big5": "big5hkscs",
    "csbig5": "big5hkscs",
    "iso-8859-8-i": "iso-8859-8",
    "csiso88598i": "iso-8859-8",
    "logical": "iso-8859-8",
    "koi8-ru": "koi8-u",
    "koi8": "koi8-r",
    "koi": "koi8-r",
    "cskoi8r": "koi8-r",
    "ibm866": "cp866",
    "866": "cp866",
    "csibm866": "cp866",
    "utf-16": "utf-16-le",
    "utf-16le": "utf-16-le",
    "utf-16be": "utf-16-be",
    "unicode": "utf-16-le",
    "ucs-2": "utf-16-le",
    "unicodefeff": "utf-16-le",
    "unicodefffe": "utf-16-be",
    "csunicode": "utf-16-le",
    "iso-10646-ucs-2": "utf-16-le",
}

# Labels the Encoding Standard maps to the "replacement" decoder or that Python cannot decode.
_REJECTED = frozenset(
    {
        "x-user-defined",
        "csiso2022kr",
        "hz-gb-2312",
        "iso-2022-cn",
        "iso-2022-cn-ext",
        "iso-2022-kr",
        "replacement",
    }
)

_BOMS = (
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)

_JSON_MIMES = frozenset(
    {"application/json", "text/json", "application/feed+json", "application/x-json"}
)
_XML_MIMES = frozenset({"text/xml", "application/xml"})
_HTML_MIMES = frozenset({"text/html", "application/xhtml+xml"})
_TEXT_APPLICATION_MIMES = frozenset(
    {
        "application/javascript",
        "application/ecmascript",
        "application/x-javascript",
        "application/x-ndjson",
        "application/yaml",
        "application/x-yaml",
        "application/toml",
        "application/sql",
        "application/graphql",
        "application/x-www-form-urlencoded",
        "application/csv",
    }
)
GENERIC_MIMES = frozenset(
    {"", "application/octet-stream", "binary/octet-stream", "application/unknown"}
)

_SNIFF_HTML = (b"<!doctype html", b"<html", b"<head", b"<body")
_SNIFF_XML = (b"<?xml", b"<rss", b"<feed", b"<rdf:rdf")
_BINARY_MAGIC = (b"%PDF", b"\x89PNG", b"GIF8", b"\xff\xd8\xff", b"PK\x03\x04")

_META_CHARSET = re.compile(
    r"""<meta\b[^>]*?charset\s*=\s*["']?\s*([A-Za-z0-9_.:\-]+)""", re.I
)
_XML_DECLARATION = re.compile(
    r"""^\s*<\?xml\b[^>]*?\bencoding\s*=\s*["']([A-Za-z0-9_.:\-]+)["']"""
)


def normalize_charset(label: str | None) -> str | None:
    """Python codec name for a WHATWG charset label, or None for unknown labels."""
    if not label:
        return None
    name = str(label).strip().strip("\"'").strip().lower()
    if not name or name in _REJECTED:
        return None
    mapped = _ALIASES.get(name, name)
    try:
        return codecs.lookup(mapped).name
    except LookupError:
        return None


def parse_content_type(value: str | None) -> tuple[str, dict[str, str]]:
    """Split a Content-Type value into its lowercase MIME type and its parameters."""
    if not value:
        return "", {}
    parts = _split_params(str(value))
    mime = parts[0].strip().lower() if parts else ""
    params: dict[str, str] = {}
    for part in parts[1:]:
        key, sep, raw = part.partition("=")
        key = key.strip().lower()
        if not sep or not key:
            continue
        raw = raw.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] == '"':
            raw = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        params.setdefault(key, raw)
    return mime, params


def _split_params(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    escaped = False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\" and quoted:
            current.append(char)
            escaped = True
            continue
        if char == '"':
            quoted = not quoted
        if char == ";" and not quoted:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


def mime_of(content_type: str | None) -> str:
    return parse_content_type(content_type)[0]


def classify(mime: str | None, head: bytes | None) -> str:
    """Kind of a body: html, xml, json, text, binary or empty."""
    base = (mime or "").split(";", 1)[0].strip().lower()
    if head is not None and len(head) == 0:
        return "empty"
    if base in _HTML_MIMES:
        return "html"
    if base in _JSON_MIMES or base.endswith("+json"):
        return "json"
    if base in _XML_MIMES or base.endswith("+xml"):
        return "xml"
    if base.startswith("text/") or base in _TEXT_APPLICATION_MIMES:
        return "text"
    if base in GENERIC_MIMES:
        return sniff(head)
    return "binary"


def sniff(head: bytes | None) -> str:
    """Guess the kind of an unlabelled body from its first bytes."""
    if head is None:
        return "binary"
    data = bytes(head)
    if not data:
        return "empty"
    for bom, _name in _BOMS:
        if data.startswith(bom):
            data = data[len(bom) :]
            break
    stripped = data.lstrip(b" \t\r\n\x0c")
    if not stripped:
        return "text"
    lowered = stripped[:512].lower()
    if any(marker in lowered for marker in _SNIFF_HTML):
        return "html"
    if lowered.startswith(_SNIFF_XML):
        return "xml"
    for magic in _BINARY_MAGIC:
        if stripped.startswith(magic):
            return "binary"
    if b"\x00" in data:
        return "binary"
    utf8 = _is_utf8(data)
    if stripped[:1] in (b"{", b"[") and utf8:
        return "json"
    if utf8:
        return "text"
    printable = sum(
        1 for byte in data if byte in (9, 10, 12, 13) or 32 <= byte < 127 or byte >= 128
    )
    return "text" if printable >= 0.95 * len(data) else "binary"


def _is_utf8(data: bytes) -> bool:
    """Strict UTF-8 check that accepts a multi-byte sequence cut at the end."""
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    view = memoryview(data)
    try:
        for start in range(0, len(data), 65536):
            decoder.decode(view[start : start + 65536], final=False)
    except UnicodeDecodeError:
        return False
    pending = decoder.getstate()[0]
    return len(pending) <= 3


def _bom_charset(raw: bytes) -> tuple[str, int] | None:
    for bom, name in _BOMS:
        if raw.startswith(bom):
            return name, len(bom)
    return None


def _ascii_compatible(charset: str) -> bool:
    return not charset.startswith("utf-16") and not charset.startswith("utf-32")


def detect_charset(raw: bytes, content_type: str | None, kind: str) -> tuple[str, str]:
    """Charset of a body and where it came from (bom, header, meta, xml, json, utf-8, auto)."""
    found = _bom_charset(raw)
    if found:
        return found[0], "bom"
    _mime, params = parse_content_type(content_type)
    declared = normalize_charset(params.get("charset"))
    if declared:
        return declared, "header"
    if kind == "html":
        head = raw[:4096].decode("latin-1")
        for match in _META_CHARSET.finditer(head):
            charset = normalize_charset(match.group(1))
            if charset:
                # A page cannot declare UTF-16 in an ASCII meta tag (Encoding Standard).
                if not _ascii_compatible(charset):
                    charset = "utf-8"
                return charset, "meta"
    if kind in ("xml", "html"):
        match = _XML_DECLARATION.match(raw[:1024].decode("latin-1"))
        if match:
            charset = normalize_charset(match.group(1))
            if charset:
                return charset, "xml"
    if kind == "json":
        try:
            charset = normalize_charset(json.detect_encoding(raw[:4])) or "utf-8"
        except (TypeError, ValueError):
            charset = "utf-8"
        return charset, "json"
    if _is_utf8(raw):
        return "utf-8", "utf-8"
    detector: Any = getattr(_requests_compat, "chardet", None)
    if detector is not None:
        try:
            result = detector.detect(raw[:65536]) or {}
        except Exception as exc:
            logger.debug("charset detector failed: %s", type(exc).__name__)
            result = {}
        guessed = normalize_charset(result.get("encoding"))
        if guessed:
            return guessed, "auto"
    return "utf-8", "utf-8 с заменой"


def decode_body(
    raw: bytes, content_type: str | None, kind: str
) -> tuple[str, str, str, bool]:
    """Decode a body: (text, charset, charset source, had undecodable bytes)."""
    charset, source = detect_charset(raw, content_type, kind)
    data = raw
    bom = _bom_charset(raw)
    if bom and bom[0] == charset:
        data = raw[bom[1] :]
    try:
        text = data.decode(charset)
        had_errors = False
    except UnicodeDecodeError:
        text = data.decode(charset, errors="replace")
        had_errors = True
    except LookupError:
        charset, source = "utf-8", "utf-8 с заменой"
        text = data.decode("utf-8", errors="replace")
        had_errors = "�" in text
    if text.startswith("﻿"):
        text = text[1:]
    return text, charset, source, had_errors


__all__ = [
    "GENERIC_MIMES",
    "classify",
    "decode_body",
    "detect_charset",
    "mime_of",
    "normalize_charset",
    "parse_content_type",
    "sniff",
]
