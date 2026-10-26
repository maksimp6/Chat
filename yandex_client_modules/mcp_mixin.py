import json
import time
from typing import Any

from economy.treasury_identity import get_current_owner_id
from observability.trace_manager import ExecutionTrace
from tool_providers.registry import registry
import mcp_server.storage as mcp_storage
from storage.db import get_conv_settings

from yandex_client_modules.request_utils import sanitize_for_log as _sanitize_for_log
from yandex_client_modules.api_logger import api_logger
from yandex_client_modules.tool_config import file_search_max_results, normalize_tools_config
from tool_providers.universal_tool_platform import (
    ReadOnlyToolPolicy,
    UniversalToolCall,
    UniversalToolExecutor,
)


def _max_results(value: Any) -> int:
    """File-search result count from settings, clamped to the provider's range."""
    return file_search_max_results(value)


class YandexMcpMixin:
    def _execute_single_tool(self, tc, all_servers, trace=None, *, execution_policy=None):
        import time as _t

        function = tc.get("function")
        if not isinstance(function, dict):
            function = {}
        name = tc.get("name") or function.get("name")
        if name and "<|" in name:
            name = name.split("<|")[0].strip()

        args = tc["arguments"] if "arguments" in tc else function.get("arguments", {})

        call_id = tc.get("call_id") or tc.get("id") or tc.get("tool_call_id") or name

        start_timestamp = _t.time()
        t_start = _t.perf_counter()

        trace_id = trace.trace_id if isinstance(trace, ExecutionTrace) else None
        user_id = None
        invocation_id = None
        if isinstance(trace, ExecutionTrace):
            user_id = (trace.trace.get("context") or {}).get("user_id")
            invocation_id = (trace.trace.get("context") or {}).get("invocation_id")

        try:
            try:
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        raise ValueError("Invalid tool arguments: expected a valid JSON object") from None
                if not isinstance(args, dict):
                    raise ValueError("Invalid tool arguments: expected a JSON object")
            except ValueError as exc:
                if isinstance(trace, ExecutionTrace):
                    trace.add_event(
                        "tool_arguments_rejected",
                        {"tool": name, "call_id": call_id, "error": str(exc)},
                    )
                raise

            call = UniversalToolCall(
                tool_name=name,
                arguments=args,
                call_id=call_id,
                transport="responses_api",
                trace_id=trace_id,
                invocation_id=invocation_id,
                user_id=user_id,
                metadata={"source": "yandex_responses", "server_count": len(all_servers or [])},
            )
            executor = UniversalToolExecutor(registry)
            if isinstance(trace, ExecutionTrace):
                universal_result = executor.execute_with_trace(call, trace, policy=execution_policy)
            else:
                universal_result = executor.execute(call, policy=execution_policy)

            if universal_result.get("success"):
                result = universal_result.get("data")
                error = None
            else:
                result = None
                error = universal_result.get("error") or "Tool execution failed"
            api_logger.debug(
                "[LOCAL TOOL RESULT] name=%s call_id=%s\\n%s",
                name,
                call_id,
                json.dumps(_sanitize_for_log(result), ensure_ascii=False, indent=2)
                if isinstance(result, (dict, list))
                else str(result),
            )

        except Exception as exc:
            result = None
            error = str(exc)

            api_logger.exception("[LOCAL TOOL ERROR] name=%s call_id=%s", name, call_id)

        t_end = _t.perf_counter()
        end_timestamp = _t.time()

        duration_ms = round((t_end - t_start) * 1000, 2)

        content_str = (
            json.dumps(result, ensure_ascii=False)
            if isinstance(result, (dict, list))
            else str(result)
            if result is not None
            else ""
        )

        timing = {
            "name": f"Tool: {name}",
            "duration_ms": duration_ms,
            "server_type": "local",
            "server_label": "Local Registry",
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            "success": error is None,
        }

        return {
            "call_id": call_id,
            "name": name,
            "content": content_str,
            "result": result,
            "error": error,
            "timing": timing,
        }

    def ask_with_mcp(
        self,
        message: Any,
        model_key: Any,
        conversation_id: Any = None,
        params: Any = None,
        trace: Any = None,
        *,
        execution_policy: ReadOnlyToolPolicy | None = None,
        settings_conversation_id: Any = None,
    ) -> Any:
        params = params or {}
        import time as _t

        mcp_tools = []
        # The chat's own id keys its settings and enabled servers. The provider conversation is
        # only the thread id, and it differs from the chat's id for every new chat.
        settings_id = settings_conversation_id or conversation_id
        # Shared conversations use the installation's MCP configuration, including
        # conversations created before ownership metadata existed.
        owner_id = get_current_owner_id()
        enabled_servers = []
        if settings_id:
            try:
                enabled_servers = mcp_storage.get_enabled_servers_for_conv(
                    settings_id, owner_id=owner_id
                )
            except Exception:
                enabled_servers = []

        for s in enabled_servers if execution_policy is None else []:
            if s.get("server_url") or (s.get("connector_id") or "").startswith("connector_"):
                mcp_tools.append(
                    {
                        "type": "mcp",
                        "server_label": s.get("server_label") or s.get("name"),
                        "server_url": s.get("server_url"),
                        "connector_id": s.get("connector_id"),
                        "authorization": s.get("authorization"),
                        "require_approval": "never",
                    }
                )

        active_cats = None
        if settings_id:
            try:
                preferences = get_conv_settings(settings_id) or {}
                active_cats = preferences.get("active_tool_categories")
            except Exception:
                active_cats = None

        if active_cats is None:
            active_cats = params.get("active_tool_categories")
        if not isinstance(active_cats, list):
            active_cats = None
        if active_cats is None:
            # Same default as the tools panel: every registered category. The
            # registry offers only tools this transport and host can execute.
            active_cats = list(registry.get_available_categories())

        hosted_tools = []
        try:
            conv_settings = get_conv_settings(settings_id) or {} if settings_id else {}
        except Exception:
            conv_settings = {}
        if not isinstance(conv_settings, dict):
            raise ValueError("Conversation settings must be a JSON object")
        raw_tools_config = conv_settings.get("tools_config", params.get("tools_config", {}))
        tools_config = normalize_tools_config(raw_tools_config)

        raw_web_cfg = tools_config.get("web_search")
        web_cfg = raw_web_cfg or {}
        web_enabled = True if raw_web_cfg is None else web_cfg.get("enabled", False)
        if web_enabled:
            web_tool = {
                "type": "web_search",
                "search_context_size": web_cfg.get("context_size") or "medium",
            }
            allowed_domains = list(web_cfg.get("allowed_domains", []))
            blocked_domains = list(web_cfg.get("blocked_domains", []))
            # A project-wide exclusion is not removable through conversation settings.
            required_blocks = tuple(getattr(self, "mandatory_blocked_web_domains", ()))
            if required_blocks:
                normalized_blocks = {domain.casefold().rstrip(".") for domain in required_blocks}
                allowed_domains = [
                    domain
                    for domain in allowed_domains
                    if not any(
                        domain.casefold().removeprefix("*.").rstrip(".") == block
                        or domain.casefold().removeprefix("*.").rstrip(".").endswith("." + block)
                        for block in normalized_blocks
                    )
                ]
                seen_blocks = {domain.casefold().rstrip(".") for domain in blocked_domains}
                for domain in required_blocks:
                    if domain not in seen_blocks:
                        blocked_domains.append(domain)
                        seen_blocks.add(domain)
            if allowed_domains or blocked_domains:
                web_tool["filters"] = {}
                if allowed_domains:
                    web_tool["filters"]["allowed_domains"] = allowed_domains
                if blocked_domains:
                    web_tool["filters"]["blocked_domains"] = blocked_domains
            hosted_tools.append(web_tool)

        file_cfg = tools_config.get("file_search") or {}
        if file_cfg.get("enabled"):
            vector_store_ids = file_cfg.get("vector_store_ids", [])
            if vector_store_ids:
                hosted_tools.append(
                    {
                        "type": "file_search",
                        "vector_store_ids": vector_store_ids,
                        "max_num_results": _max_results(file_cfg.get("max_results")),
                    }
                )

        code_cfg = tools_config.get("code_interpreter") or {}
        if code_cfg.get("enabled"):
            hosted_tools.append({"type": "code_interpreter", "container": {"type": "auto"}})

        tools = mcp_tools + hosted_tools if execution_policy is None else []

        local_tools: list[dict[str, Any]] = []
        for category in active_cats:
            try:
                category_tools = registry.get_tools_by_category(category)
                if category_tools:
                    local_tools.extend(
                        tool
                        for tool in category_tools
                        if execution_policy is None
                        or (
                            (definition := registry.get_universal_definition(tool["name"]))
                            is not None
                            and execution_policy.allows_tool(definition)
                        )
                    )
            except Exception as e:
                api_logger.error(f"[TOOLS] Ошибка категории {category}: {e}")

        if local_tools:
            tools.extend(local_tools)

        # The server owns the tool list: a caller's "tools" entry never reaches the provider.
        ask_params = dict(params)
        ask_params.pop("tools", None)
        if tools or execution_policy is not None:
            ask_params["tools"] = tools

        if trace is not None and isinstance(trace, ExecutionTrace):
            ask_params["execution_trace"] = trace

        return self.ask(message, model_key, conversation_id, ask_params, execution_trace=trace)

