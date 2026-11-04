"""Ollama on the owner's PC: chat and embeddings of the models Logos serves locally.

Logos has no Yandex API since 04.11.2026 (owner). This module knows only Ollama: the
caller passes the address, the model and the context size. Answers come back in the
Responses API shape, so the chat tool loop, parsers and traces read them like any
provider answer.
"""

from ollama_client.chat import OllamaError, ask, chat_messages, chat_tools
from ollama_client.embeddings import embed

__all__ = ["OllamaError", "ask", "chat_messages", "chat_tools", "embed"]
