"""Deterministic stand-in for the Yandex Responses client in tests.

The fake answers with a Responses-shaped payload, so the production parsers
(extract_text, extract_usage) run unchanged on it. It never touches the network
and never reserves provider quota, so tests spend nothing.
"""

from __future__ import annotations

from typing import Any


class FakeModelClient:
    def __init__(self, reply: str = "mock reply") -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def ask(
        self,
        message,
        model_key,
        conversation_id=None,
        params=None,
        execution_trace=None,
        trace_step=None,
    ):
        self.calls.append(
            {
                "message": message,
                "model_key": model_key,
                "conversation_id": conversation_id,
                "params": dict(params or {}),
            }
        )
        # Deterministic character counts, not real tokenization.
        input_tokens = len(str(message))
        output_tokens = len(self.reply)
        return {
            "id": f"resp_mock_{len(self.calls)}",
            "object": "response",
            "status": "completed",
            "model": model_key,
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": self.reply}],
                }
            ],
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
        }

    @staticmethod
    def extract_text(data):
        from yandex_client_modules.parsers import extract_text

        return extract_text(data)

    @staticmethod
    def extract_usage(data):
        from yandex_client_modules.parsers import extract_usage

        return extract_usage(data)


def install(monkeypatch, fake: FakeModelClient | None = None) -> FakeModelClient:
    """Route YandexResponsesClient.ask to the fake for the duration of one test."""
    from yandex_client_modules.client import YandexResponsesClient

    fake = fake or FakeModelClient()
    monkeypatch.setattr(YandexResponsesClient, "ask", fake.ask)
    return fake
