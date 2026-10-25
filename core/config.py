"""Logos - Configuration & Pricing"""
from core.env import env_get

import logging
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from provider_accounts.claude_catalog import CLAUDE_MODELS

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
    raw = (env_get("LOGOS_DONATION_URL") or "").strip()
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
    if parts.scheme.lower() not in {"http", "https"} or not hostname:
        logger.warning("LOGOS_DONATION_URL ignored: only absolute http/https URLs are allowed")
        return None
    if parts.username is not None or parts.password is not None:
        logger.warning("LOGOS_DONATION_URL ignored: URLs with embedded credentials are not allowed")
        return None
    return raw


HOST = "127.0.0.1"
PORT = 8080

TEXT_MODELS = {
    "logos-runtime": {
        "name": "Logos Runtime",
        "upstream": "logosai-llm",
        "type": "text",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 0.50,
        "cached": 0.50,
        "tool": 0.13,
        "output": 1.20,
    },
    "yandexgpt-5.1": {
        "name": "YandexGPT 5.1 Pro",
        "type": "text",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 0.80,
        "cached": 0.80,
        "tool": 0.20,
        "output": 0.80,
    },
    "yandexgpt-5-pro": {
        "name": "YandexGPT 5 Pro",
        "type": "text",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 1.20,
        "cached": 1.20,
        "tool": 1.20,
        "output": 1.20,
    },
    "yandexgpt-5-lite": {
        "name": "YandexGPT 5 Lite",
        "type": "text",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 0.20,
        "cached": 0.20,
        "tool": 0.20,
        "output": 0.20,
    },
    "logos-runtime-flash": {
        "name": "Logos Runtime Flash",
        "upstream": "logosai-llm-flash",
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
        # Yandex common-instance RUB/1000 tokens, checked 2026-10-22.
        # Hosted tool/session charges are additional; this is not a billed total.
        "input": 0.30,
        "cached": 0.075,
        "tool": 0.075,
        "output": 0.50,
    },
    "deepseek-v4-flash": {
        "name": "DeepSeek V4 Flash",
        "type": "text",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 0.30,
        "cached": 0.075,
        "tool": 0.075,
        "output": 0.50,
    },
    "qwen3-235b-a22b-fp8": {
        "name": "Qwen3 235B A22B FP8",
        # Retired by the provider on 2026-09-30: kept for accounting, not selectable.
        "available": False,
        "type": "text",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 0.50,
        "cached": 0.50,
        "tool": 0.50,
        "output": 0.50,
    },
    "gpt-oss-120b": {
        "name": "GPT OSS 120B",
        "type": "text",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 0.30,
        "cached": 0.30,
        "tool": 0.30,
        "output": 0.30,
    },
    "gpt-oss-20b": {
        "name": "GPT OSS 20B",
        "type": "text",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 0.10,
        "cached": 0.10,
        "tool": 0.10,
        "output": 0.10,
    },
    "qwen3.6-35b-a3b": {
        "name": "Qwen3.6 35B A3B",
        "type": "text",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": True},
        "input": 0.20,
        "cached": 0.05,
        "tool": 0.05,
        "output": 0.30,
    },
}

VOICE_MODELS = {
    "speech-realtime-260528": {
        "name": "Speech Realtime 260528",
        # Retired by the provider on 2026-10-12: kept for accounting, not selectable.
        "available": False,
        "type": "voice",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 0.10,
        "cached": 0.025,
        "tool": 0.025,
        "output": 0.20,
    },
    "speech-realtime-250923": {
        "name": "Speech Realtime 250923",
        # Retired by the provider on 2026-10-12: kept for accounting, not selectable.
        "available": False,
        "type": "voice",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 0.80,
        "cached": 0.20,
        "tool": 0.20,
        "output": 0.80,
    },
    "speech-realtime-deepseek-v4-flash": {
        "name": "Speech Realtime DeepSeek V4 Flash",
        "type": "voice",
        "capabilities": {"tools": None, "function_calling": None, "multimodal": None},
        "input": 0.30,
        "cached": 0.075,
        "tool": 0.075,
        "output": 0.50,
    },
}

TEXT_MODELS.update(CLAUDE_MODELS)
ALL_MODELS = {**TEXT_MODELS, **VOICE_MODELS}
AUDIO_STT_PRICE_PER_SEC = 0.0264
AUDIO_TTS_PRICE_PER_SEC = 0.0203


class Config:
    BASE_URL = BASE_URL
    CLOUDRU_BASE_URL = CLOUDRU_BASE_URL
    YANDEX_PROVIDER_KEY_ID = YANDEX_PROVIDER_KEY_ID
    CLOUDRU_API_KEY_ID = CLOUDRU_API_KEY_ID
    HOST = HOST
    PORT = PORT


# Keys renamed in #1316, and models the provider retired whose successor the project approved.
# Stored conversations and older clients may still send them: requests go to the successor.
LEGACY_MODEL_KEYS = {
    "logosai-llm": "logos-runtime",
    "logosai-llm-flash": "logos-runtime-flash",
    "qwen3-235b-a22b-fp8": "qwen3.6-35b-a3b",
    "speech-realtime-250923": "speech-realtime-deepseek-v4-flash",
    "speech-realtime-260528": "speech-realtime-deepseek-v4-flash",
}


def canonical_model_key(model_key: str) -> str:
    return LEGACY_MODEL_KEYS.get(model_key, model_key)


def registry_key_for_upstream(upstream_id: str) -> str | None:
    for key, info in TEXT_MODELS.items():
        if info.get("upstream") == upstream_id:
            return key
    return None


def get_model_info(model_key):
    return ALL_MODELS.get(canonical_model_key(model_key), TEXT_MODELS["logos-runtime"])


# The registry key is ours; the upstream id is the name Yandex knows the model by.
# Keys that are not in the registry (including legacy stored keys) pass through unchanged.
def upstream_model_id(model_key: str) -> str:
    info = ALL_MODELS.get(canonical_model_key(model_key)) or {}
    return str(info.get("upstream", model_key))


def get_model_uri(project_id, model_key):
    """Build the Yandex model URI from the active provider credential."""
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("Yandex project_id is required")
    return "gpt://" + project_id.strip() + "/" + upstream_model_id(model_key) + "/latest"


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
