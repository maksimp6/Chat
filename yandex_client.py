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

_BINARY_THRESHOLD = 100_000
_BASE64_RE = re.compile(r'^[A-Za-z0-9+/]{200,}={0,2}$', re.DOTALL)
_BINARY_KEYS = frozenset({
    'audio', 'audio_bytes', 'audio_data',
    'file_data', 'image_data', 'image_b64',
    'attachment_data', 'screenshot',
    'pcm', 'wav', 'ogg',
})

def _sanitize_for_log(obj):
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            key_lower = k.lower() if isinstance(k, str) else str(k).lower()
            if key_lower in _BINARY_KEYS and isinstance(v, str) and len(v) > 1000:
                result[k] = f"<AUDIO/BINARY MASKED: {len(v)} chars>"
            else:
                result[k] = _sanitize_for_log(v)
        return result
    elif isinstance(obj, list):
        return [_sanitize_for_log(item) for item in obj]
    elif isinstance(obj, str):
        if len(obj) > _BINARY_THRESHOLD:
            if obj.startswith('data:'):
                return f"<DATA_URI MASKED: {len(obj)} chars>"
            if _BASE64_RE.match(obj):
                return f"<BASE64 MASKED: {len(obj)} chars>"
        return obj
    else:
        return obj

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

    def ask(self, message, model_key, conversation_id=None, params=None):
        params = params or {}
        is_background = params.get("background", True)
        is_stream = params.get("stream", False)
        if is_background: params["store"] = True
        
        payload = {
            "model": "gpt://" + self._config.PROJECT_ID + "/" + model_key + "/latest",
            "input": params.get("input") or [{"role": "user", "content": message}],
            "background": is_background,
            "store": params.get("store", True),
        }
        if params.get("instructions"): payload["instructions"] = params["instructions"]
        if params.get("temperature") is not None: payload["temperature"] = float(params["temperature"])
        if params.get("top_p") is not None: payload["top_p"] = float(params["top_p"])
        if params.get("max_output_tokens"): payload["max_output_tokens"] = int(params["max_output_tokens"])
        if params.get("prompt"): payload["prompt"] = params["prompt"]
        if params.get("text"): payload["text"] = params["text"]
        if params.get("truncation"): payload["truncation"] = params["truncation"]
        if params.get("service_tier"): payload["service_tier"] = params["service_tier"]
        if params.get("reasoning"):
            payload["reasoning"] = params["reasoning"]
        elif params.get("reasoning_effort") and params.get("reasoning_effort") != "disabled":
            payload["reasoning"] = {"effort": params["reasoning_effort"]}

        raw_tools = params.get("tools")
        cleaned_tools = _clean_tools(raw_tools) if raw_tools else []
        if cleaned_tools:
            payload["tools"] = cleaned_tools
            if params.get("tool_choice"):
                payload["tool_choice"] = params["tool_choice"]
            if params.get("max_tool_calls"):
                payload["max_tool_calls"] = int(params["max_tool_calls"])

            ptc = params.get("parallel_tool_calls")
            if ptc is not None:
                if isinstance(ptc, str):
                    ptc = ptc.lower() not in ("false", "0")
                payload["parallel_tool_calls"] = bool(ptc)
            else:
                payload["parallel_tool_calls"] = True
        
        yandex_conv_id = self._resolve_yandex_conv_id(conversation_id)
        if yandex_conv_id:
            payload["conversation"] = {"id": yandex_conv_id}

        self._log_request("POST", self.responses_url, json=payload)
        try:
            resp = self._log_response(self.session.post(self.responses_url, json=payload, timeout=90))
            resp.raise_for_status()
        except requests.RequestException as e:
            raise YandexClientError(f"Ask Request Error: {e}")

        data = resp.json()
        task_id = data.get("id")
        if not is_background:
            status = data.get("status")
            if status in ("completed", "incomplete"): return data
            if status in ("failed", "cancelled"): raise YandexClientError(f"Task status: {status}")
        return self._wait(task_id)

    def _wait(self, task_id, timeout=180):
        start = time.time()
        url = self.responses_url + "/" + task_id
        delay = 0.5
        while time.time() - start < timeout:
            try:
                self._log_request("GET", url)
                resp = self._log_response(self.session.get(url, timeout=15))
                if resp.status_code == 404:
                    time.sleep(delay)
                    delay = min(delay * 1.5, 3)
                    continue
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException:
                time.sleep(delay)
                delay = min(delay * 1.5, 3)
                continue

            status = data.get("status")
            if status in ("completed", "incomplete", "failed", "cancelled"):
                if status == "failed":
                    err = data.get('error')
                    err_msg = err.get('message', 'unknown') if isinstance(err, dict) else str(err)
                    raise YandexClientError(f"Task failed: {err_msg}")
                if status == "cancelled": raise YandexClientError("Task cancelled")
                return data
            time.sleep(delay)
            delay = min(delay * 1.5, 3)
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
            try: args = json.loads(args)
            except Exception: args = {}

        t_start = _t.perf_counter()
        result = registry.execute(name, args, all_servers)
        t_end = _t.perf_counter()

        call_id = tc.get("call_id") or tc.get("id") or tc.get("tool_call_id") or name
        content_str = json.dumps(result, ensure_ascii=False) if isinstance(result, (dict, list)) else str(result)
        
        timing = {
            "name": f"Tool: {name}",
            "duration_ms": round((t_end - t_start) * 1000),
            "server_type": "local",
            "server_label": "Local Registry"
        }
        return {
            "call_id": call_id,
            "name": name,
            "content": content_str,
            "timing": timing
        }

    def ask_with_mcp(self, message, model_key, conversation_id=None, params=None):
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
            conv_settings = get_conv_settings(conversation_id)
            if conv_settings and "active_tool_categories" in conv_settings:
                active_cats = conv_settings["active_tool_categories"]

        if active_cats is None:
            active_cats = params.get("active_tool_categories")
        if active_cats is None:
            active_cats = ["git", "termux", "system", "filesystem", "wikipedia", "profiler"]

        active_set = set(active_cats)
        local_tools = registry.get_definitions(active_set)
        params["tools"] = mcp_tools + local_tools
        api_logger.info(f"[ROUTER] Подключено {len(params['tools'])} инструментов. Категории: {active_set}")

        t0 = _t.perf_counter()
        response = self.ask(message, model_key, conversation_id, params)
        t1 = _t.perf_counter()
        step_timings.append({"name": "LLM Router (поиск инструментов)", "duration_ms": round((t1 - t0) * 1000)})

        output = response.get("output", [])
        tool_calls = []
        for item in output:
            if not isinstance(item, dict): continue
            item_type = item.get("type")
            if item_type in ("function_call", "tool_call"):
                tool_calls.append(item)
            elif item_type == "message":
                for part in item.get("content", []):
                    if isinstance(part, dict) and part.get("type") in ("function_call", "tool_call"):
                        tool_calls.append(part)

        if not tool_calls:
            response["step_timings"] = step_timings
            return response

        is_parallel = params.get("parallel_tool_calls", True)
        max_workers = min(len(tool_calls), 8) if is_parallel else 1
        raw_outputs_text = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_tc = {executor.submit(self._execute_single_tool, tc, all_servers): tc for tc in tool_calls}
            for future in as_completed(future_to_tc):
                res = future.result()
                step_timings.append(res["timing"])
                raw_outputs_text.append(f"[{res['name']}]: {res['content']}")

        tool_summary = "\n".join(raw_outputs_text)
        prompt = (
            f"Пользователь запросил: \"{message}\"\n"
            f"Результат выполнения инструментов:\n{tool_summary}\n"
            f"Дай полноценный и точный ответ на основе этих данных."
        )
        synth_params = {k: v for k, v in params.items() if k not in ("tools", "parallel_tool_calls", "tool_choice")}

        t_synth_start = _t.perf_counter()
        final_response = self.ask(prompt, model_key, conversation_id, synth_params)
        t_synth_end = _t.perf_counter()
        step_timings.append({"name": "LLM Synthesis (финальный ответ)", "duration_ms": round((t_synth_end - t_synth_start) * 1000)})
        final_response["step_timings"] = step_timings
        return final_response
