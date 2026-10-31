"""Extraction of reasoning, text and token usage from Responses API results."""

import math


# WIKIFUNCTIONS-CANDIDATE WF-08: https://www.wikifunctions.org/wiki/Z872 + Z873 + Z12899.
# Заменить чистое ядро отбора/сборки частей на filter/map/join; адаптер сохраняет
# отдельные reasoning/text, строковые content parts, текущие преобразования и fallback output_text/text.
def extract_reasoning_and_text(data):
    if not isinstance(data, dict):
        return "", str(data or "")

    reasoning_parts = []
    text_parts = []

    output_items = data.get("output")
    if not isinstance(output_items, list):
        output_items = []
    for item in output_items:
        if not isinstance(item, dict):
            continue

        item_reasoning = []
        content_list = item.get("content", [])
        if isinstance(content_list, list):
            for part in content_list:
                if isinstance(part, dict):
                    p_type = part.get("type")
                    p_text = part.get("text", "")

                    if p_type == "reasoning_text" and p_text:
                        item_reasoning.append(p_text)
                    elif p_type in ("output_text", "text") and p_text:
                        text_parts.append(p_text)
                elif isinstance(part, str):
                    text_parts.append(part)

        elif item.get("type") == "output_text" and item.get("text"):
            text_parts.append(str(item["text"]))

        # A reasoning item without its full text keeps its summary: the same text a
        # live turn showed while the summary streamed.
        if item.get("type") == "reasoning" and not item_reasoning:
            summary = item.get("summary")
            if isinstance(summary, list):
                item_reasoning.extend(
                    entry["text"]
                    for entry in summary
                    if isinstance(entry, dict)
                    and entry.get("type") == "summary_text"
                    and isinstance(entry.get("text"), str)
                    and entry["text"]
                )
        reasoning_parts.extend(item_reasoning)

    # Top-level "text" in a Responses result is the text config
    # ({"format": ..., "verbosity": ...}), not an answer: only strings count.
    fallback = next(
        (v for v in (data.get("output_text"), data.get("text")) if isinstance(v, str) and v),
        "",
    )
    final_text = "".join(text_parts) or fallback
    # Some models write their reasoning into the answer and close it with </think>,
    # with or without the opening tag: that part is reasoning, not the reply (#1645).
    head, marker, tail = str(final_text).rpartition("</think>")
    if marker:
        thought = head.replace("<think>", "").strip()
        if thought:
            reasoning_parts.append(thought)
        final_text = tail.lstrip()
    final_reasoning = "\n\n".join(reasoning_parts)

    return final_reasoning, str(final_text)


def extract_text(data):
    _, text = extract_reasoning_and_text(data)
    return text


def _token_count(value):
    """A provider token count. Anything else is zero, so a malformed usage block
    cannot break billing arithmetic downstream."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if not math.isfinite(value) or value < 0:
        return 0
    return int(value)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def extract_usage(data):
    if not isinstance(data, dict):
        return None

    usage = _mapping(data.get("usage"))
    input_details = _mapping(usage.get("input_tokens_details"))
    output_details = _mapping(usage.get("output_tokens_details"))

    return {
        "input_tokens": _token_count(usage.get("input_tokens")),
        "output_tokens": _token_count(usage.get("output_tokens")),
        "total_tokens": _token_count(usage.get("total_tokens")),
        "cached_tokens": _token_count(input_details.get("cached_tokens")),
        "tool_tokens": _token_count(input_details.get("tool_tokens")),
        "reasoning_tokens": _token_count(output_details.get("reasoning_tokens")),
        "created_at": data.get("created_at"),
        "completed_at": data.get("completed_at"),
        "incomplete_details": data.get("incomplete_details"),
    }
