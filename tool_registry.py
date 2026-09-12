"""Единый реестр локальных инструментов приложения Alice Pro."""
import logging

logger = logging.getLogger("tool_registry")

class ToolRegistry:
    def __init__(self):
        self._tools = {}
        self._categories = {}
        self._load_all()

    def _register(self, category: str, name: str, cfg: dict):
        self._tools[name] = cfg
        if category not in self._categories:
            self._categories[category] = []
        self._categories[category].append(name)

    def _load_all(self):
        # 1. Git Tools
        try:
            from git_mcp_tools import GIT_TOOLS
            for name, cfg in GIT_TOOLS.items():
                self._register("git", name, cfg)
        except Exception as e:
            logger.error(f"[REGISTRY] Ошибка загрузки Git: {e}")

        # 2. Termux Hardware Tools
        try:
            from termux_mcp_tools import TERMUX_TOOLS
            for name, cfg in TERMUX_TOOLS.items():
                self._register("termux", name, cfg)
        except Exception as e:
            logger.error(f"[REGISTRY] Ошибка загрузки Termux: {e}")

        # 3. System Tools
        try:
            from termux_system_tools import SYSTEM_TOOLS
            for name, cfg in SYSTEM_TOOLS.items():
                self._register("system", name, cfg)
        except Exception as e:
            logger.error(f"[REGISTRY] Ошибка загрузки System: {e}")

        # 4. Filesystem Tools
        try:
            from filesystem_mcp_tools import FILESYSTEM_TOOLS
            for name, cfg in FILESYSTEM_TOOLS.items():
                self._register("filesystem", name, cfg)
        except Exception as e:
            logger.error(f"[REGISTRY] Ошибка загрузки Filesystem: {e}")

        # 5. Wikipedia Tools
        try:
            from wikipedia_mcp_tools import WIKIPEDIA_TOOLS
            for name, cfg in WIKIPEDIA_TOOLS.items():
                self._register("wikipedia", name, cfg)
        except Exception as e:
            logger.error(f"[REGISTRY] Ошибка загрузки Wikipedia: {e}")

        # 6. Profiler Tools
        try:
            from profiler_tools import PROFILER_TOOLS
            for name, cfg in PROFILER_TOOLS.items():
                self._register("profiler", name, cfg)
        except Exception as e:
            logger.error(f"[REGISTRY] Ошибка загрузки Profiler: {e}")

    def get_definitions(self, active_categories: set = None) -> list:
        tools_list = []
        for name, cfg in self._tools.items():
            category = next((cat for cat, names in self._categories.items() if name in names), "general")
            if active_categories is not None and category not in active_categories:
                continue
            tools_list.append({
                "type": "function",
                "name": name,
                "description": cfg.get("description", ""),
                "parameters": cfg.get("parameters", {"type": "object", "properties": {}})
            })
        return tools_list

    def execute(self, tool_name: str, arguments: dict, server_configs: list = None) -> dict:
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
                    cfg = s.get("config", {})
                    break
        try:
            return func(arguments, cfg)
        except TypeError:
            try:
                return func(arguments)
            except Exception as e:
                return {"error": str(e)}
        except Exception as e:
            logger.exception(f"[REGISTRY] Ошибка выполнения {tool_name}: {e}")
            return {"error": str(e)}

    def get_tool_meta(self, tool_name: str):
        return self._tools.get(tool_name)

    def get_available_categories(self) -> dict:
        return {cat: list(names) for cat, names in self._categories.items()}

registry = ToolRegistry()
