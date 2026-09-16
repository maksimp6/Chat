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
from yandex_api_logger import api_logger

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
        from yandex_client_modules.parsers import extract_reasoning_and_text
        return extract_reasoning_and_text(data)

    @staticmethod
    def extract_text(data):
        from yandex_client_modules.parsers import extract_text
        return extract_text(data)
