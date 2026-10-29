"""One function per tool category when a request would exceed the provider's tool limit.

Yandex AI Studio refuses a request with more than 100 tools ("The number of tools
exceeds the maximum allowed limit of 100"), deferred tools included (#1703). When
the turn's tools do not fit, each local category is sent as one function
``tools_<category>`` whose description lists every tool of the category by name and
synonyms (not its full description or parameters); the model calls it with ``tool``
and ``arguments`` and the executor runs that tool. Parameters and usage examples are
not inlined — the model looks them up in the index (cache_search / file_search).
Every tool stays available; only the shape of the call changes.
"""

from __future__ import annotations

import json
import re
from typing import Any

# The provider's documented maximum: a platform limit, not an application one.
PROVIDER_TOOL_LIMIT = 100
GROUP_PREFIX = "tools_"

# Short words carry no search signal; dropped when synonyms are mined from a description.
_STOPWORDS = frozenset(
    """the a an and or of to in for on with by from into как что это или и в на для с по из
    инструмент команда функция файл файла файлы когда если без это того которые тот эта этот
    tool command function file files when returns return get set list the its their this that""".split()
)
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")


def group_name(category: str) -> str:
    return GROUP_PREFIX + category


def _synonyms(tool: dict[str, Any]) -> str:
    """Search keywords for a tool: its name parts plus salient words of its description.

    Used instead of the full prose description so the grouped listing stays small while
    the model can still recognise a tool by meaning. The parameter schema and usage
    examples are not inlined — the model looks those up in the index (see group_tool).
    """
    name = str(tool.get("name") or "")
    seen: list[str] = []
    for token in re.split(r"[._]", name):
        token = token.strip().lower()
        if token and token not in seen:
            seen.append(token)
    for word in _WORD.findall(str(tool.get("description") or "")):
        word = word.lower()
        if len(word) >= 3 and word not in _STOPWORDS and word not in seen:
            seen.append(word)
        if len(seen) >= 10:
            break
    return " ".join(seen)


def _tool_line(tool: dict[str, Any]) -> str:
    return f"- {tool['name']} {_synonyms(tool)}"


def group_tool(category: str, tools: list[dict[str, Any]]) -> dict[str, Any]:
    """The single function that stands for every tool of ``category``."""
    names = [tool["name"] for tool in tools]
    description = (
        f"Категория инструментов «{category}». Вызови эту функцию с полем tool — имя "
        "нужного инструмента — и полем arguments — объект его параметров.\n"
        "Ниже только имена и синонимы (для узнавания), без параметров и примеров. "
        "Чтобы узнать параметры и увидеть примеры вызова инструмента, найди их в индексе: "
        "инструментом cache_search (накопленный индекс, туда же попадает архив диалогов) "
        "или встроенным file_search по имени инструмента.\n"
        "Инструменты (имя и синонимы):\n" + "\n".join(_tool_line(tool) for tool in tools)
    )
    grouped = {
        "type": "function",
        "name": group_name(category),
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "enum": names},
                "arguments": {"type": "object"},
            },
            "required": ["tool", "arguments"],
        },
        # arguments is a free-form object, which strict function calling cannot express.
        "strict": False,
    }
    if all(tool.get("defer_loading") is True for tool in tools):
        grouped["defer_loading"] = True
    return grouped


def fit_tools(
    categories: list[tuple[str, list[dict[str, Any]]]], other_count: int
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """The local tools to send, and which tool sits in which group function.

    ``categories`` is (category, its function tools) in display order; ``other_count``
    is the number of hosted and MCP tools of the same request. Within the limit the
    tools go as they are and the map is empty."""
    total = other_count + sum(len(tools) for _, tools in categories)
    if total <= PROVIDER_TOOL_LIMIT:
        return [tool for _, tools in categories for tool in tools], {}
    grouped: list[dict[str, Any]] = []
    membership: dict[str, str] = {}
    for category, tools in categories:
        if not tools:
            continue
        grouped.append(group_tool(category, tools))
        for tool in tools:
            membership[tool["name"]] = group_name(category)
    return grouped, membership


def unwrap_call(name: str, args: Any, category_tools: Any) -> tuple[str, Any]:
    """The real tool and arguments behind a ``tools_<category>`` call.

    ``category_tools(category)`` returns that category's tools. A name that is not
    a group function is returned unchanged. A ``tool`` outside the category raises
    ValueError naming it and the allowed tools, so the model can correct the call."""
    if not name.startswith(GROUP_PREFIX) or not isinstance(args, dict):
        return name, args
    category = name[len(GROUP_PREFIX):]
    members = [entry.get("name") for entry in category_tools(category) or []]
    if not members:
        return name, args
    tool = args.get("tool")
    if not isinstance(tool, str) or tool not in members:
        raise ValueError(
            f"{name}: tool {tool!r} is not in category {category!r}; "
            f"use one of: {', '.join(members)}"
        )
    if "arguments" in args:
        inner = args["arguments"]
        if isinstance(inner, str):
            inner = json.loads(inner) if inner.strip() else {}
        return tool, inner if inner is not None else {}
    # The schema is not strict, so a model may put the parameters beside "tool".
    return tool, {key: value for key, value in args.items() if key != "tool"}
