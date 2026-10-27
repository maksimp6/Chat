"""Request logging and payload sanitization helpers for the Yandex client."""

import re

from core.wikifunctions import lowercase_string

_BINARY_THRESHOLD = 100_000
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]{200,}={0,2}$", re.DOTALL)
_BINARY_KEYS = frozenset(
    {
        "audio",
        "audio_bytes",
        "audio_data",
        "file_data",
        "image_data",
        "image_b64",
        "attachment_data",
        "screenshot",
        "pcm",
        "wav",
        "ogg",
    }
)


def sanitize_for_log(obj):
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            key_lower = lowercase_string(k if isinstance(k, str) else str(k))
            if key_lower in _BINARY_KEYS and isinstance(v, str) and len(v) > 1000:
                result[k] = f"<AUDIO/BINARY MASKED: {len(v)} chars>"
            else:
                result[k] = sanitize_for_log(v)
        return result
    if isinstance(obj, list):
        return [sanitize_for_log(item) for item in obj]
    if isinstance(obj, str):
        # Any data URI is binary content, however small: photos never reach logs or traces.
        if obj.startswith("data:"):
            return f"<DATA_URI MASKED: {len(obj)} chars>"
        if len(obj) > _BINARY_THRESHOLD and _BASE64_RE.match(obj):
            return f"<BASE64 MASKED: {len(obj)} chars>"
        return obj
    return obj
