"""Provider-agnostic registry for Logos tools.

One tool, one function (owner's command). The registered entries are *actions*
(``git_status``, ``read_file``, ...), each with its own handler and schema. A
category of actions is one *tool*, named after the category (``git``,
``filesystem``, ...), and that tool has exactly one function: it picks the action
by its ``tool`` field and takes the action's parameters in ``arguments``. A tool
with one action is that action under the tool's name, with the action's own
parameters. The model, the tools panel and the executor see tools; the actions
stay available, every one of them, inside their tool.

Legacy action definitions remain compatible: parameters/func are still accepted,
while the registry also exposes a normalized Universal Tool contract for every
registered action.
"""

from __future__ import annotations

import copy
import functools
import inspect
import json
import logging
import re
import shutil
import threading
from collections.abc import Callable, Mapping
from importlib import import_module
from typing import Any

from tool_providers.host import bound
from tool_providers.universal_tool_platform import (
    UniversalToolDefinition,
    validate_json_schema,
)

logger = logging.getLogger("logos.tool_providers.registry")

_GIT_READ_TOOLS = frozenset({"git_status", "git_log", "git_diff", "git_branches"})
_GIT_WRITE_TOOLS = frozenset(
    {"git_add", "git_commit", "git_remote", "git_push", "git_pull", "git_fetch"}
)

# (category, module, attribute, log label, exception logged instead of raised)
_TOOL_SOURCES: tuple[tuple[str, str, str, str, type[BaseException]], ...] = (
    ("git", "tool_providers.git", "GIT_TOOLS", "Git", Exception),
    ("termux", "tool_providers.termux", "TERMUX_TOOLS", "Termux", Exception),
    ("system", "tool_providers.termux_system", "SYSTEM_TOOLS", "System", Exception),
    (
        "filesystem",
        "tool_providers.filesystem",
        "FILESYSTEM_TOOLS",
        "Filesystem",
        Exception,
    ),
    (
        "wikipedia",
        "tool_providers.wikipedia",
        "WIKIPEDIA_TOOLS",
        "Wikipedia",
        Exception,
    ),
    ("profiler", "tool_providers.profiler", "PROFILER_TOOLS", "Profiler", Exception),
    ("theme", "tool_providers.theme", "THEME_TOOLS", "Theme", Exception),
    ("runtime", "tool_providers.runtime_tools", "RUNTIME_TOOLS", "Runtime", Exception),
)
# Other units' sources (government, partner relations, 3D printing, model editor,
# cloud, browser, root agent) arrive through the external_tool_sources port and load after these.


def _module_tools(module_name: str, attribute: str) -> Mapping[str, Any]:
    tools = getattr(import_module(module_name), attribute, None)
    if tools is None:
        raise ImportError(f"{module_name} does not export {attribute}")
    return tools


def _git_policy(name: str, cfg: Mapping[str, Any]) -> Mapping[str, Any]:
    if name in _GIT_READ_TOOLS:
        return {
            **cfg,
            "capabilities": ["git", "read", "mcp"],
            "risk_level": "low",
            "read_only": True,
            "requires_approval": False,
            "supported_transports": ["responses_api", "local_agent", "mcp"],
        }
    if name in _GIT_WRITE_TOOLS:
        return {
            **cfg,
            "capabilities": ["git", "write", "mcp"],
            "risk_level": "high",
            "read_only": False,
            "requires_approval": True,
            "supported_transports": ["responses_api", "local_agent", "mcp"],
        }
    return cfg


_RISK_ORDER = ("low", "medium", "high", "critical")


def _highest_risk(levels) -> str:
    known = [level for level in levels if level in _RISK_ORDER]
    return max(known, key=_RISK_ORDER.index) if known else "medium"


def _as_object(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value) if value.strip() else {}
    return {} if value is None else value


def _split_call(args: Mapping[str, Any]) -> tuple[Any, Any]:
    """(action, its arguments) from the shapes models actually send (#1703 follow-up).

    The function is not strict, so Qwen3.6 sent, besides the documented
    {tool, arguments}: everything nested one level down ({arguments: {tool,
    arguments}}), the action name inside arguments ({arguments: {tool, query}}),
    "parameters" instead of "arguments", and the parameters beside "tool"."""
    tool = args.get("tool")
    payload_key = (
        "arguments"
        if "arguments" in args
        else "parameters"
        if "parameters" in args
        else None
    )
    payload = _as_object(args[payload_key]) if payload_key else None
    if (
        not isinstance(tool, str)
        and isinstance(payload, Mapping)
        and isinstance(payload.get("tool"), str)
    ):
        # The whole call was put inside "arguments": take it from there.
        return _split_call(payload)
    if payload_key is None:
        return tool, {key: value for key, value in args.items() if key != "tool"}
    return tool, payload


class ToolRegistry:
    def __init__(self):
        # Every registered action by name; a category of actions is one tool.
        self._actions: dict[str, dict[str, Any]] = {}
        self._categories: dict[str, list[str]] = {}
        # Sources that did not load: log label -> exception type.
        self.load_failures: dict[str, str] = {}
        self._load_all()

    @staticmethod
    def _normalize_cfg(
        category: str, name: str, cfg: Mapping[str, Any]
    ) -> dict[str, Any]:
        # Registry presentation/category defaults enrich the shared definition;
        # schema aliases and type/default normalization have a single owner.
        result = dict(cfg)
        result.setdefault("title", name.replace("_", " ").strip().title())
        # An empty or None value takes the default, as a missing key does.
        if not result.get("capabilities"):
            result["capabilities"] = [category]
        if result.get("requires_approval") is None:
            result["requires_approval"] = not bool(result.get("read_only", False))
        normalized = UniversalToolDefinition.from_mapping(name, result).to_mapping()
        normalized.pop("name")
        result.update(normalized)
        # MCP is the universal external tool surface. Every registered tool
        # must be callable through the MCP adapter; preserve any existing
        # transport declarations while adding MCP rather than requiring every
        # legacy tool implementation to be edited individually.
        transports = result["supported_transports"]
        if "mcp" not in transports:
            transports.append("mcp")
        result["supported_transports"] = transports
        return result

    def _register(self, category: str, name: str, cfg: Mapping[str, Any]):
        normalized = self._normalize_cfg(category, name, cfg)
        for previous, names in self._categories.items():
            if name in names:
                names.remove(name)
                logger.warning(
                    "[REGISTRY] Инструмент %s (%s) заменён определением из %s",
                    name,
                    previous,
                    category,
                )
        self._actions[name] = normalized
        self._categories.setdefault(category, []).append(name)

    def register(self, category: str, name: str, cfg: Mapping[str, Any]) -> None:
        """Register or replace one provider-agnostic tool definition."""
        if not category or not name:
            raise ValueError("category and name are required")
        if not isinstance(cfg, Mapping):
            raise TypeError("tool definition must be a mapping")
        self._register(category, name, cfg)

    def _load_all(self):
        own = [
            (
                category,
                functools.partial(_module_tools, module_name, attribute),
                label,
                failure,
            )
            for category, module_name, attribute, label, failure in _TOOL_SOURCES
        ]
        for category, load, label, failure in own + list(bound.external_tool_sources):
            try:
                tools = load()
                for name, cfg in tools.items():
                    if category == "git":
                        cfg = _git_policy(name, cfg)
                    self._register(category, name, cfg)
            except failure as e:
                logger.error(f"[REGISTRY] Ошибка загрузки {label}: {e}")
                self.load_failures[label] = type(e).__name__

    @staticmethod
    def _is_open_object(schema: Any) -> bool:
        """A free-form object (no declared properties) cannot be expressed strictly."""
        return (
            isinstance(schema, dict)
            and schema.get("type") == "object"
            and (
                "properties" not in schema
                or schema.get("additionalProperties") is True
                or isinstance(schema.get("additionalProperties"), dict)
            )
        )

    @staticmethod
    def _has_open_object(schema: Any) -> bool:
        if not isinstance(schema, dict):
            return False
        if ToolRegistry._is_open_object(schema):
            return True
        children = list((schema.get("properties") or {}).values()) + list(
            schema.get("anyOf") or []
        )
        if isinstance(schema.get("items"), dict):
            children.append(schema["items"])
        return any(ToolRegistry._has_open_object(child) for child in children)

    @staticmethod
    def _strict_schema(schema: dict) -> dict:
        """Normalize a JSON Schema for strict function calling.

        Free-form objects stay open: closing them would only admit ``{}``.
        Tools containing one are advertised with ``strict: False``.
        """
        if not isinstance(schema, dict):
            return schema

        result = copy.deepcopy(schema)
        schema_type = result.get("type")

        if ToolRegistry._is_open_object(result):
            return result
        if schema_type == "object" or "properties" in result:
            properties = result.get("properties") or {}
            normalized = {}
            for name, prop in properties.items():
                normalized[name] = ToolRegistry._strict_schema(prop)

            result["properties"] = normalized
            result["required"] = list(normalized.keys())
            result["additionalProperties"] = False

            original_required = set(schema.get("required") or [])
            for name, prop in normalized.items():
                if name not in original_required and validate_json_schema(None, prop):
                    normalized[name] = {"anyOf": [prop, {"type": "null"}]}
        elif schema_type == "array" and isinstance(result.get("items"), dict):
            result["items"] = ToolRegistry._strict_schema(result["items"])
        elif isinstance(result.get("anyOf"), list):
            result["anyOf"] = [
                ToolRegistry._strict_schema(item) if isinstance(item, dict) else item
                for item in result["anyOf"]
            ]

        return result

    @staticmethod
    def is_offered(
        cfg: Mapping[str, Any], transport: str = "responses_api", which=None
    ) -> bool:
        """Offer a tool only when the executor accepts this transport and the host can run it."""
        transports = cfg.get("supported_transports") or ("responses_api", "local_agent")
        if transport not in transports:
            return False
        required = (cfg.get("metadata") or {}).get("requires_executables") or ()
        which = which or shutil.which
        return all(which(str(executable)) for executable in required)

    @staticmethod
    def _which_per_listing():
        """shutil.which for one listing: each executable is looked up once in it.

        Nothing is kept between listings, so an executable installed later is seen.
        """
        return functools.lru_cache(maxsize=None)(shutil.which)

    def _function_definition(self, name: str, cfg: Mapping[str, Any]) -> dict[str, Any]:
        # Advertise the same schema the UniversalToolExecutor validates.
        parameters = UniversalToolDefinition.from_mapping(name, cfg).input_schema
        return {
            "type": "function",
            "name": name,
            "description": cfg.get("description", ""),
            "parameters": self._strict_schema(parameters),
            "strict": not self._has_open_object(parameters),
        }

    # --- tools: one function per category of actions ---

    def _offered_actions(
        self,
        category: str,
        transport: str,
        which,
        allow_action: Callable[[UniversalToolDefinition], bool] | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        offered = []
        for name in self._categories.get(category, []):
            cfg = self._actions.get(name)
            if cfg is None or not self.is_offered(cfg, transport, which):
                continue
            if allow_action is not None and not allow_action(
                UniversalToolDefinition.from_mapping(name, cfg)
            ):
                continue
            offered.append((name, cfg))
        return offered

    def get_actions(
        self,
        category: str,
        *,
        transport: str = "responses_api",
        allow_action: Callable[[UniversalToolDefinition], bool] | None = None,
    ) -> list[str]:
        """The actions of one tool this transport and host can run."""
        which = self._which_per_listing()
        return [
            name for name, _ in self._offered_actions(category, transport, which, allow_action)
        ]

    def get_tool_actions(self) -> dict[str, list[str]]:
        """Every tool with every registered action, whatever the transport."""
        return {category: list(names) for category, names in self._categories.items()}

    def _tool_function(
        self, category: str, actions: list[tuple[str, Mapping[str, Any]]]
    ) -> dict[str, Any]:
        """The one function of a tool: ``tool`` picks the action, ``arguments`` carries its parameters.

        A tool of one action is that action under the tool's name, with the action's
        own (strict) parameters: there is nothing to pick."""
        if len(actions) == 1:
            name, cfg = actions[0]
            function = self._function_definition(name, cfg)
            function["name"] = category
            if name != category:
                function["description"] = (
                    f"Инструмент «{category}», одна функция — действие {name}. "
                    + str(function.get("description") or "")
                )
            return function
        names = [name for name, _ in actions]
        listing = "\n".join(
            f"- {name} — {' '.join(str(cfg.get('description') or '').split())}"
            for name, cfg in actions
        )
        branches = []
        for name, cfg in actions:
            schema = UniversalToolDefinition.from_mapping(name, cfg).input_schema
            branch: dict[str, Any] = {"title": name}
            if isinstance(schema, Mapping):
                branch.update(copy.deepcopy(dict(schema)))
            else:
                branch["type"] = "object"
            branch.setdefault("description", f"Параметры действия {name}")
            branches.append(branch)
        return {
            "type": "function",
            "name": category,
            "description": (
                f"Инструмент «{category}». Одна функция: поле tool выбирает действие, "
                "поле arguments — объект параметров этого действия (его схема стоит "
                "в arguments под именем действия).\nДействия — не функции: функций "
                f"с именами действий нет, вызывайте только функцию «{category}» с "
                "tool=\"<действие>\". Упоминание действия в описаниях, например "
                f"{names[0]}(x=…), означает вызов «{category}» с tool=\"{names[0]}\" "
                "и arguments={\"x\": …}.\nДействия:\n" + listing
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tool": {
                        "type": "string",
                        "enum": names,
                        "description": "Действие инструмента",
                    },
                    "arguments": {
                        "description": "Параметры выбранного действия",
                        "anyOf": branches,
                    },
                },
                "required": ["tool", "arguments"],
                "additionalProperties": False,
            },
            # The arguments differ per action, which strict function calling cannot express.
            "strict": False,
        }

    def get_tools_by_category(
        self,
        category: str,
        *,
        transport: str = "responses_api",
        allow_action: Callable[[UniversalToolDefinition], bool] | None = None,
    ) -> list:
        """The one Responses API function of a tool, or nothing when no action is offered."""
        which = self._which_per_listing()
        actions = self._offered_actions(category, transport, which, allow_action)
        return [self._tool_function(category, actions)] if actions else []

    def get_definitions(self, active_categories: set = None) -> list:
        """One function per tool (category), for the active categories."""
        tools_list = []
        for category in self._categories:
            if active_categories is not None and category not in active_categories:
                continue
            tools_list.extend(self.get_tools_by_category(category))
        return tools_list

    def get_action_definitions(self, category: str) -> list:
        """Per-action function definitions of one tool (diagnostics; never sent to a model)."""
        which = self._which_per_listing()
        return [
            self._function_definition(name, cfg)
            for name, cfg in self._offered_actions(category, "responses_api", which)
        ]

    def get_available_categories(self) -> dict:
        """Every tool with its one function: ``{category: [category]}``."""
        return {category: [category] for category in self._categories}

    def unwrap_call(self, name: str, arguments: Any) -> tuple[str, Any]:
        """(action, its arguments) behind a call of a tool's one function.

        An action name, or a name that is no tool, is returned unchanged. A tool of
        one action takes the action's own arguments, or {tool, arguments} naming it.
        A ``tool`` that is no action of the tool raises ValueError naming the
        allowed actions, so the model can correct the call."""
        actions = self._categories.get(name)
        if not actions or not isinstance(arguments, Mapping):
            return name, arguments
        tool, inner = _split_call(arguments)
        if len(actions) == 1:
            if tool is None:
                # The action's own arguments, as the function advertises them.
                return actions[0], arguments
            if tool in (actions[0], name):
                return actions[0], inner
        if not isinstance(tool, str) or tool not in actions:
            raise ValueError(
                f"{name}: tool {tool!r} is not an action of {name!r}; "
                f"use one of: {', '.join(actions)}"
            )
        return tool, inner

    def _tool_cfg(self, category: str) -> dict[str, Any]:
        """The Universal Tool contract of a tool, summed up from its actions."""
        names = self._categories[category]
        actions = [(name, self._actions[name]) for name in names if name in self._actions]
        function = self._tool_function(category, actions)
        capabilities: list[str] = []
        transports: list[str] = []
        for _, cfg in actions:
            for item in cfg.get("capabilities") or ():
                if item not in capabilities:
                    capabilities.append(item)
            for item in cfg.get("supported_transports") or ():
                if item not in transports:
                    transports.append(item)
        approval_actions = [
            name for name, cfg in actions if cfg.get("requires_approval")
        ]
        for _, cfg in actions:
            for item in (cfg.get("metadata") or {}).get("approval_actions") or ():
                if item not in approval_actions:
                    approval_actions.append(item)
        return {
            "title": category,
            "description": function["description"],
            "inputSchema": function["parameters"],
            "capabilities": capabilities or [category],
            "risk_level": _highest_risk(str(cfg.get("risk_level")) for _, cfg in actions),
            "read_only": bool(actions) and all(cfg.get("read_only") for _, cfg in actions),
            "requires_approval": any(cfg.get("requires_approval") for _, cfg in actions),
            "supported_transports": transports or ["responses_api", "local_agent"],
            "executor": {"type": "local"},
            "metadata": {"actions": list(names), "approval_actions": approval_actions},
        }

    def get_universal_definition(
        self, tool_name: str
    ) -> UniversalToolDefinition | None:
        cfg = self._actions.get(tool_name)
        if cfg is None and tool_name in self._categories:
            cfg = self._tool_cfg(tool_name)
        if cfg is None:
            return None
        return UniversalToolDefinition.from_mapping(tool_name, cfg)

    def get_universal_definitions(
        self, transport: str | None = None
    ) -> list[dict[str, Any]]:
        definitions = []
        which = self._which_per_listing()
        for name in sorted(self._actions):
            definition = self.get_universal_definition(name)
            if definition is None:
                continue
            if transport and not self.is_offered(self._actions[name], transport, which):
                continue
            definitions.append(definition.to_mapping())
        return definitions

    def execute(
        self,
        tool_name: str,
        arguments: dict,
        server_configs: list = None,
        context: Mapping[str, Any] | None = None,
    ) -> dict:
        if tool_name not in self._actions and tool_name in self._categories:
            # A tool's one function: run the action its arguments name.
            action, inner = self.unwrap_call(tool_name, arguments)
            if action == tool_name:
                return {"error": f"Инструмент '{tool_name}' не назвал действие"}
            return self.execute(
                action, inner, server_configs=server_configs, context=context
            )
        if tool_name not in self._actions:
            return {"error": f"Инструмент '{tool_name}' не найден"}
        tool_cfg = self._actions[tool_name]
        func = tool_cfg.get("func")
        if not func:
            return {"error": f"Функция для '{tool_name}' не определена"}

        cfg = {}
        if server_configs:
            for s in server_configs:
                connector_id = str(s.get("connector_id") or "")
                # Whole-word match: connector "git" must not configure "digit_tool".
                if connector_id and re.search(
                    rf"(?:^|[._-]){re.escape(connector_id)}(?:$|[._-])", tool_name
                ):
                    cfg = dict(s.get("config", {}) or {})
                    break
        if context:
            cfg["_universal_context"] = dict(context)

        try:
            signature = inspect.signature(func)
            try:
                signature.bind(arguments, cfg)
            except TypeError:
                signature.bind(arguments)
                return func(arguments)
            return func(arguments, cfg)
        except Exception as e:
            # UniversalToolExecutor must classify real tool exceptions by phase
            # instead of receiving a legacy error mapping that hides the cause.
            if context and "_universal_context" in cfg:
                raise
            logger.exception(f"[REGISTRY] Ошибка выполнения {tool_name}: {e}")
            return {"error": "Ошибка выполнения инструмента"}

    def get_tool_meta(self, tool_name: str):
        # A copy, so a caller cannot change the registry; functions are shared.
        cfg = self._actions.get(tool_name)
        if cfg is None:
            return None
        return {
            key: value if callable(value) else copy.deepcopy(value)
            for key, value in cfg.items()
        }


_default_registry = None
_default_registry_lock = threading.RLock()


def default_registry() -> ToolRegistry:
    """The process tool registry, created once on first use."""
    global _default_registry
    with _default_registry_lock:
        if _default_registry is None:
            _default_registry = ToolRegistry()
        return _default_registry


def __getattr__(name: str) -> Any:
    """``registry`` is the process registry, created on first use."""
    if name == "registry":
        return default_registry()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# "registry" resolves through __getattr__ above.
__all__ = ["ToolRegistry", "default_registry", "registry"]  # noqa: F822
