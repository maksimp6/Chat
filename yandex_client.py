import sqlite3
import uuid
"""Alice Pro - Yandex API Client (Full logging — only audio/binary masked)"""
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
    if not isinstance(tool, dict):
        return None
    if tool.get("type") != "mcp":
        return tool
    cid = tool.get("connector_id", "")
    if cid in ("local_git", "termux_api", "filesystem"):
        return None
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
        res = _clean_mcp_tool(t)
        if res is not None:
            cleaned.append(res)
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

    def update_conversation_metadata(self, conv_id, metadata):
        try:
            url = f"{self.conversations_url}/{conv_id}"
            self._log_request("POST", url, json={"metadata": metadata})
            resp = self._log_response(self.session.post(url, json={"metadata": metadata}, timeout=10))
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise YandexClientError("Update metadata: " + str(e))

    def _resolve_yandex_conv_id(self, conv_id):
        if not conv_id:
            return None
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
            api_logger.info(f"[CONV_MAP] Связан локальный ID {conv_id} -> Yandex UUID {y_id}")
            return y_id
        except Exception as e:
            api_logger.error(f"[CONV_MAP] Ошибка создания Yandex conversation для {conv_id}: {e}")
            return None

    def ask(self, message, model_key, conversation_id=None, params=None):
        params = params or {}
        is_background = params.get("background", True)
        is_stream = params.get("stream", False)
        if is_background:
            params["store"] = True
        if is_stream:
            raise YandexClientError("Streaming is not supported by ask(). Use a dedicated streaming method.")
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
        if params.get("tools"):
            payload["tools"] = _clean_tools(params["tools"])
            api_logger.info(f"[ASK] Tools cleaned: {len(payload['tools'])} tools")
        if params.get("prompt"): payload["prompt"] = params["prompt"]
        if params.get("text"): payload["text"] = params["text"]
        if params.get("reasoning"): payload["reasoning"] = params["reasoning"]
        if params.get("stream") is not None: payload["stream"] = params["stream"]
        if params.get("truncation"): payload["truncation"] = params["truncation"]
        if params.get("tool_choice"): payload["tool_choice"] = params["tool_choice"]
        ptc = params.get("parallel_tool_calls")
        if ptc is not None:
            if isinstance(ptc, str):
                ptc = ptc.lower() not in ("false", "0")
            payload["parallel_tool_calls"] = bool(ptc)
        else:
            payload["parallel_tool_calls"] = True if payload.get("tools") else False
        if params.get("max_tool_calls"): payload["max_tool_calls"] = int(params["max_tool_calls"])
        if params.get("metadata"): payload["metadata"] = params["metadata"]
        if params.get("service_tier"): payload["service_tier"] = params["service_tier"]
        if params.get("prompt_cache_key"): payload["prompt_cache_key"] = params["prompt_cache_key"]
        if params.get("top_logprobs"): payload["top_logprobs"] = int(params["top_logprobs"])
        if params.get("previous_response_id"): payload["previous_response_id"] = params["previous_response_id"]
        yandex_conv_id = self._resolve_yandex_conv_id(conversation_id)
        if yandex_conv_id:
            payload["conversation"] = {"id": yandex_conv_id}
        self._log_request("POST", self.responses_url, json=payload)
        try:
            resp = self._log_response(self.session.post(self.responses_url, json=payload, timeout=90))
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            err_detail = ""
            try:
                err_body = resp.json()
                err_detail = json.dumps(err_body, ensure_ascii=False)[:500]
            except Exception:
                err_detail = resp.text[:500] if resp.text else ""
            api_logger.error(f"[ASK] HTTP error {resp.status_code}: {err_detail}")
            raise YandexClientError(f"Ask (HTTP {resp.status_code}): {err_detail or str(e)}")
        except requests.RequestException as e:
            raise YandexClientError("Ask: " + str(e))
        data = resp.json()
        task_id = data.get("id")
        api_logger.info(f"[ASK] Response id={task_id}, status={data.get('status')}")
        if not is_background:
            status = data.get("status")
            if status in ("completed", "incomplete"):
                api_logger.info(f"[ASK] Synchronous response received (status={status})")
                return data
            elif status in ("failed", "cancelled"):
                err = data.get('error')
                err_msg = err.get('message', 'unknown') if isinstance(err, dict) else str(err)
                raise YandexClientError(f"Task failed: {err_msg}")
            api_logger.warning(f"[ASK] Sync response has unexpected status='{status}', falling back to poll")
        return self._wait(task_id)

    def _wait(self, task_id, timeout=180):
        start = time.time()
        url = self.responses_url + "/" + task_id
        delay = 0.5
        api_logger.info(f"[WAIT] Polling task {task_id}")
        while time.time() - start < timeout:
            try:
                self._log_request("GET", url)
                resp = self._log_response(self.session.get(url, timeout=15))
                if resp.status_code == 404:
                    api_logger.debug(f"[WAIT] Task {task_id} not found yet (404), retrying in {delay}s...")
                    time.sleep(delay)
                    delay = min(delay * 1.5, 3)
                    continue
                resp.raise_for_status()
                data = resp.json()
            except requests.exceptions.HTTPError as e:
                err_detail = ""
                try:
                    err_detail = json.dumps(resp.json(), ensure_ascii=False)[:500]
                except Exception:
                    err_detail = resp.text[:500] if resp.text else ""
                raise YandexClientError(f"Wait poll failed (HTTP {resp.status_code}): {err_detail or str(e)}")
            except requests.exceptions.RequestException as e:
                api_logger.warning(f"[WAIT] Network error: {e}, retrying in {delay}s...")
                time.sleep(delay)
                delay = min(delay * 1.5, 3)
                continue
            status = data.get("status")
            api_logger.debug(f"[WAIT] Status: {status}")
            if status in ("completed", "incomplete", "failed", "cancelled"):
                api_logger.info(f"[WAIT] Done: {status} (task={task_id})")
                if status == "failed":
                    err = data.get('error')
                    err_msg = err.get('message', 'unknown') if isinstance(err, dict) else str(err)
                    raise YandexClientError(f"Task failed: {err_msg}")
                if status == "cancelled":
                    raise YandexClientError("Task cancelled")
                return data
            time.sleep(delay)
            delay = min(delay * 1.5, 3)
        raise YandexClientError(f"Timeout ({timeout}s) waiting for task {task_id}")

    @staticmethod
    def extract_text(data):
        if not isinstance(data, dict):
            return str(data or "")
        if data.get("output_text") and isinstance(data["output_text"], str):
            return data["output_text"]
        for item in data.get("output", []):
            if isinstance(item, dict):
                if item.get("type") == "message" and item.get("role") == "assistant":
                    for part in item.get("content", []):
                        if isinstance(part, dict) and part.get("type") == "output_text" and part.get("text"):
                            return str(part["text"])
                        elif isinstance(part, str):
                            return part
                elif item.get("type") == "output_text" and item.get("text"):
                    return str(item["text"])
                elif "text" in item and isinstance(item["text"], str) and item["text"].strip():
                    return item["text"]
                elif "content" in item and isinstance(item["content"], str) and item["content"].strip():
                    return item["content"]
        val = data.get("text")
        if isinstance(val, str) and val.strip():
            return val
        return ""

    @staticmethod
    def extract_usage(data):
        u = data.get("usage")
        if not u: return None
        return {
            "input_tokens": u.get("input_tokens", 0),
            "output_tokens": u.get("output_tokens", 0),
            "total_tokens": u.get("total_tokens", 0),
        }

class YandexRealtimeClient:
    def __init__(self, config, model_key, voice="kirill", conversation_id=None,
                 response_mode="audio", instructions=None, vad_threshold=0.3,
                 vad_silence_duration_ms=500, vad_prefix_padding_ms=300):
        self._config = config
        self.base_url = config.BASE_URL.rstrip("/")
        ws_base = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        self.ws_url = f"{ws_base}/realtime?model=gpt://{config.PROJECT_ID}/{model_key}"
        self.api_key = config.API_KEY
        self.model_key = model_key
        self.voice = voice
        self.conversation_id = conversation_id
        self.response_mode = response_mode
        self.instructions = instructions or "Ты — полезный голосовой ассистент."
        self.vad_threshold = vad_threshold
        self.vad_silence_duration_ms = vad_silence_duration_ms
        self.vad_prefix_padding_ms = vad_prefix_padding_ms
        self.ws = None
        self.session_id = None

    async def connect(self):
        headers = {
            "Authorization": "Api-Key " + self.api_key,
            "OpenAI-Project": self._config.PROJECT_ID,
        }
        api_logger.info(f"[WS CONNECT] {self.ws_url}")
        try:
            self.ws = await websockets.connect(
                self.ws_url, additional_headers=headers, ping_interval=20, ping_timeout=10
            )
            api_logger.info("[WS CONNECT] OK")
        except Exception as e:
            api_logger.error(f"[WS CONNECT] Failed: {e}")
            return False
        if self.response_mode == "text":
            out_mods = ["text"]
        elif self.response_mode == "both":
            out_mods = ["text", "audio"]
        else:
            out_mods = ["audio"]
        session_config = {
            "instructions": self.instructions,
            "output_modalities": out_mods,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": float(self.vad_threshold),
                        "prefix_padding_ms": int(self.vad_prefix_padding_ms),
                        "silence_duration_ms": int(self.vad_silence_duration_ms)
                    }
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "voice": self.voice
                }
            }
        }
        if self.conversation_id:
            session_config["conversation_id"] = self.conversation_id
        payload = {"type": "session.update", "session": session_config}
        api_logger.debug(f"[WS SESSION] Sending: {json.dumps(payload, ensure_ascii=False)}")
        await self.ws.send(json.dumps(payload))
        try:
            deadline = asyncio.get_event_loop().time() + 15
            while asyncio.get_event_loop().time() < deadline:
                resp = await asyncio.wait_for(self.ws.recv(), timeout=5)
                data = json.loads(resp)
                evt_type = data.get("type", "")
                api_logger.info(f"[WS SESSION] Response: {evt_type}")
                if data.get("error"):
                    api_logger.error(f"[WS SESSION] Error: {json.dumps(data['error'])}")
                    return False
                if evt_type in ("session.created", "session.updated"):
                    self.session_id = data.get("session", {}).get("id")
                    api_logger.info(f"[WS SESSION] ID: {self.session_id}")
                    return True
                api_logger.debug(f"[WS SESSION] Skipping event: {evt_type}")
            api_logger.error("[WS SESSION] Timeout waiting for session.created/updated")
            return False
        except Exception as e:
            api_logger.error(f"[WS SESSION] Failed: {e}")
            return False

    async def send_audio_binary(self, audio_bytes: bytes):
        if self.ws and self.ws.state == State.OPEN:
            try:
                b64_audio = base64.b64encode(audio_bytes).decode('ascii')
                event = {"type": "input_audio_buffer.append", "audio": b64_audio}
                await self.ws.send(json.dumps(event))
            except Exception as e:
                api_logger.error(f"[WS AUDIO] Send failed: {e}")

    async def send_text(self, text: str):
        if self.ws and self.ws.state == State.OPEN:
            try:
                event = {"type": "input_text", "text": text}
                await self.ws.send(json.dumps(event))
                api_logger.debug(f"[WS TEXT] Sent: {text[:100]}")
            except Exception as e:
                api_logger.error(f"[WS TEXT] Send failed: {e}")

    async def listen(self, queue):
        if not self.ws:
            return
        try:
            async for msg in self.ws:
                if isinstance(msg, bytes):
                    continue
                try:
                    data = json.loads(msg)
                    evt_type = data.get("type", "")
                    api_logger.info(f"[WS LISTEN] Event: {evt_type}")
                    if evt_type == "error":
                        api_logger.error(f"[WS LISTEN] Server error: {json.dumps(data)}")
                    safe_data = _sanitize_for_log(data)
                    api_logger.debug(f"[WS LISTEN] Data: {json.dumps(safe_data, ensure_ascii=False)}")
                    await queue.put(data)
                except json.JSONDecodeError:
                    continue
        except websockets.exceptions.ConnectionClosed as e:
            api_logger.info(f"[WS LISTEN] Closed: {e}")
            await queue.put({"type": "connection_closed", "message": str(e)})
        except Exception as e:
            api_logger.error(f"[WS LISTEN] Error: {e}")
            await queue.put({"type": "error", "message": str(e)})

    async def close(self):
        if self.ws:
            await self.ws.close()


# === Local Tools MCP Integration ===
import mcp_storage

def _build_function_tools(connectors):
    tools = []
    if 'local_git' in connectors:
        try:
            from git_mcp_tools import GIT_TOOLS
            for name, cfg in GIT_TOOLS.items():
                tools.append({"type": "function", "name": name, "description": cfg["description"], "parameters": cfg["parameters"]})
        except Exception:
            pass
    if 'termux_api' in connectors:
        try:
            from termux_mcp_tools import TERMUX_TOOLS
            for name, cfg in TERMUX_TOOLS.items():
                tools.append({"type": "function", "name": name, "description": cfg["description"], "parameters": cfg["parameters"]})
        except Exception:
            pass
    try:
        from termux_system_tools import SYSTEM_TOOLS
        for name, cfg in SYSTEM_TOOLS.items():
            tools.append({"type": "function", "name": name, "description": cfg["description"], "parameters": cfg["parameters"]})
    except Exception:
        pass
    try:
        from filesystem_mcp_tools import FILESYSTEM_TOOLS
        for name, cfg in FILESYSTEM_TOOLS.items():
            tools.append({"type": "function", "name": name, "description": cfg["description"], "parameters": cfg["parameters"]})
    except Exception:
        pass
    return tools

def _execute_local_tool_call(tool_call, server_configs):
    import json
    func_name = tool_call.get("name") or tool_call.get("function", {}).get("name")
    arguments = tool_call.get("arguments") or tool_call.get("function", {}).get("arguments", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except Exception:
            arguments = {}
    result = {"error": f"Инструмент '{func_name}' не найден"}
    try:
        from git_mcp_tools import GIT_TOOLS, execute_tool as execute_git
        if func_name in GIT_TOOLS:
            cfg = next((s.get('config', {}) for s in server_configs if s.get('connector_id') == 'local_git'), {})
            result = execute_git(func_name, arguments, cfg)
    except Exception:
        pass
    try:
        from filesystem_mcp_tools import FILESYSTEM_TOOLS, execute_fs_tool
        if func_name in FILESYSTEM_TOOLS:
            result = execute_fs_tool(func_name, arguments)
    except Exception:
        pass
    try:
        from termux_system_tools import SYSTEM_TOOLS, execute_system_tool
        if func_name in SYSTEM_TOOLS:
            result = execute_system_tool(func_name, arguments)
    except Exception:
        pass
    try:
        from termux_mcp_tools import TERMUX_TOOLS, execute_termux_tool
        if func_name in TERMUX_TOOLS:
            cfg = next((s.get('config', {}) for s in server_configs if s.get('connector_id') == 'termux_api'), {})
            result = execute_termux_tool(func_name, arguments, cfg)
    except Exception:
        pass
    call_id = tool_call.get("call_id") or tool_call.get("id") or tool_call.get("tool_call_id") or func_name
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(result, ensure_ascii=False) if isinstance(result, (dict, list)) else str(result)
    }

class YandexMcpMixin:
    def _resolve_yandex_conv_id(self, conv_id):
        if not conv_id:
            return None
        try:
            val = uuid.UUID(str(conv_id))
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
            api_logger.info(f"[CONV_MAP] Связан локальный ID {conv_id} -> Yandex UUID {y_id}")
            return y_id
        except Exception as e:
            api_logger.error(f"[CONV_MAP] Ошибка создания Yandex conversation для {conv_id}: {e}")
            return None

    def ask_with_mcp(self, message, model_key, conversation_id=None, params=None):
        params = params or {}
        local_cids = set()
        raw_tools = params.get("tools") or []
        cleaned_tools = []
        for t in raw_tools:
            if isinstance(t, dict):
                cid = t.get("connector_id")
                if cid in ('local_git', 'termux_api'):
                    local_cids.add(cid)
                    continue
                if t.get("type") == "mcp" and not t.get("server_url") and not (cid or "").startswith("connector_"):
                    continue
                cleaned_tools.append(t)
        all_servers = mcp_storage.list_servers()
        if conversation_id:
            try:
                db_servers = mcp_storage.get_enabled_servers_for_conv(conversation_id)
                for s in db_servers:
                    cid = s.get('connector_id')
                    if cid in ('local_git', 'termux_api'):
                        local_cids.add(cid)
            except Exception as e:
                api_logger.error(f"[MCP] Ошибка загрузки серверов из БД: {e}")
        if not local_cids:
            for s in all_servers:
                cid = s.get('connector_id')
                if cid in ('local_git', 'termux_api'):
                    local_cids.add(cid)
        local_function_tools = _build_function_tools(local_cids)
        params["tools"] = cleaned_tools + local_function_tools
        import time as _t
        step_timings = []
        t0 = _t.perf_counter()
        response = self.ask(message, model_key, conversation_id, params)
        t1 = _t.perf_counter()
        step_timings.append({"name": "LLM Router (поиск инструментов)", "duration_ms": round((t1 - t0) * 1000)})
        output = response.get("output", [])
        tool_calls = []
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type in ("function_call", "tool_call"):
                tool_calls.append(item)
            elif item_type == "message":
                for part in item.get("content", []):
                    if isinstance(part, dict) and part.get("type") in ("function_call", "tool_call"):
                        tool_calls.append(part)
        # Строим мапу серверов для быстрого поиска по server_label (один раз за вызов)
        server_map = {srv.get("server_label"): srv for srv in all_servers}

        # Извлекаем внешние MCP-вызовы, выполненные Yandex Cloud
        for item in output:
            if isinstance(item, dict) and item.get("type") == "mcp_call":
                label = item.get("server_label", "External MCP")
                target = server_map.get(label)
                real_url = target.get("server_url") if target else "Unknown"

                step_timings.append({
                    "name": f"MCP: {item.get('name', 'unknown')}",
                    "duration_ms": 0,
                    "duration_source": "unavailable",
                    "server_type": "mcp",
                    "server_label": label,
                    "server_url": real_url,
                    "status": item.get("status", "completed")
                })

        if not tool_calls and not any(t.get("server_type") == "mcp" for t in step_timings):
            response["step_timings"] = step_timings
            return response
        api_logger.info(f"[MCP] Выполняю {len(tool_calls)} локальных вызовов")
        tool_results = []
        raw_outputs_text = []
        for tc in tool_calls:
            name = tc.get("name") or tc.get("function", {}).get("name")
            call_id = tc.get("call_id") or tc.get("id") or name
            t_tool_start = _t.perf_counter()
            res_obj = _execute_local_tool_call(tc, all_servers)
            t_tool_end = _t.perf_counter()
            srv_info = {"type": "local", "label": "Local Termux", "url": "localhost"}
            if name.startswith("git_"): srv_info["label"] = "Local Git"
            elif name in ("read_file", "write_file", "list_directory", "run_command"): srv_info["label"] = "Local Filesystem"
            
            step_timings.append({
                "name": f"Tool: {name}", 
                "duration_ms": round((t_tool_end - t_tool_start) * 1000),
                "server_type": srv_info["type"],
                "server_label": srv_info["label"],
                "server_url": srv_info["url"]
            })
            res_content = res_obj.get("content", "")
            tool_results.append({"call_id": call_id, "name": name, "content": res_content})
            raw_outputs_text.append(f"[{name}]: {res_content}")
            api_logger.debug(f"[MCP] Результат {name}: {res_content[:150]}")
        final_params = {**params}
        try:
            fc_inputs = [
                {"type": "function_call_output", "call_id": tr["call_id"], "output": tr["content"]}
                for tr in tool_results
            ]
            final_response = self.ask(None, model_key, conversation_id, {**final_params, "input": fc_inputs})
            text = self.extract_text(final_response)
            if text:
                final_response["step_timings"] = step_timings
                return final_response
        except Exception as e1:
            api_logger.info(f"[MCP] Стратегия 1 не сработала: {e1}")
        try:
            tool_msg_inputs = [
                {"role": "tool", "tool_call_id": tr["call_id"], "content": tr["content"]}
                for tr in tool_results
            ]
            new_input = [
                {"role": "user", "content": message},
                {"role": "assistant", "content": tool_calls},
                *tool_msg_inputs
            ]
            final_response = self.ask(None, model_key, None, {**final_params, "input": new_input})
            text = self.extract_text(final_response)
            if text:
                final_response["step_timings"] = step_timings
                return final_response
        except Exception as e2:
            api_logger.info(f"[MCP] Стратегия 2 не сработала: {e2}")
        try:
            tool_summary = "\n".join(raw_outputs_text)
            prompt = f"Пользователь запросил: \"{message}\"\nРезультат выполнения локальной команды:\n{tool_summary}\nОбъясни этот результат пользователю кратко и по делу."
            t_synth_start = _t.perf_counter()
            final_response = self.ask(prompt, model_key, conversation_id, final_params)
            t_synth_end = _t.perf_counter()
            step_timings.append({"name": "LLM Synthesis (финальный ответ)", "duration_ms": round((t_synth_end - t_synth_start) * 1000)})
            final_response["step_timings"] = step_timings
            return final_response
        except Exception as e3:
            api_logger.error(f"[MCP] Стратегия 3 не сработала: {e3}")
            return {"output_text": "Команда выполнена успешно:\n" + "\n".join(raw_outputs_text), "status": "completed"}
