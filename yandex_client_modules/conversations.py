import logging
import requests

from yandex_client_modules.errors import YandexClientError

import db as database

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
                execution_trace.record_error(
                    "conversation_create",
                    str(e),
                    exception=e,
                )
            raise YandexClientError("Create conv: " + str(e))

    def _resolve_yandex_conv_id(self, conv_id):
        if not conv_id:
            return None

        try:
            conn = database.get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT yandex_id FROM conv_yandex_map WHERE local_id = ?",
                (str(conv_id),),
            )
            row = cur.fetchone()
            conn.close()
            if row:
                return row[0]
        except Exception as e:
            api_logger.error(f"[CONV_MAP] lookup failed for {conv_id}: {e}")
            return None

        # Legacy conversations used the Yandex UUID directly as local ID.
        # Keep that compatibility path, but never manufacture a new UUID.
        if isinstance(conv_id, str) and conv_id.strip():
            import re
            if re.fullmatch(r"[0-9a-fA-F-]{36}", conv_id.strip()):
                api_logger.info(f"[CONV_MAP] legacy direct Yandex conversation id: {conv_id}")
                return conv_id

        try:
            conn = database.get_conn()
            cur = conn.cursor()
            y_conv = self.create_conversation()
            y_id = y_conv.get("id")
            cur.execute(
                "INSERT INTO conv_yandex_map "
                "(local_id, yandex_id) VALUES (?, ?) "
                "ON CONFLICT(local_id) DO UPDATE SET yandex_id = excluded.yandex_id",
                (conv_id, y_id),
            )
            conn.commit()
            conn.close()
            return y_id
        except Exception as e:
            api_logger.error(f"[CONV_MAP] Ошибка: {e}")
            return None
