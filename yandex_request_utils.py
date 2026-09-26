"""Request logging and payload sanitization helpers for the Yandex client."""

import re

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
            key_lower = k.lower() if isinstance(k, str) else str(k).lower()
            if key_lower in _BINARY_KEYS and isinstance(v, str) and len(v) > 1000:
                result[k] = f"<AUDIO/BINARY MASKED: {len(v)} chars>"
            else:
                result[k] = sanitize_for_log(v)
        return result
    if isinstance(obj, list):
        return [sanitize_for_log(item) for item in obj]
    if isinstance(obj, str):
        if len(obj) > _BINARY_THRESHOLD:
            if obj.startswith("data:"):
                return f"<DATA_URI MASKED: {len(obj)} chars>"
            if _BASE64_RE.match(obj):
                return f"<BASE64 MASKED: {len(obj)} chars>"
        return obj
    return obj
