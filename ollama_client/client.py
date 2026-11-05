"""The chat client of Ollama: the turn requests of a dialog served by a local model.

The chat's tool loop (chat/mcp_routes.py, LogosClient) sits on top of this class as it
sits on top of the Yandex client: it calls ``ask_with_mcp``, ``ask`` and the
``extract_*`` readers. Nothing here comes from the Yandex client: Ollama has its own
settings (ollama_client.settings), instructions and tool list.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from ollama_client import settings
from ollama_client.chat import OllamaError, ask as ollama_ask

__all__ = ["OllamaClient", "OllamaClientError"]

logger = logging.getLogger("logos.ollama_client")


class OllamaClientError(Exception):
    """A turn request Ollama did not answer, with the text the person reads."""

    def __init__(self, message: str, *, status_code: int | None, reached: bool):
        super().__init__(message)
        self.status_code = status_code
        self.user_text = settings.USER_TEXT_UNREACHABLE
        # The chat retries a request that never reached the provider.
        self.request_not_accepted = not reached


def _emit(sink: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]) -> None:
    """Deliver one live event; a failing receiver never breaks the request."""
    if sink is None:
        return
    try:
        sink(event)
    except Exception:
        logger.exception("Live event %s was not delivered", event.get("kind"))


def _traced(trace: Any) -> bool:
    return hasattr(trace, "add_api_request") and hasattr(trace, "add_event")


def response_input(params: dict[str, Any], message: Any) -> list[dict[str, Any]]:
    """The turn's input items: the caller's own, or the user's message."""
    if "input" in params:
        return params["input"]
    return [{"role": "user", "content": message}]


def _timeout(params: dict[str, Any]) -> float | None:
    """The wait between two streamed pieces; none when the turn sets no limit."""
    value = params.get("poll_timeout")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


class OllamaClient:
    """Turn requests of the models in ``settings.MODELS``; ``registry`` gives the tools."""

    def __init__(self, registry: Any = None):
        self.registry = registry

    def ask(
        self,
        message: Any,
        model_key: Any,
        conversation_id: Any = None,
        params: Any = None,
        execution_trace: Any = None,
        trace_step: Any = None,
    ) -> dict[str, Any]:
        """One /api/chat request; Ollama keeps no conversation, so ``conversation_id``
        is unused: the caller sends the whole dialog in ``params["input"]``."""
        params = dict(params or {})
        info = settings.MODELS.get(model_key)
        if info is None:
            raise ValueError(f"{model_key!r} is not an Ollama model")
        trace = execution_trace if _traced(execution_trace) else None
        step = trace_step
        if trace is not None and step is None:
            step = len(trace.trace.get("api_requests", [])) + 1
        url = settings.URL
        started = time.time()
        perf = time.perf_counter()
        if trace is not None:
            trace.add_event("api_request_sent", {"method": "POST", "url": url, "step": step})
        sink = params.get("on_stream_event")
        if not callable(sink):
            sink = None
        live = None
        if sink is not None:
            _emit(sink, {"kind": "response_status", "status": "in_progress", "response_id": None})
            # One reasoning part per request: the live view joins its pieces without breaks.
            part = f"ollama-{step or 0}-{started}"

            def live(event: dict[str, Any]) -> None:
                kind = event.get("kind")
                if kind == "reasoning_delta":
                    _emit(sink, {"kind": "reasoning_delta", "text": event["text"], "summary": False, "part": part})
                elif kind == "text_delta":
                    _emit(sink, {"kind": "text_delta", "text": event["text"]})
                elif kind == "tool_call_requested":
                    # A tool's one function names its action in "tool": show the action.
                    arguments = event.get("arguments")
                    name = event.get("name")
                    if isinstance(arguments, dict) and isinstance(arguments.get("tool"), str):
                        name = arguments["tool"]
                    _emit(
                        sink,
                        {"kind": "tool_progress", "tool": "function", "phase": "requested", "name": name, "server_label": None},
                    )

        try:
            body, result = ollama_ask(
                url,
                info["upstream"],
                model_key,
                response_input(params, message),
                params,
                timeout=_timeout(params),
                on_event=live,
            )
        except OllamaError as exc:
            raise OllamaClientError(str(exc), status_code=exc.status_code, reached=exc.reached) from exc
        if trace is not None:
            ended = time.time()
            trace.add_api_request(body, step_index=step, start_timestamp=started, provider_key_id=None)
            trace.add_response(
                result,
                step_index=step,
                start_timestamp=started,
                end_timestamp=ended,
                timing_ms=round((time.perf_counter() - perf) * 1000, 2),
                kind="initial_response",
                provider_key_id=None,
            )
            trace.add_event(
                "api_request_completed",
                {"method": "POST", "url": url, "step": step, "response_id": result["id"], "status": result["status"]},
            )
        on_provider_response = params.get("on_provider_response")
        if callable(on_provider_response):
            on_provider_response(result)
        _emit(sink, {"kind": "response_status", "status": result["status"], "response_id": result["id"]})
        return result

    def tools(self, execution_policy: Any = None) -> list[dict[str, Any]]:
        """The functions of Ollama's tool categories (one function per category)."""
        if self.registry is None:
            return []
        allow_action = None if execution_policy is None else execution_policy.allows_tool
        functions: list[dict[str, Any]] = []
        for category in settings.TOOLS:
            if self.registry.get_actions(category, allow_action=allow_action):
                functions.extend(self.registry.get_tools_by_category(category, allow_action=allow_action))
        return functions

    def ask_with_mcp(
        self,
        message: Any,
        model_key: Any,
        conversation_id: Any = None,
        params: Any = None,
        trace: Any = None,
        *,
        execution_policy: Any = None,
        settings_conversation_id: Any = None,
    ) -> dict[str, Any]:
        """A turn request with Ollama's instructions and tools."""
        ask_params = dict(params or {})
        # The server owns the tool list: a caller's "tools" never reaches the model.
        ask_params["tools"] = self.tools(execution_policy)
        settings_id = settings_conversation_id or conversation_id
        if settings_id and execution_policy is None:
            ask_params["instructions"] = settings.INSTRUCTIONS
            # The chat's own id: tools that take a conversation_id need it.
            ask_params["input"] = [
                {
                    "role": "system",
                    "content": f"[ТЕКУЩИЙ ДИАЛОГ]\nconversation_id этого диалога Logos: {settings_id}",
                },
                *response_input(ask_params, message),
            ]
        if _traced(trace):
            trace.add_event("ollama_tools", {"tools": [tool.get("name") for tool in ask_params["tools"]]})
        return self.ask(message, model_key, conversation_id, ask_params, execution_trace=trace)

    @staticmethod
    def extract_reasoning_and_text(data: Any) -> tuple[str, str]:
        """(thinking, answer) of an Ollama answer in the Responses shape."""
        if not isinstance(data, dict):
            return "", str(data or "")
        reasoning: list[str] = []
        text: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "reasoning":
                reasoning.extend(
                    entry["text"]
                    for entry in item.get("summary") or []
                    if isinstance(entry, dict) and isinstance(entry.get("text"), str) and entry["text"]
                )
            elif item.get("type") == "message":
                text.extend(
                    part["text"]
                    for part in item.get("content") or []
                    if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str)
                )
        return "\n\n".join(reasoning), "".join(text)

    @classmethod
    def extract_text(cls, data: Any) -> str:
        return cls.extract_reasoning_and_text(data)[1]

    @staticmethod
    def extract_usage(data: Any) -> dict[str, Any] | None:
        if not isinstance(data, dict):
            return None
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}

        def count(name: str) -> int:
            value = usage.get(name)
            return int(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

        return {
            "input_tokens": count("input_tokens"),
            "output_tokens": count("output_tokens"),
            "total_tokens": count("total_tokens"),
            "cached_tokens": 0,
            "tool_tokens": 0,
            "reasoning_tokens": 0,
            "created_at": data.get("created_at"),
            "completed_at": data.get("completed_at"),
            "incomplete_details": data.get("incomplete_details"),
        }
