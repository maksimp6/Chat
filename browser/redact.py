"""Masking of credentials in URLs, headers and free text shown to the model or logged.

Header and cookie values never reach model-visible output in clear text: a
masked value keeps only its length, and a cookie listing adds a short sha256
fingerprint so two values can be compared without revealing either.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Iterable
from urllib.parse import unquote

from browser.adapters import _CREDENTIAL_ASSIGNMENT
from browser.capabilities import sanitize_browser_value

logger = logging.getLogger("logos.browser.redact")

_SENSITIVE_HEADER_NAMES = frozenset(
    {
        "cookie",
        "set-cookie",
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "api-key",
        "apikey",
        "x-auth-token",
        "x-auth-key",
    }
)
# scheme://authority at the start of a URL; the authority ends at / ? or #.
_URL_AUTHORITY = re.compile(r"^(\s*(?:[A-Za-z][A-Za-z0-9+.\-]*:)?//)([^/?#]*)")
# user:password@ anywhere in free text (logs, error messages).
_TEXT_USERINFO = re.compile(
    r"((?:[A-Za-z][A-Za-z0-9+.\-]*:)?//[^\s/?#@:]*:)[^\s/?#@]+@"
)
_BARE_USERINFO = re.compile(r"^(\s*[^\s/?#@:]+:)[^\s/?#@]+@")


def _split_userinfo(authority: str) -> tuple[str, str | None, str] | None:
    """(user, password or None, host part) when the authority carries userinfo."""
    if "@" not in authority:
        return None
    userinfo, _, hostport = authority.rpartition("@")
    user, colon, password = userinfo.partition(":")
    return user, (password if colon else None), hostport


def display_url(url: Any) -> str:
    """The URL with the userinfo password replaced by "***"."""
    if url is None:
        return ""
    text = str(url)
    match = _URL_AUTHORITY.match(text)
    if match is None:
        # No "//": a bare "user:pass@host" (a proxy setting) or a malformed value.
        return _TEXT_USERINFO.sub(r"\1***@", _BARE_USERINFO.sub(r"\1***@", text))
    parts = _split_userinfo(match.group(2))
    if parts is None:
        return text
    user, password, hostport = parts
    if not password:
        return text
    return match.group(1) + f"{user}:***@{hostport}" + text[match.end() :]


def userinfo_password(url: Any) -> str | None:
    """The percent-decoded password of the URL userinfo, if any."""
    if not url:
        return None
    match = _URL_AUTHORITY.match(str(url))
    if match is None:
        return None
    parts = _split_userinfo(match.group(2))
    if parts is None or not parts[1]:
        return None
    return unquote(parts[1])


def is_sensitive_name(name: Any) -> bool:
    """True for header, field or cookie names whose values are credentials."""
    key = str(name or "").strip()
    if not key:
        return False
    if key.lower() in _SENSITIVE_HEADER_NAMES:
        return True
    return sanitize_browser_value({key: "x"}).get(key) == "<redacted>"


def mask_value(value: Any) -> str:
    """"<скрыто: N симв.>" for a secret value."""
    return f"<скрыто: {len(str(value or ''))} симв.>"


def _mask_set_cookie(value: str) -> str:
    pair, sep, attributes = value.partition(";")
    name, eq, cookie_value = pair.partition("=")
    if not eq:
        # A nameless cookie: the whole pair is the value.
        masked = mask_value(pair.strip())
    else:
        masked = f"{name.strip()}={mask_value(cookie_value.strip())}"
    attributes = attributes.strip()
    return f"{masked}; {attributes}" if sep and attributes else masked


def _mask_cookie(value: str) -> str:
    names = []
    for item in value.split(";"):
        item = item.strip()
        if not item:
            continue
        name, eq, _ = item.partition("=")
        names.append(f"{name.strip()}=<скрыто>" if eq else "<скрыто>")
    return "; ".join(names)


def _mask_authorization(value: str) -> str:
    text = value.strip()
    scheme, space, credential = text.partition(" ")
    credential = credential.strip()
    if not space or not credential:
        return mask_value(text)
    return f"{scheme} {mask_value(credential)}"


def mask_header_value(name: Any, value: Any) -> str:
    """The value as shown to the model: credentials masked, the rest unchanged."""
    text = "" if value is None else str(value)
    key = str(name or "").strip().lower()
    if key == "set-cookie":
        return _mask_set_cookie(text)
    if key == "cookie":
        return _mask_cookie(text)
    if key in ("authorization", "proxy-authorization"):
        return _mask_authorization(text)
    if is_sensitive_name(key):
        return mask_value(text)
    return text


def mask_headers(pairs: Iterable[tuple[Any, Any]]) -> list[tuple[str, str]]:
    """Header pairs with every sensitive value masked; order and duplicates kept."""
    return [(str(name), mask_header_value(name, value)) for name, value in pairs]


def redact_text(text: Any) -> str:
    """Free text (errors, logs) with credential assignments and URL passwords masked."""
    if text is None:
        return ""
    value = _CREDENTIAL_ASSIGNMENT.sub(
        lambda match: match.group(1) + "=<redacted>", str(text)
    )
    return _TEXT_USERINFO.sub(r"\1***@", value)


def fingerprint(value: Any) -> str:
    """The first 8 hex characters of the value's sha256."""
    data = str(value or "").encode("utf-8", "surrogatepass")
    return hashlib.sha256(data).hexdigest()[:8]


def bearer_credential(value: Any) -> str | None:
    """The credential after "Bearer " or "Basic " in an Authorization value."""
    text = str(value or "").strip()
    scheme, space, credential = text.partition(" ")
    if space and scheme.lower() in ("bearer", "basic") and credential.strip():
        return credential.strip()
    return None


__all__ = [
    "bearer_credential",
    "display_url",
    "fingerprint",
    "is_sensitive_name",
    "mask_header_value",
    "mask_headers",
    "mask_value",
    "redact_text",
    "userinfo_password",
]
