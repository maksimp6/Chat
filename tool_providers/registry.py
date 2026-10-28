"""Provider-agnostic registry for Logos tools.

Legacy tool definitions remain compatible: parameters/func are still
accepted, while the registry also exposes a normalized Universal Tool
contract for every registered tool.
"""

from __future__ import annotations

import copy
import inspect
import logging
import re
import shutil
from importlib import import_module
from typing import Any, Mapping, Optional

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
    (
        "government",
        "organization.government",
        "GOVERNMENT_TOOLS",
        "Government",
        Exception,
    ),
    (
        "partner",
        "organization.partner_relations",
        "PARTNER_TOOLS",
        "Partner Relations",
        Exception,
    ),
    ("3d", "forge.tools", "PRINTING3D_TOOLS", "3D Printing", Exception),
    ("3d", "model_editor.tools", "MODEL_EDITOR_TOOLS", "Model Editor", ImportError),
    ("cloud", "cloud.tools", "CLOUD_TOOLS", "Cloud tools", Exception),
)


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


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, dict[str, Any]] = {}
        self._categories: dict[str, list[str]] = {}
        self._load_all()

    @staticmethod
    def _normalize_cfg(
        category: str, name: str, cfg: Mapping[str, Any]
    ) -> dict[str, Any]:
        # Registry presentation/category defaults enrich the shared definition;
        # schema aliases and type/default normalization have a single owner.
        result = dict(cfg)
        result.setdefault("title", name.replace("_", " ").strip().title())
        result.setdefault("capabilities", [category])
        result.setdefault("requires_approval", not bool(result.get("read_only", False)))
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
        for names in self._categories.values():
            if name in names:
                names.remove(name)
        self._tools[name] = normalized
        self._categories.setdefault(category, []).append(name)

    def register(self, category: str, name: str, cfg: Mapping[str, Any]) -> None:
        """Register or replace one provider-agnostic tool definition."""
        if not category or not name:
            raise ValueError("category and name are required")
        if not isinstance(cfg, Mapping):
            raise TypeError("tool definition must be a mapping")
        self._register(category, name, cfg)

    def _load_all(self):
        for category, module_name, attribute, label, failure in _TOOL_SOURCES:
            try:
                tools = getattr(import_module(module_name), attribute, None)
                if tools is None:
                    raise ImportError(f"{module_name} does not export {attribute}")
                for name, cfg in tools.items():
                    if category == "git":
                        cfg = _git_policy(name, cfg)
                    self._register(category, name, cfg)
            except failure as e:
                logger.error(f"[REGISTRY] Ошибка загрузки {label}: {e}")

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
    def is_offered(cfg: Mapping[str, Any], transport: str = "responses_api") -> bool:
        """Offer a tool only when the executor accepts this transport and the host can run it."""
        transports = cfg.get("supported_transports") or ("responses_api", "local_agent")
        if transport not in transports:
            return False
        required = (cfg.get("metadata") or {}).get("requires_executables") or ()
        return all(shutil.which(str(executable)) for executable in required)

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

    def get_definitions(self, active_categories: set = None) -> list:
        tools_list = []
        for name, cfg in self._tools.items():
            category = next(
                (cat for cat, names in self._categories.items() if name in names),
                "general",
            )
            if active_categories is not None and category not in active_categories:
                continue
            if self.is_offered(cfg):
                tools_list.append(self._function_definition(name, cfg))
        return tools_list

    def get_tools_by_category(self, category: str) -> list:
        """Return Responses API function definitions for one local category."""
        names = self._categories.get(category, [])
        tools_list = []
        for name in names:
            cfg = self._tools.get(name)
            if cfg and self.is_offered(cfg):
                tools_list.append(self._function_definition(name, cfg))
        return tools_list

    def get_universal_definition(
        self, tool_name: str
    ) -> Optional[UniversalToolDefinition]:
        cfg = self._tools.get(tool_name)
        if cfg is None:
            return None
        return UniversalToolDefinition.from_mapping(tool_name, cfg)

    def get_universal_definitions(
        self, transport: Optional[str] = None
    ) -> list[dict[str, Any]]:
        definitions = []
        for name in sorted(self._tools):
            definition = self.get_universal_definition(name)
            if definition is None:
                continue
            if transport and not self.is_offered(self._tools[name], transport):
                continue
            definitions.append(definition.to_mapping())
        return definitions

    def execute(
        self,
        tool_name: str,
        arguments: dict,
        server_configs: list = None,
        context: Optional[Mapping[str, Any]] = None,
    ) -> dict:
        if tool_name not in self._tools:
            return {"error": f"Инструмент '{tool_name}' не найден"}
        tool_cfg = self._tools[tool_name]
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
        return self._tools.get(tool_name)

    def get_available_categories(self) -> dict:
        return {cat: list(names) for cat, names in self._categories.items()}


registry = ToolRegistry()

__all__ = ["ToolRegistry", "registry"]
