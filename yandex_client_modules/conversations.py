import logging
import uuid
import requests

from db import get_conn

from yandex_client_modules.errors import YandexClientError

api_logger = logging.getLogger("yandex_api_debug")


class YandexConversationMixin:
    def create_conversation(self):
        try:
            self._log_request("POST", self.conversations_url, json={})
            resp = self._log_response(
                self.session.post(self.conversations_url, json={}, timeout=10)
            )
            resp.raise_for_status()
            data = resp.json()
            api_logger.info(f"[CONV] Created: id={data.get('id')}")
            return data
        except requests.RequestException as e:
            raise YandexClientError("Create conv: " + str(e))

    def _resolve_yandex_conv_id(self, conv_id):
        if not conv_id:
            return None

        try:
            return str(uuid.UUID(str(conv_id)))
        except (ValueError, TypeError, AttributeError):
            pass

        try:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS conv_yandex_map (
                    local_id TEXT PRIMARY KEY,
                    yandex_id TEXT NOT NULL
                )
            """)
            cur.execute(
                "SELECT yandex_id FROM conv_yandex_map WHERE local_id = ?",
                (conv_id,),
            )
            row = cur.fetchone()
            if row:
                conn.close()
                return row[0]

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
