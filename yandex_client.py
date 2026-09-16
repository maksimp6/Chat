from trace_manager import ExecutionTrace
import sqlite3
import uuid
import json
import time
import requests
import asyncio
import websockets
from websockets.protocol import State
import logging
import os
import base64
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from tool_registry import registry
import mcp_storage
from db import get_conv_settings
from yandex_request_utils import sanitize_for_log as _sanitize_for_log
from yandex_request_builder import build_response_payload

os.makedirs('logs', exist_ok=True)
api_logger = logging.getLogger("yandex_api_debug")
api_logger.setLevel(logging.DEBUG)
if not api_logger.handlers:
    fh = logging.FileHandler("logs/api_debug.txt", encoding="utf-8", mode='a')
    fh.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s | %(levelname)-7s | %(message)s')
    fh.setFormatter(formatter)
    api_logger.addHandler(fh)

class YandexClientError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code

def _clean_mcp_tool(tool):
    if not isinstance(tool, dict) or tool.get("type") != "mcp":
        return None
    cid = tool.get("connector_id", "")
    url = tool.get("server_url", "").strip()
    if not url and not cid.startswith("connector_"):
        return None
    cleaned = {"type": "mcp", "server_label": tool.get("server_label", "mcp_server")}
    if url: cleaned["server_url"] = url
    if cid: cleaned["connector_id"] = cid
    if tool.get("server_description"): cleaned["server_description"] = tool["server_description"]
    if tool.get("require_approval"): cleaned["require_approval"] = tool["require_approval"]
    if tool.get("authorization"): cleaned["authorization"] = tool["authorization"]
    if tool.get("headers"): cleaned["headers"] = tool["headers"]
    return cleaned

def _clean_tools(tools):
    if not isinstance(tools, list):
        return tools
    cleaned = []
    for t in tools:
        if isinstance(t, dict) and t.get("type") == "mcp":
            res = _clean_mcp_tool(t)
            if res is not None: cleaned.append(res)
        else:
            cleaned.append(t)
    return cleaned

from file_manager import YandexFileManagerMixin

class YandexResponsesClient(YandexFileManagerMixin):
    def __init__(self, config):
        self._config = config
        self.base_url = config.BASE_URL.rstrip("/")
        self.responses_url = self.base_url + "/responses"
        self.conversations_url = self.base_url + "/conversations"
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": "Api-Key " + config.API_KEY,
            "Content-Type": "application/json",
            "OpenAI-Project": config.PROJECT_ID,
        })

    def _log_request(self, method, url, **kwargs):
        api_logger.info(f"[REQ] {method} {url}")
        if kwargs.get('json'):
            safe_payload = _sanitize_for_log(kwargs['json'])
            api_logger.debug(f"[REQ BODY]\n{json.dumps(safe_payload, ensure_ascii=False, indent=2)}")

    def _log_response(self, resp):
        status = resp.status_code
        api_logger.info(f"[RESP] {status} {resp.url}")
        try:
            resp_json = resp.json()
            safe_resp = _sanitize_for_log(resp_json)
            log_text = json.dumps(safe_resp, ensure_ascii=False, indent=2)
        except ValueError:
            log_text = resp.text
        if status >= 400:
            api_logger.error(f"[ERR BODY]\n{log_text}")
        else:
            api_logger.debug(f"[RESP BODY]\n{log_text}")
        return resp

    def create_conversation(self):
        try:
            self._log_request("POST", self.conversations_url, json={})
            resp = self._log_response(self.session.post(self.conversations_url, json={}, timeout=10))
            resp.raise_for_status()
            data = resp.json()
            api_logger.info(f"[CONV] Created: id={data.get('id')}")
            return data
        except requests.RequestException as e:
            raise YandexClientError("Create conv: " + str(e))

    def _resolve_yandex_conv_id(self, conv_id):
        if not conv_id: return None
        import uuid as _uuid
        try:
            val = _uuid.UUID(str(conv_id))
            return str(val)
        except (ValueError, TypeError, AttributeError):
            pass
        db_path = "alice_pro.db"
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("""
            CREATE TABLE IF NOT EXISTS conv_yandex_map (
                local_id TEXT PRIMARY KEY,
                yandex_id TEXT NOT NULL
            )
            """)
            cur.execute("SELECT yandex_id FROM conv_yandex_map WHERE local_id = ?", (conv_id,))
            row = cur.fetchone()
            if row:
                conn.close()
                return row[0]
            y_conv = self.create_conversation()
            y_id = y_conv.get("id")
            cur.execute("INSERT OR REPLACE INTO conv_yandex_map (local_id, yandex_id) VALUES (?, ?)", (conv_id, y_id))
            conn.commit()
            conn.close()
            return y_id
        except Exception as e:
            api_logger.error(f"[CONV_MAP] Ошибка: {e}")
            return None

    def ask(self, message, model_key, conversation_id=None, params=None, execution_trace=None, trace_step=None):
        params = params or {}
        is_background = params.get("background", True)
        is_stream = params.get("stream", False)
        if is_background: params["store"] = True
        
        yandex_conv_id = self._resolve_yandex_conv_id(conversation_id)

        metadata = (
            execution_trace.get_metadata()
            if execution_trace and isinstance(execution_trace, ExecutionTrace)
            else None
        )

        payload = build_response_payload(
            project_id=self._config.PROJECT_ID,
            model_key=model_key,
            message=message,
            params=params,
            metadata=metadata,
            conversation_id=conversation_id,
            yandex_conv_id=yandex_conv_id,
        )

        request_start_timestamp = time.time()
        request_start_perf = time.perf_counter()
        trace_step_number = trace_step

        if execution_trace and isinstance(execution_trace, ExecutionTrace):
            if trace_step_number is None:
                trace_step_number = len(execution_trace.trace.get("api_requests", [])) + 1
            execution_trace.add_api_request(
                _sanitize_for_log(payload),
                step_index=trace_step_number,
                start_timestamp=request_start_timestamp
            )
            execution_trace.add_event("api_request_sent", {
                "method": "POST",
                "url": self.responses_url,
                "step": trace_step_number,
                "payload": _sanitize_for_log(payload)
            })

        self._log_request("POST", self.responses_url, json=payload)
        resp = None
        try:
            resp = self._log_response(self.session.post(self.responses_url, json=payload, timeout=90))
            resp.raise_for_status()
        except requests.RequestException as e:
            error_timestamp = time.time()
            status_code = resp.status_code if resp is not None else None
            error_detail = None
            if resp is not None:
                try:
                    error_body = resp.json()
                    error_detail = _sanitize_for_log(error_body)
                except ValueError:
                    error_detail = resp.text[:4000]

            error_message = str(e)
            if status_code is not None:
                error_message = f"HTTP {status_code}: {error_message}"

            if execution_trace and isinstance(execution_trace, ExecutionTrace):
                execution_trace.add_event("api_request_error", {
                    "method": "POST",
                    "url": self.responses_url,
                    "step": trace_step_number,
                    "model": payload.get("model"),
                    "status_code": status_code,
                    "error": error_message,
                    "detail": error_detail,
                    "start_timestamp": request_start_timestamp,
                    "end_timestamp": error_timestamp,
                    "timing_ms": round((time.perf_counter() - request_start_perf) * 1000, 2)
                })

            raise YandexClientError(error_message, status_code=status_code) from e

        request_end_timestamp = time.time()
        request_duration_ms = round(
            (time.perf_counter() - request_start_perf) * 1000, 2
        )

        data = resp.json()

        if execution_trace and isinstance(execution_trace, ExecutionTrace):
            execution_trace.add_response(
                data,
                step_index=trace_step_number or 1,
                start_timestamp=request_start_timestamp,
                end_timestamp=request_end_timestamp,
                timing_ms=request_duration_ms,
                kind="initial_response"
            )
            step = trace_step
            if step is None:
                api_requests = execution_trace.trace.get("api_requests", [])
                step = api_requests[-1].get("step") if api_requests else 1
            execution_trace.add_event("api_request_completed", {
                "method": "POST",
                "url": self.responses_url,
                "step": step,
                "start_timestamp": request_start_timestamp,
                "end_timestamp": request_end_timestamp,
                "timing_ms": request_duration_ms,
                "response_id": data.get("id"),
                "status": data.get("status")
            })
        task_id = data.get("id")
        if not is_background:
            status = data.get("status")
            if status in ("completed", "incomplete"): return data
            if status in ("failed", "cancelled"): raise YandexClientError(f"Task status: {status}")
        return self._wait(task_id, execution_trace=execution_trace, trace_step=trace_step_number)

    def _wait(self, task_id, timeout=180, execution_trace=None, trace_step=None):
        start = time.time()
        url = self.responses_url + "/" + task_id
        delay = 0.5
        last_snapshot = None
        while time.time() - start < timeout:
            try:
                poll_start = time.time()
                self._log_request("GET", url)
                resp = self._log_response(self.session.get(url, timeout=15))
                if resp.status_code == 404:
                    if execution_trace and isinstance(execution_trace, ExecutionTrace):
                        execution_trace.add_event("api_poll_error", {
                            "step": trace_step, "response_id": task_id, "status_code": 404
                        })
                    time.sleep(delay)
                    delay = min(delay * 1.5, 3)
                    continue
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                if execution_trace and isinstance(execution_trace, ExecutionTrace):
                    execution_trace.add_event("api_poll_error", {
                        "step": trace_step, "response_id": task_id, "error": str(e)
                    })
                time.sleep(delay)
                delay = min(delay * 1.5, 3)
                continue

            poll_end = time.time()
            snapshot_key = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
            if execution_trace and isinstance(execution_trace, ExecutionTrace) and snapshot_key != last_snapshot:
                execution_trace.add_response(
                    data,
                    step_index=trace_step or 1,
                    start_timestamp=poll_start,
                    end_timestamp=poll_end,
                    timing_ms=round((poll_end - poll_start) * 1000, 2),
                    kind="poll_response"
                )
                last_snapshot = snapshot_key

            status = data.get("status")
            if status in ("completed", "incomplete", "failed", "cancelled"):
                if execution_trace and isinstance(execution_trace, ExecutionTrace):
                    execution_trace.add_event("api_poll_completed", {
                        "step": trace_step, "response_id": task_id, "status": status
                    })
                if status == "failed":
                    err = data.get('error')
                    err_msg = err.get('message', 'unknown') if isinstance(err, dict) else str(err)
                    raise YandexClientError(f"Task failed: {err_msg}")
                if status == "cancelled": raise YandexClientError("Task cancelled")
                return data
            time.sleep(delay)
            delay = min(delay * 1.5, 3)
        if execution_trace and isinstance(execution_trace, ExecutionTrace):
            execution_trace.add_event("api_poll_timeout", {"step": trace_step, "response_id": task_id, "timeout": timeout})
        raise YandexClientError(f"Timeout ({timeout}s) waiting for task {task_id}")

    @staticmethod
    def extract_reasoning_and_text(data):
        if not isinstance(data, dict):
            return "", str(data or "")
        reasoning_parts = []
        text_parts = []
        for item in data.get("output", []):
            if isinstance(item, dict):
                content_list = item.get("content", [])
                if isinstance(content_list, list):
                    for part in content_list:
                        if isinstance(part, dict):
                            p_type = part.get("type")
                            p_text = part.get("text", "")
                            if p_type == "reasoning_text" and p_text:
                                reasoning_parts.append(p_text)
                            elif p_type in ("output_text", "text") and p_text:
                                text_parts.append(p_text)
                        elif isinstance(part, str):
                            text_parts.append(part)
                elif item.get("type") == "output_text" and item.get("text"):
                    text_parts.append(str(item["text"]))
        final_text = "".join(text_parts) or data.get("output_text") or data.get("text") or ""
        final_reasoning = "\n\n".join(reasoning_parts)
        return final_reasoning, str(final_text)

    @staticmethod
    def extract_text(data):
        _, text = YandexResponsesClient.extract_reasoning_and_text(data)
        return text

    @staticmethod
    def extract_usage(data):
        if not isinstance(data, dict): return None
        u = data.get("usage") or {}
        in_det = u.get("input_tokens_details") or {}
        out_det = u.get("output_tokens_details") or {}
        return {
            "input_tokens": u.get("input_tokens", 0),
            "output_tokens": u.get("output_tokens", 0),
            "total_tokens": u.get("total_tokens", 0),
            "cached_tokens": in_det.get("cached_tokens", 0),
            "tool_tokens": in_det.get("tool_tokens", 0),
            "reasoning_tokens": out_det.get("reasoning_tokens", 0),
            "created_at": data.get("created_at"),
            "completed_at": data.get("completed_at"),
            "incomplete_details": data.get("incomplete_details")
        }

class YandexMcpMixin:
    def _execute_single_tool(self, tc, all_servers):
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

        try:
            result = registry.execute(name, args, all_servers)
            error = None

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
            web_tool = {
                "type": "web_search",
                "search_context_size": web_cfg.get("context_size") or "medium"
            }
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