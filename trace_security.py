"""Shared trace-data sanitization helpers.

Kept independent from ExecutionTrace so request/response capture code can be
split out incrementally without changing the existing public class API.
"""
from typing import Any, Set


SENSITIVE_KEYS: Set[str] = {
    "api_key", "apikey", "authorization", "password", "passwd", "secret",
    "token", "access_token", "refresh_token", "cookie", "set-cookie",
}
MAX_REPR = 4000
MAX_DEPTH = 12
MAX_ITEMS = 50


def safe_repr(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > MAX_REPR:
            return value[:MAX_REPR] + "... <truncated>"
        return value
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_ITEMS:
                result["<truncated>"] = f"{len(value) - MAX_ITEMS} more items"
                break
            key_text = str(key)
            if key_text.lower().replace("-", "_") in SENSITIVE_KEYS:
                result[key_text] = "<redacted>"
            else:
                result[key_text] = safe_repr(item, depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        values = list(value)
        result = [safe_repr(item, depth + 1) for item in values[:MAX_ITEMS]]
        if len(values) > MAX_ITEMS:
            result.append(f"<truncated: {len(values) - MAX_ITEMS} more items>")
        return result
    try:
        text = repr(value)
    except Exception:
        text = f"<{type(value).__name__}: repr failed>"
    if len(text) > MAX_REPR:
        text = text[:MAX_REPR] + "... <truncated>"
    return text


def sanitize_trace_value(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > MAX_REPR:
            return value[:MAX_REPR] + "... <truncated>"
        return value
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:MAX_ITEMS]:
            key_text = str(key)
            normalized = key_text.lower().replace("-", "_")
            result[key_text] = "<redacted>" if normalized in SENSITIVE_KEYS else sanitize_trace_value(item, depth + 1)
        if len(value) > MAX_ITEMS:
            result["<truncated>"] = f"{len(value) - MAX_ITEMS} more items"
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [sanitize_trace_value(item, depth + 1) for item in items[:MAX_ITEMS]]
        if len(items) > MAX_ITEMS:
            result.append(f"<truncated: {len(items) - MAX_ITEMS} more items>")
        return result
    return safe_repr(value, depth)
