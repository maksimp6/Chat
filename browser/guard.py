"""Link-local guard for every browser request, redirect and Chrome subresource.

The check is the one ``http_fetch`` uses (reached through the browser host
port): a host that resolves to a link-local address, which includes the cloud
metadata endpoint, is refused. Private and loopback addresses stay reachable,
exactly as for ``http_fetch``. The guard fails closed: when the resolver itself
raises, the request is refused rather than sent unchecked.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from browser.errors import (
    E_BAD_REQUEST,
    E_GUARD_FAILED,
    E_NO_HOST,
    E_REDIRECT_LINK_LOCAL,
    E_URL_SCHEME_HTTP,
    LINK_LOCAL_MESSAGE,
    BrowserBlocked,
    BrowserError,
)
from browser.host import resolves_to_link_local
from browser.redact import display_url

logger = logging.getLogger("logos.browser.guard")

# Chrome's own URL blocklist: a backstop under the Fetch interception, matching literal
# link-local hosts even when the interception of some target could not be enabled.
LITERAL_LINK_LOCAL_PATTERNS = [
    "*://169.254.*",
    "*://[fe8*",
    "*://[fe9*",
    "*://[fea*",
    "*://[feb*",
    "*://metadata.google.internal*",
]

_GUARDED_SCHEMES = ("http", "https", "ws", "wss")


def _host_of(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def ensure_allowed(url: str, *, hop: int | None = None) -> None:
    """Raise unless ``url`` is an http(s) address whose host is not link-local.

    ``hop`` is the redirect number (None or 0 for the request itself); it picks the
    message and is reported as the blocking stage.
    """
    text = str(url or "")
    try:
        parts = urlsplit(text)
        host = parts.hostname
    except ValueError as exc:
        raise BrowserError(E_BAD_REQUEST.format(reason=str(exc))) from None
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise BrowserError(E_URL_SCHEME_HTTP.format(scheme=scheme or "?"))
    if not host:
        raise BrowserError(E_NO_HOST.format(url=display_url(text)))
    stage = "redirect" if hop else "request"
    try:
        blocked = bool(resolves_to_link_local(text))
    except Exception as exc:
        logger.debug("guard resolver failed for %s: %s", host, type(exc).__name__)
        raise BrowserBlocked(
            E_GUARD_FAILED.format(url=display_url(text), type=type(exc).__name__),
            details={"blocked_host": host, "blocked_stage": stage},
        ) from None
    if not blocked:
        return
    logger.debug("guard blocked %s (%s)", host, stage)
    if hop:
        raise BrowserBlocked(
            E_REDIRECT_LINK_LOCAL.format(url=display_url(text)),
            details={"blocked_host": host, "blocked_stage": stage},
        )
    raise BrowserBlocked(
        f"{LINK_LOCAL_MESSAGE}: {host}",
        details={"blocked_host": host, "blocked_stage": stage},
    )


def is_blocked(url: str) -> bool:
    """True when ``url`` must not be fetched; never raises (errors count as blocked)."""
    try:
        text = str(url or "")
        scheme = (urlsplit(text).scheme or "").lower()
        if scheme not in _GUARDED_SCHEMES:
            return False
        probe = text
        if scheme in ("ws", "wss"):
            # The resolver reads only the host; give it a scheme it knows.
            probe = ("https" if scheme == "wss" else "http") + text[len(scheme) :]
        return bool(resolves_to_link_local(probe))
    except Exception:
        return True


def blocked_host(url: str) -> str:
    """Host name of a blocked URL for notes and events (never the full URL)."""
    return _host_of(str(url or "")) or "?"


__all__ = [
    "LITERAL_LINK_LOCAL_PATTERNS",
    "blocked_host",
    "ensure_allowed",
    "is_blocked",
]
