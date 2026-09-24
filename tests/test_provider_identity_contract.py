import pytest
from unittest.mock import Mock

from config import Config
from yandex_client_modules.conversations import YandexConversationMixin
from yandex_client_modules.errors import YandexClientError


def test_config_has_no_yandex_project_id():
    assert not hasattr(Config, "PROJECT_ID")


class FakeConversationClient(YandexConversationMixin):
    conversations_url = "https://example.test/conversations"

    def __init__(self, payload):
        self.session = Mock()
        self.session.post.return_value = FakeResponse(payload)

    def _log_request(self, *args, **kwargs):
        pass

    def _log_response(self, response):
        return response


class FakeResponse:
    status_code = 200
    url = "https://example.test/conversations"

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_conversation_uses_provider_uuid_without_generating_local_id():
    provider_uuid = "550e8400-e29b-41d4-a716-446655440000"
    client = FakeConversationClient({"id": provider_uuid})

    result = client.create_conversation()

    assert result["id"] == provider_uuid
    client.session.post.assert_called_once()


def test_conversation_rejects_non_uuid_provider_id():
    client = FakeConversationClient({"id": "local-conversation-id"})

    with pytest.raises(YandexClientError, match="valid UUID"):
        client.create_conversation()


def test_conversation_resolver_rejects_non_provider_id():
    client = FakeConversationClient({"id": "unused"})

    with pytest.raises(YandexClientError, match="provider UUID"):
        client._resolve_yandex_conv_id("local-conversation-id")
