# Vendored from observability/trace_security.py. Do not edit this copy: change the canonical file
# and run `python3 scripts/check_independence.py --sync-vendored`.
"""Shared trace-data sanitization helpers.

Kept independent from ExecutionTrace so request/response capture code can be
split out incrementally without changing the existing public class API.
"""

import re
from typing import Any


SENSITIVE_KEYS: set[str] = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "passwd",
    "secret",
    "credential",
    "credentials",
    "token",
    "access_token",
    "refresh_token",
    "cookie",
    "set-cookie",
    "private_key",
    "privatekey",
    "ssh_private_key",
    "secret_key",
    "client_secret",
}
MAX_REPR = 4000
MAX_DEPTH = 12
MAX_ITEMS = 50
# Matches credential words as whole underscore-separated parts, so "input_tokens" is not a secret.
_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|_)(?:token|secret|password|passwd|api_?key|authorization|cookie|credentials?|private_?key)(?:_|$)"
)

_INLINE_SECRET = re.compile(
    r"(?i)([A-Za-z0-9_.-]*?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|"
    r"secret|token|authorization|private[_-]?key|ssh[_-]?private[_-]?key|secret[_-]?key|"
    r"client[_-]?secret)[\"']?\s*[=:]\s*)((?:bearer\s+)?[^\s;&|]+)"
)
_INLINE_CLI_SECRET = re.compile(
    r"(?i)((?:--(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|token|authorization|private[_-]?key|ssh[_-]?private[_-]?key|secret[_-]?key|client[_-]?secret))\s+)([^\s;&|]+)"
)
_BEARER_SECRET = re.compile(r"(?i)(\bbearer\s+)([^\s;&|]+)")
_PRIVATE_KEY_BEGIN = "-----BEGIN "


def _is_sensitive_key(key: str) -> bool:
    """Credential-like keys, including camelCase and prefixed names such as accessKeySecret or YANDEX_API_KEY."""
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower().replace("-", "_")
    return bool(_SENSITIVE_KEY_RE.search(normalized))


def _redact_private_key_blocks(value: str) -> str:
    """Redact PEM-like private-key blocks with a deterministic linear scan."""
    parts: list[str] = []
    cursor = 0
    length = len(value)

    while cursor < length:
        begin = value.find(_PRIVATE_KEY_BEGIN, cursor)
        if begin < 0:
            parts.append(value[cursor:])
            break

        label_end = value.find("-----", begin + len(_PRIVATE_KEY_BEGIN))
        if label_end < 0:
            parts.append(value[cursor:])
            break

        label = value[begin + len(_PRIVATE_KEY_BEGIN) : label_end]
        if not label.endswith("PRIVATE KEY"):
            parts.append(value[cursor : label_end + 5])
            cursor = label_end + 5
            continue

        parts.append(value[cursor:begin])
        end_marker = f"-----END {label}-----"
        end = value.find(end_marker, label_end + 5)
        parts.append("<redacted-private-key>")
        if end < 0:
            break
        cursor = end + len(end_marker)

    return "".join(parts)


def _sanitize_string(value: str) -> str:
    """Retain useful trace text while removing common inline credentials."""
    value = _INLINE_SECRET.sub(r"\1<redacted>", value)
    value = _INLINE_CLI_SECRET.sub(r"\1<redacted>", value)
    value = _BEARER_SECRET.sub(r"\1<redacted>", value)
    return _redact_private_key_blocks(value)


def redact_text(value: str) -> str:
    """Remove inline credentials from free text without truncating it."""
    return _sanitize_string(value)


def _distinct_key_text(key: Any, key_text: str, taken: dict[str, Any]) -> str:
    """Key text that keeps keys such as 1 and "1" apart instead of merging them."""
    if key_text not in taken:
        return key_text
    distinct = f"{key_text} ({type(key).__name__})"
    candidate, suffix = distinct, 2
    while candidate in taken:
        candidate = f"{distinct} #{suffix}"
        suffix += 1
    return candidate


def safe_repr(value: Any, depth: int = 0) -> Any:
    """The same sanitization as sanitize_trace_value, kept under its own name."""
    return sanitize_trace_value(value, depth)


def sanitize_trace_value(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str):
            value = _sanitize_string(value)
            if len(value) > MAX_REPR:
                return value[:MAX_REPR] + "... <truncated>"
        return value
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:MAX_ITEMS]:
            key_text = str(key)
            result[_distinct_key_text(key, key_text, result)] = (
                "<redacted>"
                if _is_sensitive_key(key_text)
                else sanitize_trace_value(item, depth + 1)
            )
        if len(value) > MAX_ITEMS:
            result["<truncated>"] = f"{len(value) - MAX_ITEMS} more items"
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [sanitize_trace_value(item, depth + 1) for item in items[:MAX_ITEMS]]
        if len(items) > MAX_ITEMS:
            result.append(f"<truncated: {len(items) - MAX_ITEMS} more items>")
        return result
    try:
        text = repr(value)
    except Exception:
        text = f"<{type(value).__name__}: repr failed>"
    text = _sanitize_string(text)
    if len(text) > MAX_REPR:
        text = text[:MAX_REPR] + "... <truncated>"
    return text
