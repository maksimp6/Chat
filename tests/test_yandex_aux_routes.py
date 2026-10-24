"""Offline HTTP contracts for auxiliary Yandex routes; never call a provider."""

from types import SimpleNamespace

from flask import Flask
import pytest
import requests

import chat.completion_routes as completion
import chat.embedding_routes as embeddings


class Response:
    def __init__(self, payload, status=200):
        self.status_code, self.payload = status, payload

    def json(self):
        return self.payload


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        requests.sessions.Session, "request",
        lambda *args, **kwargs: pytest.fail("auxiliary route test attempted live HTTP"),
    )
    monkeypatch.setattr(
        requests, "post",
        lambda *args, **kwargs: pytest.fail("unexpected provider call"),
    )
    app = Flask(__name__)
    app.testing = True
    app.register_blueprint(embeddings.embedding_bp)
    app.register_blueprint(completion.completion_bp)
    return app.test_client()


@pytest.fixture
def active_credential(monkeypatch):
    store, calls = object(), []
    credential = SimpleNamespace(api_key="synthetic-db-key", project_id="synthetic-db-project")
    for module in (embeddings, completion):
        monkeypatch.setattr(module, "get_store", lambda: store)
        def resolve(db, decrypt, *, provider):
            assert db is store and provider == "yandex"
            calls.append(provider)
            return credential
        monkeypatch.setattr(module, "get_active_credential", resolve)
    # A stale environment credential must never override the configured active record.
    monkeypatch.setenv("YANDEX_API_KEY", "synthetic-stale-env-key")
    monkeypatch.setenv("YANDEX_PROJECT_ID", "synthetic-stale-env-project")
    return credential, calls


@pytest.mark.parametrize("mode", [[], {}, None, True, 7])
def test_invalid_embedding_mode_is_bad_request_without_provider_call(client, mode):
    response = client.post("/api/embeddings", json={"mode": mode, "text": "fixture"})
    assert response.status_code == 400


@pytest.mark.parametrize("role", [[], {}, None, True, 7])
def test_invalid_completion_role_is_bad_request_without_provider_call(client, role):
    response = client.post("/api/completion", json={"messages": [{"role": role, "text": "fixture"}]})
    assert response.status_code == 400


@pytest.mark.parametrize("path,payload", [
    ("/api/embeddings", {"text": "fixture"}),
    ("/api/completion", {"message": "fixture"}),
])
def test_missing_active_credential_fails_before_provider_http(client, monkeypatch, path, payload):
    def missing(*args, **kwargs):
        raise embeddings.CredentialError("synthetic private diagnostic")
    for module in (embeddings, completion):
        monkeypatch.setattr(module, "get_store", lambda: object())
        monkeypatch.setattr(module, "get_active_credential", missing)
    response = client.post(path, json=payload)
    assert response.status_code == 503
    assert "synthetic private" not in response.get_data(as_text=True)


def test_routes_use_real_saved_active_credential_without_environment_keys(client, monkeypatch, tmp_path):
    from agent_memory.client import close_memory_clients
    from provider_accounts.credentials import bootstrap_credential
    from storage.db import get_store

    monkeypatch.setenv("LOGOS_MEMORY_PATH", str(tmp_path / "logos.memory"))
    monkeypatch.delenv("YANDEX_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_PROJECT_ID", raising=False)
    bootstrap_credential(get_store(), "synthetic-saved-key", "saved-project", lambda value: value)
    calls = []
    def post(url, *, json, headers, timeout):
        assert headers == {"Authorization": "Api-Key synthetic-saved-key"}
        calls.append((url, json, timeout))
        if url == embeddings.UPSTREAM_URL:
            return Response({"embedding": [0.1, -0.2], "numTokens": "3"})
        return Response({"result": {"alternatives": [{"message": {"text": "offline reply"}}], "usage": {"totalTokens": "4"}}})
    monkeypatch.setattr(requests, "post", post)
    try:
        first = client.post("/api/embeddings", json={"text": "fixture"})
        second = client.post("/api/completion", json={"message": "fixture", "max_tokens": 8})
        assert first.status_code == second.status_code == 200
        assert first.get_json()["model"] == "emb://saved-project/text-search-doc"
        assert second.get_json()["model"] == "gpt://saved-project/yandexgpt-5.1"
        assert second.get_json()["transport"] == "completion"
        assert calls[1][1]["completionOptions"]["maxTokens"] == "8"
        assert "synthetic-saved-key" not in first.get_data(as_text=True) + second.get_data(as_text=True)
    finally:
        close_memory_clients()


@pytest.mark.parametrize("vector", [[], [float("nan")], [float("inf")], [True], ["0.1"], None])
def test_malformed_embedding_does_not_return_success_or_lose_known_token_count(
    client, active_credential, monkeypatch, vector
):
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: Response({"embedding": vector, "numTokens": 5}))
    response = client.post("/api/embeddings", json={"text": "fixture"})
    assert response.status_code == 502
    assert response.get_json()["completed"] == 0
    assert response.get_json()["tokens_spent"] == 5


def test_dimension_mismatch_preserves_batch_progress_and_spent_tokens(client, active_credential, monkeypatch):
    results = iter([Response({"embedding": [0.1, 0.2], "numTokens": 2}), Response({"embedding": [0.3], "numTokens": 3})])
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: next(results))
    response = client.post("/api/embeddings", json={"texts": ["first", "second"]})
    assert response.status_code == 502
    assert response.get_json()["completed"] == 1
    assert response.get_json()["tokens_spent"] == 5


def test_each_embedding_timeout_is_limited_by_remaining_batch_budget(client, active_credential, monkeypatch):
    clock, calls = SimpleNamespace(value=0.0), []
    monkeypatch.setattr(embeddings, "time", SimpleNamespace(monotonic=lambda: clock.value))
    def post(url, *, json, headers, timeout):
        calls.append(timeout)
        clock.value += 55
        return Response({"embedding": [0.1], "numTokens": 2})
    monkeypatch.setattr(requests, "post", post)
    response = client.post("/api/embeddings", json={"texts": ["first", "second", "third"]})
    assert calls == [30, 5]
    assert response.status_code == 504
    assert response.get_json()["completed"] == 2
    assert response.get_json()["tokens_spent"] == 4


@pytest.mark.parametrize("path,payload", [
    ("/api/embeddings", {"text": "fixture"}),
    ("/api/completion", {"message": "fixture"}),
])
def test_provider_errors_never_echo_request_diagnostics(client, active_credential, monkeypatch, path, payload):
    def unavailable(*args, **kwargs):
        raise requests.RequestException("synthetic-db-key private upstream content")
    monkeypatch.setattr(requests, "post", unavailable)
    response = client.post(path, json=payload)
    assert response.status_code == 502
    assert "synthetic-db-key" not in response.get_data(as_text=True)
    assert "private upstream" not in response.get_data(as_text=True)
