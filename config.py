"""Alice Pro - Configuration & Pricing"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_ENV_FILE = Path(__file__).resolve().with_name(".env")
load_dotenv(_ENV_FILE)

# Keep compatibility with the legacy YC_API_KEY name while preferring the
# canonical YANDEX_API_KEY variable. Never put credentials in source code.
# Provider credentials are resolved at runtime from provider_credentials.
# No provider API key belongs in application configuration.

PROJECT_ID = os.getenv("YANDEX_PROJECT_ID", "b1g1fekh2198nuan1tnh")
BASE_URL = os.getenv("YANDEX_BASE_URL", "https://ai.api.cloud.yandex.net/v1")
CLOUDRU_BASE_URL = os.getenv(
    "CLOUDRU_BASE_URL",
    "https://foundation-models.api.cloud.ru/v1",
)
YANDEX_PROVIDER_KEY_ID = os.getenv("YANDEX_PROVIDER_KEY_ID")
CLOUDRU_API_KEY_ID = os.getenv("CLOUDRU_API_KEY_ID")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL", "")

HOST = "0.0.0.0"
PORT = 8080

TEXT_MODELS = {
    "aliceai-llm": {"name": "Alice AI LLM", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.50, "cached": 0.50, "tool": 0.13, "output": 1.20},
    "yandexgpt-5.1": {"name": "YandexGPT 5.1 Pro", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.80, "cached": 0.80, "tool": 0.20, "output": 0.80},
    "yandexgpt-5-pro": {"name": "YandexGPT 5 Pro", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 1.20, "cached": 1.20, "tool": 1.20, "output": 1.20},
    "yandexgpt-5-lite": {"name": "YandexGPT 5 Lite", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.20, "cached": 0.20, "tool": 0.20, "output": 0.20},
    "aliceai-llm-flash": {"name": "Alice AI LLM Flash", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.10, "cached": 0.025, "tool": 0.025, "output": 0.20},
    "deepseek-v4-flash": {"name": "DeepSeek V4 Flash", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.30, "cached": 0.075, "tool": 0.075, "output": 0.50},
    "qwen3-235b-a22b-fp8": {"name": "Qwen3 235B A22B FP8", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.50, "cached": 0.50, "tool": 0.50, "output": 0.50},
    "gpt-oss-120b": {"name": "GPT OSS 120B", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.30, "cached": 0.30, "tool": 0.30, "output": 0.30},
    "gpt-oss-20b": {"name": "GPT OSS 20B", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.10, "cached": 0.10, "tool": 0.10, "output": 0.10},
    "qwen3.6-35b-a3b": {"name": "Qwen3.6 35B A3B", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": True}, "input": 0.20, "cached": 0.05, "tool": 0.05, "output": 0.30},
}

VOICE_MODELS = {
    "speech-realtime-260528": {"name": "Speech Realtime 260528", "type": "voice", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.10, "cached": 0.025, "tool": 0.025, "output": 0.20},
    "speech-realtime-250923": {"name": "Speech Realtime 250923", "type": "voice", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.80, "cached": 0.20, "tool": 0.20, "output": 0.80},
    "speech-realtime-deepseek-v4-flash": {"name": "Speech Realtime DeepSeek V4 Flash", "type": "voice", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.30, "cached": 0.075, "tool": 0.075, "output": 0.50},
}

ALL_MODELS = {**TEXT_MODELS, **VOICE_MODELS}
AUDIO_STT_PRICE_PER_SEC = 0.0264
AUDIO_TTS_PRICE_PER_SEC = 0.0203


class Config:
    PROJECT_ID = PROJECT_ID
    BASE_URL = BASE_URL
    CLOUDRU_BASE_URL = CLOUDRU_BASE_URL
    YANDEX_PROVIDER_KEY_ID = YANDEX_PROVIDER_KEY_ID
    CLOUDRU_API_KEY_ID = CLOUDRU_API_KEY_ID
    SUPABASE_URL = SUPABASE_URL
    SUPABASE_ANON_KEY = SUPABASE_ANON_KEY
    SUPABASE_SERVICE_ROLE_KEY = SUPABASE_SERVICE_ROLE_KEY
    SUPABASE_DB_URL = SUPABASE_DB_URL
    HOST = HOST
    PORT = PORT


def get_model_info(model_key):
    return ALL_MODELS.get(model_key, TEXT_MODELS["aliceai-llm"])


def get_model_uri(model_key):
    """Build the Yandex model URI without changing existing request behavior."""
    return "gpt://" + PROJECT_ID + "/" + model_key + "/latest"


def calculate_cost(model_key, input_tokens, output_tokens=0, cached_tokens=0, tool_tokens=0):
    m = get_model_info(model_key)
    cost = (
        input_tokens * m["input"] +
        cached_tokens * m.get("cached", m["input"]) +
        tool_tokens * m.get("tool", m["input"]) +
        output_tokens * m.get("output", m["input"])
    ) / 1000
    return round(cost, 2)


def calculate_full_cost(model_key, usage):
    if not usage:
        return 0.0
    m = get_model_info(model_key)
    token_cost = (
        usage.get("input_tokens", 0) * m["input"] +
        usage.get("output_tokens", 0) * m.get("output", m["input"])
    ) / 1000
    audio_cost = (
        usage.get("audio_seconds_stt", 0) * AUDIO_STT_PRICE_PER_SEC +
        usage.get("audio_seconds_tts", 0) * AUDIO_TTS_PRICE_PER_SEC
    )
    return round(token_cost + audio_cost, 4)


config = Config()
REPO_DIR = "/sdcard/repo"

try:
    import trace_mirror_integration  # noqa: F401,E402
except ImportError:
    logger.debug("Trace mirror integration is unavailable", exc_info=True)
except Exception:
    logger.warning("Failed to install trace mirror integration", exc_info=True)
