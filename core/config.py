"""Logos - Configuration & Pricing"""
from core.wikifunctions import lowercase_string, trim_string

from core.env import env_get

import logging
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_ENV_FILE = Path(__file__).resolve().with_name(".env")
load_dotenv(_ENV_FILE)

# Keep compatibility with the legacy YC_API_KEY name while preferring the
# canonical YANDEX_API_KEY variable. Never put credentials in source code.
# Provider credentials are resolved at runtime from provider_credentials.
# No provider API key belongs in application configuration.

BASE_URL = env_get("YANDEX_BASE_URL", "https://ai.api.cloud.yandex.net/v1")
CLOUDRU_BASE_URL = env_get(
    "CLOUDRU_BASE_URL",
    "https://foundation-models.api.cloud.ru/v1",
)
YANDEX_PROVIDER_KEY_ID = env_get("YANDEX_PROVIDER_KEY_ID")
CLOUDRU_API_KEY_ID = env_get("CLOUDRU_API_KEY_ID")


def get_donation_url():
    """Return the optional public donation page URL, or None when disabled.

    Only absolute http/https URLs without embedded credentials are accepted so
    the header button can never be turned into a script or credential carrier.
    """
    raw = trim_string((env_get("LOGOS_DONATION_URL") or ""))
    if not raw:
        return None
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        logger.warning("LOGOS_DONATION_URL ignored: contains whitespace or control characters")
        return None
    try:
        parts = urlsplit(raw)
        hostname = parts.hostname
    except ValueError:
        logger.warning("LOGOS_DONATION_URL ignored: malformed URL")
        return None
    if lowercase_string(parts.scheme) not in {"http", "https"} or not hostname:
        logger.warning("LOGOS_DONATION_URL ignored: only absolute http/https URLs are allowed")
        return None
    if parts.username is not None or parts.password is not None:
        logger.warning("LOGOS_DONATION_URL ignored: URLs with embedded credentials are not allowed")
        return None
    return raw


HOST = "127.0.0.1"
PORT = 8080

TEXT_MODELS = {
    "yandexgpt-5.1": {
        "upstream": "yandexgpt-5.1",
        "name": "YandexGPT Pro 5.1",
        "type": "text",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 0.80,
        "cached": 0.80,
        "tool": 0.20,
        "output": 0.80,
    },
    "yandexgpt-5-pro": {
        "upstream": "yandexgpt-5-pro",
        "name": "YandexGPT Pro 5",
        "type": "text",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 1.20,
        "cached": 1.20,
        "tool": 1.20,
        "output": 1.20,
    },
    "yandexgpt-5-lite": {
        "name": "YandexGPT Lite 5",
        "lifecycle": {"state": "deprecated", "shutdown_on": "2026-11-15"},
        "type": "text",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 0.20,
        "cached": 0.20,
        "tool": 0.20,
        "output": 0.20,
    },
    "gpt-oss-120b": {
        "name": "gpt-oss-120b",
        "lifecycle": {"state": "deprecated", "available_until": "2026-10-30"},
        "type": "text",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 0.30,
        "cached": 0.30,
        "tool": 0.30,
        "output": 0.30,
    },
    "gpt-oss-20b": {
        "name": "gpt-oss-20b",
        "lifecycle": {"state": "deprecated", "available_until": "2026-10-30"},
        "type": "text",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 0.10,
        "cached": 0.10,
        "tool": 0.10,
        "output": 0.10,
    },
    "logos-runtime": {
        "name": "Alice AI LLM",
        "upstream": "aliceai-llm",
        "type": "text",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 0.50,
        "cached": 0.50,
        "tool": 0.13,
        "output": 1.20,
    },
    "logos-runtime-flash": {
        "name": "Alice AI LLM Flash",
        "upstream": "aliceai-llm-flash",
        "type": "text",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 0.10,
        "cached": 0.025,
        "tool": 0.025,
        "output": 0.20,
    },
    "deepseek-v4.1-flash": {
        "name": "DeepSeek V4.1 Flash",
        "type": "text",
        "capabilities": {"tools": True, "function_calling": True, "multimodal": None},
        "context_window": 1048576,
        # Yandex common-instance RUB/1000 tokens. Hosted tool/session charges are additional.
        "input": 0.30,
        "cached": 0.075,
        "tool": 0.075,
        "output": 0.50,
    },
    "deepseek-v4-flash": {
        "name": "DeepSeek V4 Flash",
        "lifecycle": {"state": "deprecated", "shutdown_on": "2026-11-15"},
        # Listed as deprecated on the Yandex model page, shutdown 2026-11-15.
        "type": "text",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 0.30,
        "cached": 0.075,
        "tool": 0.075,
        "output": 0.50,
    },
    "qwen3.6-35b-a3b": {
        "name": "Qwen3.6 35B",
        "type": "text",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": True},
        "input": 0.20,
        "cached": 0.05,
        "tool": 0.05,
        "output": 0.30,
    },
}

VOICE_MODELS = {
    "speech-realtime-deepseek-v4-flash": {
        "name": "Speech Realtime DeepSeek V4 Flash",
        "upstream": "speech-realtime-deepseek-v4-flash",
        "type": "voice",
        "transport": "realtime",
        "voice_family": "general",
        "capabilities": {
            "audio_input": True, "text_input": True, "audio_output": True,
            "tools": False, "function_calling": False,
        },
        "pricing_status": "unknown",
    },
    "speech-realtime-max-tts-live": {
        "name": "Speech Realtime Max TTS Live",
        "upstream": "speech-realtime-max-tts-live/latest",
        "type": "voice",
        "transport": "realtime",
        "voice_family": "livetts",
        "capabilities": {
            "audio_input": True, "text_input": True, "audio_output": True,
            "tools": False, "function_calling": False,
        },
        "pricing_status": "unknown",
    },
    "speech-realtime-tts-live": {
        "name": "Speech TTS Live",
        "upstream": "speech-realtime-tts-live/latest",
        "type": "voice",
        "transport": "realtime",
        "voice_family": "livetts",
        "capabilities": {
            "audio_input": False, "text_input": True, "audio_output": True,
            "tools": False, "function_calling": False,
        },
        "pricing_status": "unknown",
    },
}

IMAGE_MODELS = {
    "alice-ai-art": {
        "name": "Alice AI ART",
        "upstream": "aliceai-image-art-3.0",
        "type": "image",
        "provider": "yandex_image",
        "transport": "images",
        "max_prompt_characters": 500,
        "sizes": ["1024x1024", "1536x1024", "1024x1536"],
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        # The Yandex docs list no price for this model, so its cost stays unknown.
        "pricing_status": "unknown",
    },
}

# These models use dedicated APIs and must never be sent to the chat endpoint.
EMBEDDING_MODELS = {
    "text-search-doc": {
        "name": "Text Search Document",
        "upstream": "text-search-doc/latest",
        "type": "embedding",
        "dimensions": [256],
    },
    "text-search-query": {
        "name": "Text Search Query",
        "upstream": "text-search-query/latest",
        "type": "embedding",
        "dimensions": [256],
    },
    "text-embeddings-v2-doc": {
        "name": "Text Embeddings v2 Document",
        "upstream": "text-embeddings-v2-doc/",
        "type": "embedding",
        "dimensions": [128, 256, 512, 768],
    },
    "text-embeddings-v2-query": {
        "name": "Text Embeddings v2 Query",
        "upstream": "text-embeddings-v2-query/",
        "type": "embedding",
        "dimensions": [128, 256, 512, 768],
    },
}

CLASSIFIER_MODELS = {
    "classifier-yandexgpt-lite": {
        "name": "YandexGPT Lite Classifier",
        "upstream": "yandexgpt-lite/latest",
        "type": "classifier",
    },
    "classifier-yandexgpt": {
        "name": "YandexGPT Classifier",
        "upstream": "yandexgpt/latest",
        "type": "classifier",
    },
}

ALL_MODELS = {**TEXT_MODELS, **VOICE_MODELS, **IMAGE_MODELS}
AUDIO_STT_PRICE_PER_SEC = 0.0264
AUDIO_TTS_PRICE_PER_SEC = 0.0203


class Config:
    BASE_URL = BASE_URL
    CLOUDRU_BASE_URL = CLOUDRU_BASE_URL
    YANDEX_PROVIDER_KEY_ID = YANDEX_PROVIDER_KEY_ID
    CLOUDRU_API_KEY_ID = CLOUDRU_API_KEY_ID
    HOST = HOST
    PORT = PORT


# Keys renamed in #1316. Stored conversations and older clients may still send the old id.
# Stored conversations and older clients may still send them: requests go to the successor.
LEGACY_MODEL_KEYS = {}


def canonical_model_key(model_key: str) -> str:
    return LEGACY_MODEL_KEYS.get(model_key, model_key)


def registry_key_for_upstream(upstream_id: str) -> str | None:
    aliases = {"yandexgpt/rc": "yandexgpt-5.1", "yandexgpt/latest": "yandexgpt-5-pro"}
    if upstream_id in aliases:
        return aliases[upstream_id]
    for key, info in TEXT_MODELS.items():
        upstream = info.get("upstream", key)
        if upstream_id in {upstream, upstream + "/latest"}:
            return key
    return None


def get_model_info(model_key):
    info = ALL_MODELS.get(canonical_model_key(model_key))
    if info is None:
        # Discovered tuned/versioned models have no public tariff. Preserve unknown
        # cost instead of inventing another model's rate or failing after execution.
        if not isinstance(model_key, str) or not trim_string(model_key):
            raise ValueError("model key is required")
        return {"name": model_key, "type": "text", "pricing_status": "unknown"}
    return info


# The registry key is ours; the upstream id is the name Yandex knows the model by.
# Keys that are not in the registry (including legacy stored keys) pass through unchanged.
def upstream_model_id(model_key: str) -> str:
    info = ALL_MODELS.get(canonical_model_key(model_key)) or {}
    return str(info.get("upstream", model_key))


def get_model_uri(project_id, model_key):
    """Preserve the exact model/version while binding it to the active project."""
    if not isinstance(project_id, str):
        raise ValueError("Yandex project_id is required")
    project_id = trim_string(project_id)
    if not project_id:
        raise ValueError("Yandex project_id is required")
    if (
        any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in project_id)
        or any(ch in project_id for ch in "/?#:@\\")
    ):
        raise ValueError("invalid Yandex project_id")
    upstream = upstream_model_id(model_key)
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in upstream):
        raise ValueError("invalid Yandex model ID")
    if "?" in upstream or "#" in upstream:
        raise ValueError("model URI cannot contain a query or fragment")
    if upstream.startswith("gpt://"):
        parts = urlsplit(upstream)
        if parts.netloc != project_id or not parts.path.startswith("/"):
            raise ValueError("model URI must belong to the active Yandex project")
        suffix = parts.path[1:]
    else:
        suffix = upstream
    if (
        not suffix
        or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in suffix)
        or any(ch in suffix for ch in "?#:\\")
        or any(part in {"", ".", ".."} for part in suffix.split("/"))
    ):
        raise ValueError("invalid Yandex text model ID")
    # Do not strip a tuned suffix, change an explicit version, or append /latest.
    return "gpt://" + project_id + "/" + suffix


def calculate_cost(model_key, input_tokens, output_tokens=0, cached_tokens=0, tool_tokens=0):
    m = get_model_info(model_key)
    if m.get("pricing_status") == "unknown":
        return None
    cost = (
        input_tokens * m["input"]
        + cached_tokens * m.get("cached", m["input"])
        + tool_tokens * m.get("tool", m["input"])
        + output_tokens * m.get("output", m["input"])
    ) / 1000
    return round(cost, 2)


def calculate_full_cost(model_key, usage):
    m = get_model_info(model_key)
    if m.get("pricing_status") == "unknown":
        return None
    if not usage:
        return 0.0
    token_cost = (
        usage.get("input_tokens", 0) * m["input"]
        + usage.get("output_tokens", 0) * m.get("output", m["input"])
    ) / 1000
    audio_cost = (
        usage.get("audio_seconds_stt", 0) * AUDIO_STT_PRICE_PER_SEC
        + usage.get("audio_seconds_tts", 0) * AUDIO_TTS_PRICE_PER_SEC
    )
    return round(token_cost + audio_cost, 4)


config = Config()
REPO_DIR = "/sdcard/repo"
