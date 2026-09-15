"""Unit tests for Yandex Conversation request metadata.

These tests replace the HTTP session, so CI never contacts the paid Yandex API.
"""
import unittest
from unittest.mock import Mock

from conversation_metadata import create_conversation_request


class TestConversationMetadataRequest(unittest.TestCase):
    def _client(self):
        client = Mock()
        client.conversations_url = "https://example.invalid/v1/conversations"
        client.session.post.return_value = Mock(
            status_code=200,
            url=client.conversations_url,
            json=lambda: {"id": "test-conversation"},
            raise_for_status=Mock(),
        )
        client._log_request = Mock()
        client._log_response = Mock(side_effect=lambda response: response)
        return client

    def test_metadata_is_sent_in_documented_body(self):
        client = self._client()

        result = create_conversation_request(
            client,
            metadata={"test_request": "true"},
        )

        self.assertEqual(result["id"], "test-conversation")
        client.session.post.assert_called_once_with(
            client.conversations_url,
            json={
                "metadata": {"test_request": "true"},
                "items": [],
            },
            timeout=10,
        )

    def test_default_metadata_is_empty(self):
        client = self._client()

        create_conversation_request(client)

        client.session.post.assert_called_once_with(
            client.conversations_url,
            json={"metadata": {}, "items": []},
            timeout=10,
        )


if __name__ == "__main__":
    unittest.main()
