import logging
import requests

from yandex_client_modules.errors import YandexClientError

api_logger = logging.getLogger("yandex_api_debug")


class YandexConversationMixin:
    def create_conversation(self, execution_trace=None):
        try:
            if execution_trace is not None:
                execution_trace.add_event("conversation_create_started", {
                    "method": "POST",
                    "url": self.conversations_url,
                })
            self._log_request("POST", self.conversations_url, json={})
            resp = self._log_response(
                self.session.post(self.conversations_url, json={}, timeout=10)
            )
            resp.raise_for_status()
            data = resp.json()
            yandex_id = data.get("id") if isinstance(data, dict) else None
            if not yandex_id:
                raise YandexClientError("Create conv: Yandex response has no conversation id")
            api_logger.info(f"[CONV] Created: id={yandex_id}")
            if execution_trace is not None:
                execution_trace.add_event("conversation_created", {
                    "provider": "yandex",
                    "conversation_id": yandex_id,
                    "status_code": resp.status_code,
                })
            return data
        except requests.RequestException as e:
            if execution_trace is not None:
                execution_trace.record_error("conversation_create", str(e), exception=e)
            raise YandexClientError("Create conv: " + str(e))

    def _resolve_yandex_conv_id(self, conv_id):
        if not conv_id:
            return None
        return str(conv_id)
