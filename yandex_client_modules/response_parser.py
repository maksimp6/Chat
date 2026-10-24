"""Pure parsing helpers for Yandex Responses API results.

This module intentionally has no HTTP, database, tracing, or MCP side effects.
"""


def extract_reasoning_and_text(data):
    """Return a tuple of (reasoning_text, response_text) from a response."""
    if not isinstance(data, dict):
        return "", str(data or "")

    reasoning_parts = []
    text_parts = []

    for item in data.get("output", []):
        if not isinstance(item, dict):
            continue

        content_list = item.get("content", [])
        if isinstance(content_list, list):
            for part in content_list:
                if isinstance(part, dict):
                    part_type = part.get("type")
                    part_text = part.get("text", "")
                    if part_type == "reasoning_text" and part_text:
                        reasoning_parts.append(str(part_text))
                    elif part_type in ("output_text", "text") and part_text:
                        text_parts.append(str(part_text))
                elif isinstance(part, str):
                    text_parts.append(part)
        elif item.get("type") == "output_text" and item.get("text"):
            text_parts.append(str(item["text"]))

    final_text = "".join(text_parts) or data.get("output_text") or data.get("text") or ""
    final_reasoning = "\n\n".join(reasoning_parts)
    return final_reasoning, str(final_text)


def extract_text(data):
    """Return only the user-facing text from a response."""
    _, text = extract_reasoning_and_text(data)
    return text


def extract_usage(data):
    """Normalize token usage and timing metadata from a response."""
    if not isinstance(data, dict):
        return None

    usage = data.get("usage") or {}
    input_details = usage.get("input_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or {}

    return {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "cached_tokens": input_details.get("cached_tokens", 0),
        "tool_tokens": input_details.get("tool_tokens", 0),
        "reasoning_tokens": output_details.get("reasoning_tokens", 0),
        "created_at": data.get("created_at"),
        "completed_at": data.get("completed_at"),
        "incomplete_details": data.get("incomplete_details"),
    }
