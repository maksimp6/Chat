def extract_reasoning_and_text(data):
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
                    p_type = part.get("type")
                    p_text = part.get("text", "")

                    if p_type == "reasoning_text" and p_text:
                        reasoning_parts.append(p_text)
                    elif p_type in ("output_text", "text") and p_text:
                        text_parts.append(p_text)
                elif isinstance(part, str):
                    text_parts.append(part)

        elif item.get("type") == "output_text" and item.get("text"):
            text_parts.append(str(item["text"]))

    final_text = (
        "".join(text_parts)
        or data.get("output_text")
        or data.get("text")
        or ""
    )
    final_reasoning = "\n\n".join(reasoning_parts)

    return final_reasoning, str(final_text)


def extract_text(data):
    _, text = extract_reasoning_and_text(data)
    return text


def extract_usage(data):
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
