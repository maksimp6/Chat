import logging
import uuid
import requests

from yandex_client_modules.errors import YandexClientError

api_logger = logging.getLogger("yandex_api_debug")


class YandexConversationMixin:
    def create_conversation(self, execution_trace=None):
        try:
            if execution_trace is not None:
                from yandex_client_modules.request_mixin import _resolve_global_provider_credential
                _resolve_global_provider_credential(self, execution_trace)

            self._log_request("POST", self.conversations_url, json={})
            resp = self._log_response(
                self.session.post(self.conversations_url, json={}, timeout=10)
            )
            resp.raise_for_status()
            data = resp.json()
            provider_id = data.get("id") if isinstance(data, dict) else None
            if not isinstance(provider_id, str) or not provider_id.strip():
                raise YandexClientError("Yandex conversation creation returned no provider ID")
            try:
                provider_id = str(uuid.UUID(provider_id))
            except (ValueError, AttributeError) as exc:
                raise YandexClientError("Yandex conversation ID is not a valid UUID") from exc
            data["id"] = provider_id
            if execution_trace is not None:
                execution_trace.add_event("conversation_created", {
                    "provider_conversation_id": provider_id,
                })
            api_logger.info("[CONV] Created: id=%s", provider_id)
            return data
        except requests.RequestException as exc:
            raise YandexClientError("Create conversation: " + str(exc)) from exc

    def _resolve_yandex_conv_id(self, conv_id):
        if not conv_id:
            return None
        try:
            return str(uuid.UUID(str(conv_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise YandexClientError("Conversation ID must be the Yandex provider UUID") from exc
