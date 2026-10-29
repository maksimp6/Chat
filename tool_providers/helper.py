"""Helper model: the model delegates a task to another model (category ``helper``).

``HELPER_TOOLS`` holds one tool, ``helper_delegate``. It runs a full Logos turn of
the chosen text model (``TEXT_MODELS``) through the chat client: the helper gets the
same tool loop, tools and MCP servers as the calling dialog (every category when the
call has no dialog), its own provider conversation, and returns its answer. Passing
the returned ``helper_conversation_id`` back continues the same helper, which keeps
what it was told before. The helper's requests are recorded on the caller's
execution trace.

Tools that need the owner's approval are not run by the helper: they are returned in
``pending_tools``. There is no limit on rounds, tools or nesting (0 means no
application limit); a helper can delegate further.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tool_providers.host import bound
from tool_providers.schema import object_schema

# The dialog settings' own maximum is "high"; "xhigh" is offered where the model has it.
REASONING_EFFORTS = ("high", "xhigh", "medium", "low", "minimal", "none")
HELPER_ROLE = (
    "Ты — модель-помощник Логоса. Основная модель поручила тебе задачу. Выполни её "
    "полностью, используя доступные инструменты, и верни результат так, чтобы "
    "основная модель могла сразу им воспользоваться: что сделано, что найдено, "
    "на чём основано, что не удалось и почему. Ошибки инструментов и провайдера "
    "называй дословно."
)


def _call_of(cfg: Mapping[str, Any] | None) -> Any:
    context = cfg.get("_universal_context") if isinstance(cfg, Mapping) else None
    return context.get("call") if isinstance(context, Mapping) else None


def _trace_of(cfg: Mapping[str, Any] | None) -> Any:
    metadata = getattr(_call_of(cfg), "metadata", None)
    return metadata.get("execution_trace") if isinstance(metadata, Mapping) else None


def _dialog_of(trace: Any) -> str | None:
    """The calling dialog, whose settings, tools and MCP servers the helper uses."""
    data = getattr(trace, "trace", None)
    context = data.get("context") if isinstance(data, Mapping) else None
    conversation = (
        context.get("conversation_id") if isinstance(context, Mapping) else None
    )
    text = str(conversation or "").strip()
    return None if not text or text.startswith("mcp:") else text


def _failure(message: str, phase: str, **extra: Any) -> dict[str, Any]:
    return {"success": False, "error": message, "metadata": {"phase": phase, **extra}}


def _text_or_none(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _pending_tools(response: Mapping[str, Any]) -> list[str | None]:
    """Function calls the turn stopped at: they wait for the owner's approval."""
    output = response.get("output")
    items = output if isinstance(output, list) else []
    return [
        item.get("name")
        for item in items
        if isinstance(item, Mapping) and item.get("type") == "function_call"
    ]


def helper_delegate(
    arguments: dict[str, Any], cfg: dict | None = None
) -> dict[str, Any]:
    """Run the task as a turn of the helper model and return its answer."""
    arguments = arguments if isinstance(arguments, Mapping) else {}
    task = _text_or_none(arguments.get("task"))
    if task is None:
        return _failure("task must be a non-empty string", "validation")
    model = arguments.get("model")
    models = dict(bound.TEXT_MODELS)
    if model not in models:
        return _failure(
            "model must be one of the text models",
            "validation",
            models=sorted(models),
        )
    context = _text_or_none(arguments.get("context"))
    instructions = _text_or_none(arguments.get("instructions"))
    conversation_id = _text_or_none(arguments.get("helper_conversation_id"))
    effort = arguments.get("reasoning_effort") or "high"
    if effort not in REASONING_EFFORTS:
        return _failure("unknown reasoning_effort", "validation")

    trace = _trace_of(cfg)
    dialog = _dialog_of(trace)
    client = bound.LogosClient(bound.Config)
    if conversation_id is None:
        conversation_id = client.create_conversation(trace)["id"]
    message = (
        task
        if context is None
        else f"{task}\n\nКонтекст от основной модели:\n{context}"
    )
    response = client.ask_with_mcp(
        message=message,
        model_key=model,
        conversation_id=conversation_id,
        params={
            "instructions": HELPER_ROLE
            if instructions is None
            else f"{HELPER_ROLE}\n\n{instructions}",
            "reasoning_effort": effort,
        },
        trace=trace,
        settings_conversation_id=dialog,
    )
    reasoning, text = client.extract_reasoning_and_text(response)
    pending = _pending_tools(response)
    return {
        "success": True,
        "model": model,
        "helper_conversation_id": conversation_id,
        "dialog_settings": dialog,
        "response_id": response.get("id"),
        "status": response.get("status"),
        "error": response.get("error"),
        "incomplete_details": response.get("incomplete_details"),
        "answer": text,
        "reasoning": reasoning,
        "pending_tools": pending,
        "usage": client.extract_usage(response),
    }


def _optional(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


HELPER_TOOLS: dict[str, dict[str, Any]] = {
    "helper_delegate": {
        "title": "Helper model: delegate",
        "description": (
            "Delegate a task to a helper model and get its answer. The helper runs a "
            "full Logos turn with the same tools and MCP servers as this dialog, in its "
            "own conversation; pass helper_conversation_id from an earlier result to "
            "continue the same helper. Give the task in full: the helper does not see "
            "this dialog. Use it for parts of the work that can be done separately, in "
            "parallel, or by another model."
        ),
        "parameters": object_schema(
            {
                "task": {
                    "type": "string",
                    "minLength": 1,
                    "description": "What the helper must do, in full",
                },
                "model": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Text model key of the helper, e.g. deepseek-v4.1-flash, "
                        "logos-runtime, yandexgpt-5.1, qwen3.6-35b-a3b"
                    ),
                },
                "context": _optional(
                    {
                        "type": "string",
                        "description": "Facts and material from this dialog the helper needs",
                    }
                ),
                "instructions": _optional(
                    {
                        "type": "string",
                        "description": "Extra instructions added to the helper's role",
                    }
                ),
                "reasoning_effort": _optional(
                    {
                        "type": "string",
                        "enum": list(REASONING_EFFORTS),
                        "description": "Helper's reasoning depth: high when not given",
                    }
                ),
                "helper_conversation_id": _optional(
                    {
                        "type": "string",
                        "description": "Continue this helper (from an earlier result)",
                    }
                ),
            },
            ["task", "model"],
        ),
        "capabilities": ["helper", "delegation", "model"],
        "risk_level": "medium",
        "read_only": False,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": helper_delegate,
    }
}


__all__ = ["HELPER_ROLE", "HELPER_TOOLS", "REASONING_EFFORTS", "helper_delegate"]
