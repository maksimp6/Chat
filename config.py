"""Alice Pro - Configuration & Pricing."""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_ENV_FILE = Path(__file__).resolve().with_name(".env")
load_dotenv(_ENV_FILE)

# Yandex compatibility: keep the historical API_KEY name in Python while the
# environment remains explicitly provider-scoped.
API_KEY = os.getenv("YANDEX_API_KEY") or os.getenv("YC_API_KEY")
CLOUDRU_API_KEY = os.getenv("CLOUDRU_API_KEY")

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
    "alice-lite": {"name": "Alice Lite", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.20, "cached": 0.05, "tool": 0.05, "output": 0.20},
    "aliceai-llm": {"name": "Alice AI LLM", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.50, "cached": 0.50, "tool": 0.13, "output": 1.20},
    "yandexgpt-5.1": {"name": "YandexGPT 5.1 Pro", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.80, "cached": 0.80, "tool": 0.20, "output": 0.80},
    "yandexgpt-5-pro": {"name": "YandexGPT 5 Pro", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 1.20, "cached": 1.20, "tool": 1.20, "output": 1.20},
    "yandexgpt-5-lite": {"name": "YandexGPT 5 Lite", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.20, "cached": 0.20, "tool": 0.20, "output": 0.20},
    "aliceai-llm-flash": {"name": "Alice AI LLM Flash", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.10, "cached": 0.025, "tool": 0.025, "output": 0.20},
    "deepseek-v4-flash": {"name": "DeepSeek V4 Flash", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.30, "cached": 0.075, "tool": 0.075, "output": 0.50},
    "qwen3-235b-a22b-fp8": {"name": "Qwen3 235B A22B FP8", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.50, "cached": 0.50, "tool": 0.50, "output": 0.50},
    "gpt-oss-120b": {"name": "GPT OSS 120B", "type": "text", "capabilities": {"tools": None, "function_calling": None, "multimodal": None}, "input": 0.40, "cached": 0.04, "tool": 0.04, "output": 0.90},
}

VOICE_MODELS = {}
