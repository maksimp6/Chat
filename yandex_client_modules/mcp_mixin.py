import json
import time

from trace_manager import ExecutionTrace
from tool_registry import registry
import mcp_storage
from db import get_conv_settings

from yandex_request_utils import sanitize_for_log as _sanitize_for_log
from yandex_api_logger import api_logger
from universal_tool_platform import UniversalToolCall, UniversalToolExecutor


class YandexMcpMixin:
    def _execute_single_tool(self, tc, all_servers, trace=None):
        import time as _t

        name = tc.get("name") or tc.get("function", {}).get("name")
        if name and "<|" in name:
            name = name.split("<|")[0].strip()

        args = tc.get("arguments") or tc.get("function", {}).get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}

        call_id = (
            tc.get("call_id")
            or tc.get("id")
            or tc.get("tool_call_id")
            or name
        )

        start_timestamp = _t.time()
        t_start = _t.perf_counter()

        trace_id = trace.trace_id if isinstance(trace, ExecutionTrace) else None
        user_id = None
        if isinstance(trace, ExecutionTrace):
            user_id = (trace.trace.get("context") or {}).get("user_id")

        call = UniversalToolCall(
            tool_name=name,
            arguments=args,
            call_id=call_id,
            transport="responses_api",
            trace_id=trace_id,
            user_id=user_id,
            metadata={"source": "yandex_responses", "server_count": len(all_servers or [])},
        )

        try:
            executor = UniversalToolExecutor(registry)
            if isinstance(trace, ExecutionTrace):
                universal_result = executor.execute_with_trace(call, trace)
            else:
                universal_result = executor.execute(call)

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
                json.dumps(
                    _sanitize_for_log(result),
                    ensure_ascii=False,
                    indent=2
                ) if isinstance(result, (dict, list))
                else str(result)
            )

        except Exception as exc:
            result = None
            error = str(exc)

            api_logger.exception(
                "[LOCAL TOOL ERROR] name=%s call_id=%s",
                name,
                call_id
            )

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
            "success": error is None
        }

        return {
            "call_id": call_id,
            "name": name,
            "content": content_str,
            "result": result,
            "error": error,
            "timing": timing
        }

    def ask_with_mcp(self, message, model_key, conversation_id=None, params=None, trace=None):
        params = params or {}
        step_timings = []
        import time as _t

        mcp_tools = []
        all_servers = mcp_storage.list_servers()
        enabled_servers = []
        if conversation_id:
            try: enabled_servers = mcp_storage.get_enabled_servers_for_conv(conversation_id)
            except Exception: enabled_servers = []

        for s in enabled_servers:
            if s.get("server_url") or (s.get("connector_id") or "").startswith("connector_"):
                mcp_tools.append({
                    "type": "mcp",
                    "server_label": s.get("server_label") or s.get("name"),
                    "server_url": s.get("server_url"),
                    "connector_id": s.get("connector_id"),
                    "authorization": s.get("authorization")
                })

        active_cats = None
        if conversation_id:
            try: active_cats = get_conv_settings(conversation_id).get("active_tool_categories") if get_conv_settings(conversation_id) else None
            except Exception: active_cats = None

        if active_cats is None:
            active_cats = params.get("active_tool_categories")
        if active_cats is None:
            active_cats = ["git", "termux", "system", "filesystem", "wikipedia", "profiler"]

        hosted_tools = []
        conv_settings = get_conv_settings(conversation_id) if conversation_id else {}
        tools_config = (conv_settings or {}).get("tools_config") or params.get("tools_config") or {}

        web_cfg = tools_config.get("web_search") or {}
        if web_cfg.get("enabled"):
            web_tool = {"type": "web_search", "search_context_size": web_cfg.get("context_size") or "medium"}
            allowed = web_cfg.get("allowed_domains") or ""
            blocked = web_cfg.get("blocked_domains") or ""
            allowed_domains = [x.strip() for x in allowed.replace("\\n", ",").split(",") if x.strip()]
            blocked_domains = [x.strip() for x in blocked.replace("\\n", ",").split(",") if x.strip()]
            if allowed_domains or blocked_domains:
                web_tool["filters"] = {}
                if allowed_domains: web_tool["filters"]["allowed_domains"] = allowed_domains
                if blocked_domains: web_tool["filters"]["blocked_domains"] = blocked_domains
            hosted_tools.append(web_tool)

        file_cfg = tools_config.get("file_search") or {}
        if file_cfg.get("enabled"):
            vector_ids = file_cfg.get("vector_store_ids") or ""
            vector_store_ids = [x.strip() for x in vector_ids.replace("\\n", ",").split(",") if x.strip()]
            if vector_store_ids:
                hosted_tools.append({"type": "file_search", "vector_store_ids": vector_store_ids, "max_num_results": int(file_cfg.get("max_results", 20))})

        code_cfg = tools_config.get("code_interpreter") or {}
        if code_cfg.get("enabled"):
            hosted_tools.append({"type": "code_interpreter", "container": {"type": "auto"}})

        tools = mcp_tools + hosted_tools

        local_tools = []
        for category in active_cats:
            try:
                category_tools = registry.get_tools_by_category(category)
                if category_tools:
                    local_tools.extend(category_tools)
            except Exception as e:
                api_logger.error(f"[TOOLS] Ошибка категории {category}: {e}")

        if local_tools:
            tools.extend(local_tools)

        ask_params = dict(params)
        if tools:
            ask_params["tools"] = tools

        if trace is not None and isinstance(trace, ExecutionTrace):
            ask_params["execution_trace"] = trace

        return self.ask(message, model_key, conversation_id, ask_params, execution_trace=trace)
