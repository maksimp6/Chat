"""Validate and normalize the hosted-tool settings shared by API and client."""

from __future__ import annotations

import os
from typing import Any

from yandex_client_modules._vendor.wikifunctions import clamp_integer

# The provider returns at most 50 File Search results; settings stay at that maximum.
FILE_SEARCH_MAX_RESULTS = 50

# The owner's knowledge store: the chat's File Search, the repository RAG
# (scripts/repository_rag.py, scripts/logos_knowledge.py) and the dialog-archive
# ingest (.github/workflows/redmi9-rag-ingest.yml) all use this one store.
DEFAULT_VECTOR_STORE_IDS = ("fvtvp6t2su0g5q4ft2ev",)

# Dialog settings saved before this revision carry the old medium/off defaults; they
# are read as the maximal ones. Later explicit choices carry the revision and stay.
# Revision 2 raised every setting to its maximum; revision 3 turns live answers on
# for a record that has no stream key yet. A stream key already stored is kept.
# Revision 4 adds the cache category to a stored tool selection; revision 5 added a
# helper category that no longer exists, and revision 6 takes it out again.
SETTINGS_REVISION = 6


def file_search_max_results(value: Any) -> int:
    """Keep the provider's 1..50 result range; no value means the maximum."""
    try:
        return clamp_integer(
            1,
            int(FILE_SEARCH_MAX_RESULTS if value is None else value),
            FILE_SEARCH_MAX_RESULTS,
        )
    except (TypeError, ValueError, OverflowError):
        return FILE_SEARCH_MAX_RESULTS


def knowledge_vector_store_ids() -> list[str]:
    """The stores every dialog searches: LOGOS_VECTOR_STORE_IDS, else the owner's store."""
    configured = [
        part.strip()
        for part in os.environ.get("LOGOS_VECTOR_STORE_IDS", "").split(",")
        if part.strip()
    ]
    return configured or list(DEFAULT_VECTOR_STORE_IDS)


def upgrade_dialog_settings(settings: Any) -> Any:
    """Read settings saved before SETTINGS_REVISION at the maximal values.

    The upgrade runs revision by revision; a missing or non-integer revision is 0.
    Before revision 2, records hold the former defaults (medium reasoning and
    verbosity, medium search context, 20 File Search results, Code Interpreter off)
    that nobody chose: every hosted tool is switched on and every capacity is raised
    to its maximum. Before revision 3, live (streamed) answers are switched on only when
    the record has no stream key: a stream value already stored, true or false, is kept.
    Before revision 4, a stored tool selection gets the cache category; before
    revision 6, the removed helper category is taken out of it.
    The record is stamped with the revision, so choices made after it are kept; a record
    already at revision 2 keeps its tool selection and later choices.
    """
    if not isinstance(settings, dict):
        return settings
    revision = settings.get("settings_revision")
    if not isinstance(revision, int) or isinstance(revision, bool):
        revision = 0
    if revision >= SETTINGS_REVISION:
        return settings
    upgraded = dict(settings)
    if revision < 2:
        # An old tool selection would hide every category added since: all are on again.
        upgraded.pop("active_tool_categories", None)
        if upgraded.get("reasoning_effort") in (None, "low", "medium"):
            upgraded["reasoning_effort"] = "high"
        if upgraded.get("text_verbosity") in (None, "low", "medium", "concise", "verbose"):
            upgraded["text_verbosity"] = "high"
        if upgraded.get("truncation") == "auto":
            upgraded["truncation"] = "disabled"
        if "max_output_tokens" in upgraded:
            upgraded["max_output_tokens"] = 0
        if "max_tool_calls" in upgraded:
            upgraded["max_tool_calls"] = None
        if "parallel_tool_calls" in upgraded:
            upgraded["parallel_tool_calls"] = True
        tools = upgraded.get("tools_config")
        tools = dict(tools) if isinstance(tools, dict) else {}
        web = dict(tools.get("web_search") or {})
        web["enabled"] = True
        web["context_size"] = "high"
        tools["web_search"] = web
        tools["code_interpreter"] = {**dict(tools.get("code_interpreter") or {}), "enabled": True}
        files = dict(tools.get("file_search") or {})
        files["enabled"] = True
        files["max_results"] = FILE_SEARCH_MAX_RESULTS
        tools["file_search"] = files
        upgraded["tools_config"] = tools
    # Only a missing key takes the new default: a stored stream value is a choice and stays.
    if revision < 3 and "stream" not in upgraded:
        upgraded["stream"] = True
    # A selection saved before the cache category existed could not have chosen it.
    categories = upgraded.get("active_tool_categories")
    if revision < 4 and isinstance(categories, list) and "cache" not in categories:
        categories = upgraded["active_tool_categories"] = [*categories, "cache"]
    if revision < 6 and isinstance(categories, list) and "helper" in categories:
        # The helper became the cache search; a stored "helper" names no category.
        upgraded["active_tool_categories"] = [
            category for category in categories if category != "helper"
        ]
    upgraded["settings_revision"] = SETTINGS_REVISION
    return upgraded


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
        for part in item.replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace(",", "\n")
        .splitlines()
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
            if "context_size" in config and config["context_size"] not in (
                "low",
                "medium",
                "high",
            ):
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
    if "defer_loading" in raw:
        config = raw["defer_loading"]
        path = "tools_config.defer_loading"
        if not isinstance(config, dict):
            raise ValueError(f"{path} must be a JSON object")
        normalized = dict(config)
        if "enabled" in config:
            normalized["enabled"] = _enabled(config["enabled"], f"{path}.enabled")
        if "eager_categories" in config:
            normalized["eager_categories"] = _entries(
                config["eager_categories"], f"{path}.eager_categories"
            )
        result["defer_loading"] = normalized
    return result


def deferred_tool_categories(tools_config: Any) -> tuple[bool, set[str]]:
    """(on, eager categories) of the dialog's defer_loading setting.

    When on, the application's function tools go to the provider with
    ``defer_loading: true`` except those of the eager categories: the provider
    shows a deferred tool to the model only after its tool search finds it.
    Off (the setting absent) every tool is sent loaded, as before the setting.
    """
    config = tools_config.get("defer_loading") if isinstance(tools_config, dict) else None
    if not isinstance(config, dict) or config.get("enabled") is not True:
        return False, set()
    eager = config.get("eager_categories")
    return True, set(eager) if isinstance(eager, list) else set()
