"""The local model on the owner's PC: chat and embeddings through Ollama's API, served by asmrun since 08.11.2026.

Logos has no Yandex API since 04.11.2026 (owner) and no Ollama since 08.11.2026: asmrun, the
owner's runner in assembly, answers the same API. This module has its own settings (``settings``), its chat client
(``OllamaClient``), embeddings and status. Answers come back in the Responses API
shape, so the chat tool loop, parsers and traces read them like any provider answer.
"""

from ollama_client import settings
from ollama_client.chat import OllamaError, ask, chat_messages, chat_tools
from ollama_client.client import OllamaClient, OllamaClientError
from ollama_client.embeddings import embed
from ollama_client.status import status

__all__ = [
    "OllamaClient",
    "OllamaClientError",
    "OllamaError",
    "ask",
    "chat_messages",
    "chat_tools",
    "embed",
    "settings",
    "status",
]
