# Vendored from agent_runtime/partial_output.py. Do not edit this copy: change the canonical file
# and run `python3 scripts/check_independence.py --sync-vendored`.
"""Shared extraction of user-visible text from Yandex Responses API output."""

from typing import Any, Optional


# WIKIFUNCTIONS-CANDIDATE WF-10: https://www.wikifunctions.org/wiki/Z872 + Z873 + Z12899.
# Заменить ядро сбора reasoning/output_text на filter/map/join; адаптер сохраняет
# top-level output_text, только dict content parts, финальный strip и fallback при пустом тексте.
def extract_response_text(data: dict[str, Any]) -> tuple[str, str]:
    """Return (reasoning, user-visible text) from one Responses API response."""
    if not isinstance(data, dict):
        return "", ""

    reasoning_parts = []
    text_parts = []
    for item in data.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content", [])
        # An output_text item that also repeats its text in content is counted once.
        item_text = item.get("type") == "output_text" and item.get("text")
        item_parts = [str(item["text"])] if item_text else []
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or not part.get("text"):
                    continue
                part_type = part.get("type")
                if part_type == "reasoning_text":
                    reasoning_parts.append(str(part["text"]))
                elif part_type in ("output_text", "text") and not item_text:
                    item_parts.append(str(part["text"]))
        if item_parts:
            text_parts.append("".join(item_parts))

    # Separate output items, so adjacent messages do not run together.
    text = "\n\n".join(text_parts).strip()
    if not text:
        # Top-level "text" is the text config ({"format": ..., "verbosity": ...}),
        # not an answer: only strings count.
        text = next(
            (v for v in (data.get("output_text"), data.get("text")) if isinstance(v, str) and v),
            "",
        ).strip()
    return "\n\n".join(reasoning_parts), text


# WIKIFUNCTIONS-CANDIDATE WF-14: https://www.wikifunctions.org/wiki/Z18479 + Z872 + Z873.
# Заменить поиск последнего текста на reverse + адаптер ленивого filter/map/first-match;
# обычные W filter/map не обещают lazy/early-stop и без такой адаптации неэквивалентны.
# адаптер не вычисляет ранние ответы после совпадения и возвращает исходную trace entry рядом с текстом.
def extract_last_response_text(responses: list) -> tuple[str, Optional[dict[str, Any]]]:
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
