"""The local model's own settings: the server, the model, its context, its tools and its instructions.

The server is asmrun, the owner's runner written in assembly (research/asmrun), which
answers Ollama's API on the PC (owner, 08.11.2026: "I am not going to have Ollama anymore
so replace"). The client code and the package name stay as they were for Ollama: the
protocol is the same (POST /api/chat with the whole dialog, one JSON line back, GET
/api/version, /api/tags, /api/ps, POST /api/show). asmrun is single-threaded and keeps
one conversation: it answers one request at a time, and when a request continues the
dialog it last saw, only the new turn is read; another dialog starts the model's state
over. The settings are fixed and read-only; ⚙️ shows them on the asmrun tab through
GET /api/ollama/settings.
"""

from __future__ import annotations

from typing import Any

# asmrun on the owner's PC (Intel N100, 8 GB RAM): `asmrun.exe <model> --chat --no-tools
# --http 11434 ...` under Windows, or the same runner booted from the USB stick with no
# operating system (then the address is the one its screen prints). The port is Ollama's,
# so nothing else had to move.
URL = "http://192.168.10.6:11434"

# The models Logos serves through asmrun: the key Logos uses -> its catalog entry. The
# runner serves the one model it was started with and answers under any name; the
# "upstream" names the GGUF on the stick for the person. No price applies.
# "think": asmrun runs Qwen3.5 with thinking off (the empty <think></think> block), so the
# flag is False for every model.
MODELS: dict[str, dict[str, Any]] = {
    "asmrun-qwen35-9b": {
        "name": "Qwen3.5 9B (asmrun)",
        "upstream": "qwen35-9b-opus-distill",
        "provider": "asmrun",
        "type": "text",
        "capabilities": {"tools": True, "function_calling": True, "multimodal": None},
        "context_window": 2048,
        "think": False,
        "input": 0.0,
        "cached": 0.0,
        "tool": 0.0,
        "output": 0.0,
    },
}

# The context the runner was started with (-c 2048 on the stick's cfg); asmrun ignores
# the request's options and uses its own.
CONTEXT = 2048
# -1: the answer runs until the model stops or the runner's -n is reached (done_reason
# "length" then).
NUM_PREDICT = -1
# Prompt tokens the runner reads per step (its ACT_SLOTS).
NUM_BATCH = 32
# Thinking for a model whose MODELS entry names none: off.
THINK = False


def think(upstream_or_key: str) -> bool:
    """Whether the model (its Logos key or its upstream name) thinks before answering."""
    for key, info in MODELS.items():
        if upstream_or_key in (key, info["upstream"]):
            return bool(info.get("think", THINK))
    return THINK

# The tool categories the local model is given. All tool schemas together are ~33,000
# tokens: they do not fit its context, and the N100 reads a prompt at a few dozen
# tokens/s. python (owner, 05.11.2026) runs code in the Logos interpreter on the phone.
TOOLS = ("remember", "python")

# The local model's instructions, ahead of every turn. They are constant, so the runner's
# continuing state keeps the same beginning from turn to turn.
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

USER_TEXT_UNREACHABLE = "Локальная модель не ответила: проверьте, что asmrun запущен на ПК."


def options() -> dict[str, Any]:
    """The /api/chat options every request sends (asmrun reads none of them)."""
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
