"""Provider-agnostic registry for Alice Pro tools.

Legacy tool definitions remain compatible: parameters/func are still
accepted, while the registry also exposes a normalized Universal Tool
contract for every registered tool.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Mapping, Optional

from universal_tool_platform import UniversalToolDefinition

logger = logging.getLogger("tool_registry")


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, dict[str, Any]] = {}
        self._categories: dict[str, list[str]] = {}
        self._load_all()

    @staticmethod
    def _normalize_cfg(category: str, name: str, cfg: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(cfg)
        result.setdefault("title", name.replace("_", " ").strip().title())
        result.setdefault(
            "inputSchema",
            result.get("input_schema") or result.get("parameters") or {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        )
        result.setdefault("outputSchema", result.get("output_schema") or {"type": "object"})
        result.setdefault("capabilities", list(result.get("capabilities") or [category]))
        result.setdefault("risk_level", str(result.get("risk_level") or "medium"))
        result.setdefault("read_only", bool(result.get("read_only", False)))
        result.setdefault(
            "requires_approval",
            bool(result.get("requires_approval", not result["read_only"])),
        )
        # MCP is the universal external tool surface. Every registered tool
        # must be callable through the MCP adapter; preserve any existing
        # transport declarations while adding MCP rather than requiring every
        # legacy tool implementation to be edited individually.
        transports = list(
            result.get("supported_transports") or ("responses_api", "local_agent")
        )
        if "mcp" not in transports:
            transports.append("mcp")
        result["supported_transports"] = transports
        result.setdefault("executor", dict(result.get("executor") or {"type": "local"}))
        result.setdefault("metadata", dict(result.get("metadata") or {}))
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
        try:
            from git_mcp_tools import GIT_TOOLS
            mcp_read_tools = {"git_status", "git_log", "git_diff", "git_branches"}
            mcp_write_tools = {"git_add", "git_commit", "git_remote", "git_push", "git_pull", "git_fetch"}
            for name, cfg in GIT_TOOLS.items():
                if name in mcp_read_tools:
                    cfg = {
                        **cfg,
                        "capabilities": ["git", "read", "mcp"],
                        "risk_level": "low",
                        "read_only": True,
                        "requires_approval": False,
                        "supported_transports": ["responses_api", "local_agent", "mcp"],
                    }
                elif name in mcp_write_tools:
                    cfg = {
                        **cfg,
                        "capabilities": ["git", "write", "mcp"],
                        "risk_level": "high",
                        "read_only": False,
                        "requires_approval": True,
                        "supported_transports": ["responses_api", "local_agent", "mcp"],
                    }
                self._register("git", name, cfg)
        except Exception as e:
            logger.error(f"[REGISTRY] Ошибка загрузки Git: {e}")

        try:
            from termux_mcp_tools import TERMUX_TOOLS
            for name, cfg in TERMUX_TOOLS.items():
                self._register("termux", name, cfg)
        except Exception as e:
            logger.error(f"[REGISTRY] Ошибка загрузки Termux: {e}")

        try:
            from termux_system_tools import SYSTEM_TOOLS
            for name, cfg in SYSTEM_TOOLS.items():
                self._register("system", name, cfg)
        except Exception as e:
            logger.error(f"[REGISTRY] Ошибка загрузки System: {e}")

        try:
            from filesystem_mcp_tools import FILESYSTEM_TOOLS
            for name, cfg in FILESYSTEM_TOOLS.items():
                self._register("filesystem", name, cfg)
        except Exception as e:
            logger.error(f"[REGISTRY] Ошибка загрузки Filesystem: {e}")

        try:
            from wikipedia_mcp_tools import WIKIPEDIA_TOOLS
            for name, cfg in WIKIPEDIA_TOOLS.items():
                self._register("wikipedia", name, cfg)
        except Exception as e:
            logger.error(f"[REGISTRY] Ошибка загрузки Wikipedia: {e}")

        try:
            from profiler_tools import PROFILER_TOOLS
            for name, cfg in PROFILER_TOOLS.items():
                self._register("profiler", name, cfg)
        except Exception as e:
            logger.error(f"[REGISTRY] Ошибка загрузки Profiler: {e}")

        try:
            from theme_tools import THEME_TOOLS
            for name, cfg in THEME_TOOLS.items():
                self._register("theme", name, cfg)
        except Exception as e:
            logger.error(f"[REGISTRY] Ошибка загрузки Theme: {e}")

        try:
            from runtime_tools import RUNTIME_TOOLS
            for name, cfg in RUNTIME_TOOLS.items():
                self._register("runtime", name, cfg)
        except Exception as e:
            logger.error(f"[REGISTRY] Ошибка загрузки Runtime: {e}")

        try:
            from partner_relations import PARTNER_TOOLS
            for name, cfg in PARTNER_TOOLS.items():
                self._register("partner", name, cfg)
        except Exception as e:
            logger.error(f"[REGISTRY] Ошибка загрузки Partner Relations: {e}")

    @staticmethod
    def _strict_schema(schema: dict) -> dict:
        """Normalize a JSON Schema for strict function calling."""
        if not isinstance(schema, dict):
            return schema

        result = copy.deepcopy(schema)
        schema_type = result.get("type")

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
                if name not in original_required:
                    if isinstance(prop, dict) and "anyOf" not in prop:
                        normalized[name] = {"anyOf": [prop, {"type": "null"}]}
        elif schema_type == "array" and isinstance(result.get("items"), dict):
            result["items"] = ToolRegistry._strict_schema(result["items"])
        elif isinstance(result.get("anyOf"), list):
            result["anyOf"] = [
                ToolRegistry._strict_schema(item) if isinstance(item, dict) else item
                for item in result["anyOf"]
            ]

        return result

    def get_definitions(self, active_categories: set = None) -> list:
        tools_list = []
        for name, cfg in self._tools.items():
            category = next(
                (cat for cat, names in self._categories.items() if name in names),
                "general",
            )
            if active_categories is not None and category not in active_categories:
                continue

            parameters = cfg.get(
                "parameters",
                cfg.get("inputSchema") or {"type": "object", "properties": {}},
            )
            strict_parameters = self._strict_schema(parameters)

            tools_list.append(
                {
                    "type": "function",
                    "name": name,
                    "description": cfg.get("description", ""),
                    "parameters": strict_parameters,
                    "strict": True,
                    "defer_loading": False,
                }
            )
        return tools_list

    def get_tools_by_category(self, category: str) -> list:
        """Return Responses API function definitions for one local category."""
        names = self._categories.get(category, [])
        tools_list = []
        for name in names:
            cfg = self._tools.get(name)
            if not cfg:
                continue

            parameters = cfg.get(
                "parameters",
                cfg.get("inputSchema") or {"type": "object", "properties": {}},
            )
            tools_list.append(
                {
                    "type": "function",
                    "name": name,
                    "description": cfg.get("description", ""),
                    "parameters": self._strict_schema(parameters),
                    "strict": True,
                    "defer_loading": False,
                }
            )
        return tools_list

    def get_universal_definition(self, tool_name: str) -> Optional[UniversalToolDefinition]:
        cfg = self._tools.get(tool_name)
        if cfg is None:
            return None
        return UniversalToolDefinition.from_mapping(tool_name, cfg)

    def get_universal_definitions(self, transport: Optional[str] = None) -> list[dict[str, Any]]:
        definitions = []
        for name in sorted(self._tools):
            definition = self.get_universal_definition(name)
            if definition is None:
                continue
            if transport and transport not in definition.supported_transports:
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
                if s.get("connector_id") in tool_name:
                    cfg = dict(s.get("config", {}) or {})
                    break
        if context:
            cfg["_universal_context"] = dict(context)

        try:
            return func(arguments, cfg)
        except TypeError:
            try:
                return func(arguments)
            except Exception as e:
                if context and "_universal_context" in cfg:
                    raise
                return {"error": str(e)}
        except Exception as e:
            # UniversalToolExecutor must classify real tool exceptions by phase
            # instead of receiving a legacy error mapping that hides the cause.
            if context and "_universal_context" in cfg:
                raise
            logger.exception(f"[REGISTRY] Ошибка выполнения {tool_name}: {e}")
            return {"error": str(e)}

    def get_tool_meta(self, tool_name: str):
        return self._tools.get(tool_name)

    def get_available_categories(self) -> dict:
        return {cat: list(names) for cat, names in self._categories.items()}


registry = ToolRegistry()

__all__ = ["ToolRegistry", "registry"]
