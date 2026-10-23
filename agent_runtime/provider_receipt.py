"""Public machine diagnostics, kept separate from private provider error bodies."""

from __future__ import annotations

import math
import re
from typing import Any


def provider_token(value: Any) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", value):
        return None
    if value.startswith(("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_", "sk-")):
        return None
    return value


def provider_echo(response: dict[str, Any]) -> dict[str, Any]:
    """An echoed setting is evidence of the echo, not the provider's internal limit."""
    echo: dict[str, Any] = {}
    model = response.get("model")
    if (
        isinstance(model, str)
        and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,256}", model)
        and not model.startswith(("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_", "sk-"))
    ):
        echo["model"] = model
    for key in ("max_output_tokens", "max_tool_calls"):
        value = response.get(key)
        if type(value) is int and value >= 0:
            echo[key] = value
    temperature = response.get("temperature")
    if (
        isinstance(temperature, (int, float))
        and not isinstance(temperature, bool)
        and math.isfinite(temperature)
        and temperature >= 0
    ):
        echo["temperature"] = temperature
    truncation = provider_token(response.get("truncation"))
    if truncation is not None:
        echo["truncation"] = truncation
    reasoning = response.get("reasoning")
    if isinstance(reasoning, dict):
        effort = provider_token(reasoning.get("effort"))
        if effort is not None:
            echo["reasoning"] = {"effort": effort}
    return echo


def capture_provider_diagnostics(result: dict[str, Any], response: dict[str, Any]) -> None:
    """Replace per-response fields so a prior refusal never contaminates a later ID."""
    error = response.get("error")
    result["provider_error_present"] = error is not None
    error = error if isinstance(error, dict) else {}
    details = response.get("incomplete_details")
    details = details if isinstance(details, dict) else {}
    values = {
        "provider_status": response.get("status"),
        "provider_error_code": error.get("code"),
        "provider_error_type": error.get("type"),
        "provider_reason": details.get("reason") or error.get("reason"),
    }
    for key, value in values.items():
        result.pop(key, None)
        token = provider_token(value)
        if token is not None:
            result[key] = token
    result.setdefault("provider_status", "unknown")
    echo = provider_echo(response)
    result.pop("provider_echo", None)
    if echo:
        result["provider_echo"] = echo
    if "capability_receipt" in result:
        result["capability_receipt"].pop("provider_echo", None)
        if echo:
            result["capability_receipt"]["provider_echo"] = echo


def assistant_text(response: dict[str, Any]) -> tuple[str, str]:
    """Preserve explicit assistant text; exclude tool/user/reasoning diagnostics."""
    parts: list[str] = []
    output = response.get("output")
    for item in output if isinstance(output, list) else []:
        if not isinstance(item, dict) or item.get("role") not in (None, "assistant"):
            continue
        if item.get("type") == "output_text" and isinstance(item.get("text"), str):
            parts.append(item["text"])
        if item.get("type") != "message":
            continue
        content = item.get("content")
        for part in content if isinstance(content, list) else []:
            if isinstance(part, str):
                parts.append(part)
            elif (
                isinstance(part, dict)
                and part.get("type") in {"output_text", "text"}
                and isinstance(part.get("text"), str)
            ):
                parts.append(part["text"])
    if not parts and isinstance(response.get("output_text"), str):
        parts.append(response["output_text"])
    reply = "".join(parts)
    return reply, ("available" if reply else "empty") if parts else "unknown"
