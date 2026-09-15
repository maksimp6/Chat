"""Shared extraction of user-visible text from Yandex Responses API output."""
from typing import Any, Dict, Optional, Tuple


def extract_response_text(data: Dict[str, Any]) -> Tuple[str, str]:
    """Return (reasoning, user-visible text) from one Responses API response."""
    if not isinstance(data, dict):
        return "", ""

    reasoning_parts = []
    text_parts = []
    for item in data.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content", [])
        if item.get("type") == "output_text" and item.get("text"):
            text_parts.append(str(item["text"]))
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or not part.get("text"):
                continue
            part_type = part.get("type")
            if part_type == "reasoning_text":
                reasoning_parts.append(str(part["text"]))
            elif part_type in ("output_text", "text"):
                text_parts.append(str(part["text"]))

    text = "".join(text_parts).strip()
    if not text:
        text = str(data.get("output_text") or data.get("text") or "").strip()
    return "\n\n".join(reasoning_parts), text


def extract_last_response_text(responses: list) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Return the last non-empty user-visible response text and its trace entry."""
    if not isinstance(responses, list):
        return "", None
    for response in reversed(responses):
        if not isinstance(response, dict):
            continue
        raw = response.get("raw")
        if not isinstance(raw, dict):
            continue
        _, text = extract_response_text(raw)
        if text:
            return text, response
    return "", None


def format_partial_output_message(partial_output: str, error: str) -> str:
    """Format the persisted/displayed assistant message for a failed request."""
    if partial_output:
        return f"{partial_output}\n\n⚠️ Ошибка: {error}"
    return f"⚠️ Ошибка: {error}"
