"""Ollama's own settings: the server, the model, its context, its tools and its instructions.

They are fixed and read-only (owner, 05.11.2026: "add olama settings", "separate yandex
api and olama and make them fully independant"); ⚙️ shows them on the Ollama tab
through GET /api/ollama/settings. Nothing from the Yandex Responses settings (sampling,
store, background, ...) reaches Ollama.
"""

from __future__ import annotations

from typing import Any

# Ollama on the owner's PC (Intel N100, 8 GB RAM, OLLAMA_HOST=0.0.0.0:11434). The PC's
# Ollama compresses its context cache (OLLAMA_FLASH_ATTENTION=1, OLLAMA_KV_CACHE_TYPE=q4_0)
# and keeps one model loaded (OLLAMA_MAX_LOADED_MODELS=1, so it never swaps to the SSD).
# Owner, 05.11.2026: "lets reduce model ram usage": the q4_0 cache holds 16384 tokens in
# ~650 MB instead of 1224 MB (q8_0).
URL = "http://192.168.10.6:11434"

# The models Logos serves through Ollama: the key Logos uses -> its catalog entry. The key is
# not a Yandex catalog key (qwen3-4b is), so no Yandex price or profile applies.
# qwen3:1.7b was tried on 05.11.2026 (~34/~9 tokens/s read/write against ~14/~4.5): it
# called remember but answered with the call as text, so the owner chose qwen3:4b.
# "think": whether the model thinks before answering (Ollama's /api/chat "think"); a model
# without thinking (qwen2.5-coder) rejects the flag with HTTP 400.
MODELS: dict[str, dict[str, Any]] = {
    "ollama-qwen3-4b": {
        "name": "Qwen3 4B (Ollama)",
        "upstream": "qwen3:4b",
        "provider": "ollama",
        "type": "text",
        "capabilities": {"tools": True, "function_calling": True, "multimodal": None},
        "context_window": 16384,
        "think": True,
        "input": 0.0,
        "cached": 0.0,
        "tool": 0.0,
        "output": 0.0,
    },
    # The coding model (owner, 05.11.2026: "specific ollama models for coding", "compress to
    # the maximum"). Every dedicated Qwen coder is 30B or larger (19 GB+); the largest coder
    # the 8 GB PC holds beside Windows is Qwen2.5-Coder 3B, and q2_K is Ollama's smallest
    # quantisation of it: 1.3 GB against 1.9 GB (q4_K_M). 7B at q2_K (3.0 GB) would swap
    # again (#1760). The model does not think, so "think" is off for it.
    "ollama-qwen2.5-coder-3b": {
        "name": "Qwen2.5 Coder 3B q2_K (Ollama)",
        "upstream": "qwen2.5-coder:3b-instruct-q2_K",
        "provider": "ollama",
        "type": "text",
        "capabilities": {"tools": True, "function_calling": True, "multimodal": None},
        "context_window": 16384,
        "think": False,
        "input": 0.0,
        "cached": 0.0,
        "tool": 0.0,
        "output": 0.0,
    },
}

# The whole room for the prompt, the thinking and the answer (owner, 05.11.2026: reasoning
# and output at maximum).
CONTEXT = 16384
# -1: the answer runs until the model stops or the context is full; no output cap.
NUM_PREDICT = -1
# Prompt tokens read per step. Measured on the PC (05.11.2026): 512 needs ~100 MB less RAM
# than Ollama's 1024 and reads a prompt ~10% faster, with the same answer.
NUM_BATCH = 512
# Thinking for a model whose MODELS entry names none; qwen3 thinks: its thinking streams
# live and is what remember searches by.
THINK = True


def think(upstream_or_key: str) -> bool:
    """Whether the model (its Logos key or its Ollama name) thinks before answering."""
    for key, info in MODELS.items():
        if upstream_or_key in (key, info["upstream"]):
            return bool(info.get("think", THINK))
    return THINK

# The tool categories a local model is given. All tool schemas together are ~33,000
# tokens: they do not fit its context, and the N100 reads a prompt at ~14 tokens/s.
# python (owner, 05.11.2026: "make python execution tool and add alongside remember tool")
# runs code in the Logos interpreter on the phone; its runs are embedded for remember.
TOOLS = ("remember", "python")

# Ollama's own instructions, ahead of every turn. They are constant, so Ollama's prompt
# cache keeps the same beginning from turn to turn.
INSTRUCTIONS = (
    "Ты — Логос, ассистент владельца этого приложения. Приложение Logos работает на "
    "телефоне Redmi 9 в Termux, а ты — локальная модель на ПК владельца. Владелец "
    "общается по-русски и ждёт действия, а не плана.\n"
    "1. Инструменты вызывай только через function calling. Никогда не пиши вызов "
    "текстом ответа и никакой служебной разметкой.\n"
    "2. Инструмент remember вспоминает: он ищет в памяти Логоса по твоим мыслям и "
    "возвращает найденное — прошлые диалоги, факты и то, что ты знаешь о своих "
    "инструментах. Вызывай его, когда нужно вспомнить что-то, чего нет в этом диалоге.\n"
    "3. Не хватает параметра, доступа или инструмента — скажи это одним коротким "
    "сообщением: что именно нужно. Не придумывай значения.\n"
    "4. Отвечай коротко: что сделано, что получилось, что нужно от владельца.\n"
    "5. Блокер сообщай честно и сразу."
)

USER_TEXT_UNREACHABLE = "Локальная модель не ответила: проверьте, что Ollama запущена на ПК."


def options() -> dict[str, Any]:
    """The /api/chat options every request sends."""
    return {"num_ctx": CONTEXT, "num_predict": NUM_PREDICT, "num_batch": NUM_BATCH}


def describe() -> dict[str, Any]:
    """The settings as ⚙️ shows them."""
    return {
        "url": URL,
        "models": {key: info["upstream"] for key, info in MODELS.items()},
        "options": options(),
        "think": {key: bool(info.get("think", THINK)) for key, info in MODELS.items()},
        "stream": True,
        "tools": list(TOOLS),
        "instructions": INSTRUCTIONS,
    }
