"""Thin CLI/Termux adapter for the canonical Alice Pro runtime."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import requests


DEFAULT_BASE_URL = "http://127.0.0.1:5000"
DEFAULT_MODEL = "aliceai-llm"
DEFAULT_TIMEOUT = 60.0


class AliceCliError(RuntimeError):
    """Safe CLI-facing backend error that never includes secret URL tokens."""


class AliceCliClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        short_token: str | None = None,
        model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("ALICE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.short_token = (
            short_token if short_token is not None else os.getenv("ALICE_SHORT_TOKEN", "")
        ).strip()
        self.model = (model or os.getenv("ALICE_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self.timeout = float(timeout)
        self.http = session or requests.Session()

    def _url(self, path: str) -> str:
        normalized = "/" + path.lstrip("/")
        if self.short_token:
            prefix = "/" + quote(self.short_token, safe="")
            return f"{self.base_url}{prefix}{normalized}"
        return f"{self.base_url}{normalized}"

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self.http.request(
                method,
                self._url(path),
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise AliceCliError("Alice Pro backend is unavailable") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise AliceCliError("Alice Pro backend returned invalid JSON") from exc

        if response.status_code >= 400:
            error = data.get("error") if isinstance(data, dict) else None
            message = str(error or "request failed")
            raise AliceCliError(f"Alice Pro HTTP {response.status_code}: {message}")

        if not isinstance(data, dict):
            raise AliceCliError("Alice Pro backend returned an invalid response")
        return data

    def create_conversation(self, *, title: str = "Alice Pro CLI") -> str:
        payload = self._request_json(
            "POST",
            "/api/conversations",
            payload={"title": title, "model": self.model},
        )
        conversation_id = str(payload.get("id") or "").strip()
        if not conversation_id:
            raise AliceCliError("Alice Pro did not return a conversation id")
        return conversation_id

    def send_message(
        self,
        conversation_id: str,
        message: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/api/chat",
            payload={
                "conversation_id": conversation_id,
                "session_id": session_id or conversation_id,
                "message": message,
                "model": self.model,
            },
        )

    def execute_approved(
        self,
        conversation_id: str,
        tool_call: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/api/mcp/execute-approved",
            payload={
                "conversation_id": conversation_id,
                "name": tool_call.get("name"),
                "arguments": tool_call.get("arguments") or {},
                "model": self.model,
            },
        )


def _print_reply(payload: dict[str, Any]) -> None:
    reply = payload.get("reply")
    if reply:
        print(f"\nAI > {reply}\n")
        return
    error = payload.get("error")
    if error:
        print(f"\nAI error > {error}\n")


def _handle_approval(
    client: AliceCliClient,
    conversation_id: str,
    payload: dict[str, Any],
) -> None:
    tool_call = payload.get("tool_call") or {}
    name = str(tool_call.get("name") or "unknown")
    description = str(tool_call.get("description") or name)
    arguments = tool_call.get("arguments") or {}

    print(f"\nТребуется подтверждение: {description}")
    print(f"Инструмент: {name}")
    if arguments:
        print(f"Параметры: {arguments}")

    try:
        answer = input("Выполнить? [y/N] ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        answer = ""

    if answer not in {"y", "yes", "д", "да"}:
        print("Действие не выполнено.\n")
        return

    approved = client.execute_approved(conversation_id, tool_call)
    _print_reply(approved)


def main() -> None:
    client = AliceCliClient()
    conversation_id = os.getenv("ALICE_CONVERSATION_ID", "").strip()

    try:
        if not conversation_id:
            conversation_id = client.create_conversation(
                title=os.getenv("ALICE_CLI_TITLE", "Alice Pro CLI").strip() or "Alice Pro CLI"
            )
    except AliceCliError as exc:
        print(f"Alice Pro CLI startup failed: {exc}")
        return

    session_id = os.getenv("ALICE_SESSION_ID", "").strip() or conversation_id
    print(
        "🚀 Alice Pro CLI подключён к каноническому runtime. "
        "Введите запрос (или 'exit' для выхода):\n"
    )
    print(f"Conversation: {conversation_id}\n")

    while True:
        try:
            user_input = input("User > ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not user_input or user_input.lower() in {"exit", "quit"}:
            break

        try:
            payload = client.send_message(
                conversation_id,
                user_input,
                session_id=session_id,
            )
            if payload.get("requires_approval"):
                _handle_approval(client, conversation_id, payload)
            else:
                _print_reply(payload)
        except AliceCliError as exc:
            print(f"\nAlice Pro request failed: {exc}\n")


if __name__ == "__main__":
    main()
