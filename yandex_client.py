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

from yandex_client_modules.errors import YandexClientError
from yandex_client_modules.conversations import YandexConversationMixin
from yandex_client_modules.request_mixin import YandexRequestMixin
from yandex_client_modules.polling_mixin import YandexPollingMixin
from yandex_client_modules.mcp_mixin import YandexMcpMixin

from file_manager import YandexFileManagerMixin

class YandexResponsesClient(YandexRequestMixin, YandexPollingMixin, YandexConversationMixin, YandexMcpMixin, YandexFileManagerMixin):
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
