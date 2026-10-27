"""Validate and normalize the hosted-tool settings shared by API and client."""

from __future__ import annotations

from typing import Any

from core.wikifunctions import clamp_integer


def file_search_max_results(value: Any) -> int:
    """Keep the provider's 1..50 result range and the existing default of 20."""
    try:
        return clamp_integer(1, int(20 if value is None else value), 50)
    except (TypeError, ValueError, OverflowError):
        return 20


# WIKIFUNCTIONS-CANDIDATE WF-03: https://www.wikifunctions.org/wiki/Z10079 + Z10047.
# Заменить строковую ветку преобразования boolean композицией trim/lowercase;
# адаптер сохраняет bool без преобразования, только true/false и прежний ValueError/path.
def _enabled(value: Any, path: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "false"}:
            return normalized == "true"
    raise ValueError(f"{path} must be a boolean or the string true/false")


# WIKIFUNCTIONS-CANDIDATE WF-04: https://www.wikifunctions.org/wiki/Z10075 + Z25614 + Z19202.
# Перенести разбор списка в replace/split -> map(trim Z10079) -> stable dedup;
# адаптер сохраняет string/list[str], literal \\r\\n/\\n, запятые и ВСЕ разделители splitlines,
# пропуск пустых элементов, порядок и ошибки; простой split("\n") сам по себе неэквивалентен.
def _entries(value: Any, path: str) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        values = value
    else:
        raise ValueError(f"{path} must be a string or a list of strings")

    # Accept text inputs, JSON string arrays and older literal backslash-n separators.
    entries = (
        part.strip()
        for item in values
        for part in item.replace("\\r\\n", "\n").replace("\\n", "\n").replace(",", "\n").splitlines()
    )
    return list(dict.fromkeys(entry for entry in entries if entry))


def normalize_tools_config(raw: Any) -> dict[str, Any]:
    """Reject malformed known tool settings before saving or sending a request.

    Unknown fields are preserved for compatibility. Missing tools retain their
    existing defaults; in particular an absent web_search entry stays opt-out.
    """
    if not isinstance(raw, dict):
        raise ValueError("tools_config must be a JSON object")
    result = dict(raw)
    for name in ("web_search", "file_search", "code_interpreter"):
        if name not in raw:
            continue
        config = raw[name]
        path = f"tools_config.{name}"
        if not isinstance(config, dict):
            raise ValueError(f"{path} must be a JSON object")
        normalized = dict(config)
        if "enabled" in config:
            normalized["enabled"] = _enabled(config["enabled"], f"{path}.enabled")
        if name == "web_search":
            if "context_size" in config and config["context_size"] not in ("low", "medium", "high"):
                raise ValueError(f"{path}.context_size must be low, medium or high")
            for field in ("allowed_domains", "blocked_domains"):
                if field in config:
                    normalized[field] = _entries(config[field], f"{path}.{field}")
        elif name == "file_search":
            if "vector_store_ids" in config:
                normalized["vector_store_ids"] = _entries(
                    config["vector_store_ids"], f"{path}.vector_store_ids"
                )
            if "max_results" in config:
                value = config["max_results"]
                if isinstance(value, bool) or not isinstance(value, (int, str)):
                    raise ValueError(f"{path}.max_results must be an integer")
                try:
                    count = int(value)
                except ValueError:
                    raise ValueError(f"{path}.max_results must be an integer") from None
                normalized["max_results"] = file_search_max_results(count)
        result[name] = normalized
    return result
