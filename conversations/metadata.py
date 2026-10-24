"""Helpers for creating Yandex Conversations without coupling tests to network calls."""

from __future__ import annotations

import requests


def create_conversation_request(client, metadata=None):
    """Create a Yandex Conversation using the documented request body.

    The helper deliberately accepts a client instance so tests can replace its
    HTTP session with a fake session. It never makes a request unless called.
    """
    payload = {
        "metadata": dict(metadata or {}),
        "items": [],
    }
    client._log_request("POST", client.conversations_url, json=payload)
    response = client._log_response(
        client.session.post(client.conversations_url, json=payload, timeout=10)
    )
    response.raise_for_status()
    return response.json()
