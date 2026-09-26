import io
import re

import pytest
import requests

import file_manager
from yandex_client import YandexClientError


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", content=b"data"):
        self.status_code = status_code
        self.payload = {} if payload is None else payload
        self.text = text
        self.content = content

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, results=None, headers=None):
        self.results = list(results or [])
        self.headers = dict(headers or {})
        self.calls = []

    def request(self, method, url, timeout, **kwargs):
        self.calls.append((method, url, timeout, kwargs))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class Client(file_manager.YandexFileManagerMixin):
    def __init__(self, session):
        self.session = session
        self.base_url = "https://api.example.test/v1"
        self.logged_requests = []
        self.logged_responses = []

    def _log_request(self, method, url, **kwargs):
        self.logged_requests.append((method, url, kwargs))

    def _log_response(self, response):
        self.logged_responses.append(response)


class WrapperClient(file_manager.YandexFileManagerMixin):
    def __init__(self, response):
        self.base_url = "https://api.example.test/v1"
        self.response = response
        self.calls = []
        self.error_contexts = []

    def _fm_request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response

    def _fm_handle_error(self, response, context):
        self.error_contexts.append((response, context))
        raise YandexClientError(context)


def test_fm_request_resolves_auth_restores_multipart_header_and_logs(monkeypatch):
    resolved = []
    response = FakeResponse(payload={"ok": True})
    session = FakeSession(
        [response],
        headers={"Content-Type": "application/json", "X-Test": "1"},
    )
    client = Client(session)
    monkeypatch.setattr(
        file_manager,
        "_resolve_global_provider_credential",
        lambda instance: resolved.append(instance),
    )

    result = client._fm_request(
        "POST",
        "https://api.example.test/v1/files",
        files={"file": ("x.txt", io.BytesIO(b"x"))},
    )

    assert result is response
    assert resolved == [client]
    assert session.headers["Content-Type"] == "application/json"
    assert client.logged_requests[0][0] == "POST"
    assert client.logged_responses == [response]
    assert session.calls[0][2] == 30


def test_fm_request_retries_timeout_and_network_error_then_succeeds(monkeypatch):
    response = FakeResponse(payload={"ok": True})
    session = FakeSession(
        [
            requests.exceptions.Timeout(),
            requests.exceptions.RequestException("temporary"),
            response,
        ]
    )
    client = Client(session)
    sleeps = []
    monkeypatch.setattr(file_manager, "_resolve_global_provider_credential", lambda instance: None)
    monkeypatch.setattr(file_manager.time, "sleep", sleeps.append)

    assert client._fm_request("GET", "https://api.example.test/v1/files") is response
    assert sleeps == [0.5, 1.5]
    assert len(session.calls) == 3


def test_fm_request_raises_after_retries_and_restores_header(monkeypatch):
    session = FakeSession(
        [requests.exceptions.RequestException("offline") for _ in range(4)],
        headers={"Content-Type": "application/json"},
    )
    client = Client(session)
    monkeypatch.setattr(file_manager, "_resolve_global_provider_credential", lambda instance: None)
    monkeypatch.setattr(file_manager.time, "sleep", lambda delay: None)

    with pytest.raises(YandexClientError, match="Network error: offline"):
        client._fm_request(
            "POST",
            "https://api.example.test/v1/files",
            files={"file": ("x.txt", io.BytesIO(b"x"))},
        )

    assert session.headers["Content-Type"] == "application/json"
    assert len(session.calls) == 4


@pytest.mark.parametrize(
    ("status", "payload", "text", "expected"),
    [
        (404, {"error": {"message": "missing"}}, "", "Not found"),
        (413, {"error": {"message": "large"}}, "", "File too large"),
        (401, {"error": {"message": "auth"}}, "", "Auth failed (401)"),
        (403, {"error": {"message": "auth"}}, "", "Auth failed (403)"),
        (429, {"error": {"message": "slow down"}}, "", "Rate limit (429): slow down"),
        (500, {"error": {"message": "broken"}}, "", "failed (HTTP 500): broken"),
        (502, ["bad", "gateway"], "", "failed (HTTP 502): ['bad', 'gateway']"),
        (503, ValueError("not json"), "plain failure", "failed (HTTP 503): plain failure"),
        (504, ValueError("not json"), "", "failed (HTTP 504): unknown"),
    ],
)
def test_fm_handle_error_maps_http_failures(status, payload, text, expected):
    client = Client(FakeSession())
    response = FakeResponse(status_code=status, payload=payload, text=text)

    with pytest.raises(YandexClientError, match=re.escape(expected)):
        client._fm_handle_error(response, "Context")


def test_file_api_success_paths_include_optional_parameters():
    response = FakeResponse(payload={"id": "file-1", "bytes": 3}, content=b"abc")
    client = WrapperClient(response)

    assert client.list_files(limit=5) == response.payload
    assert client.calls[-1] == (
        "GET",
        "https://api.example.test/v1/files",
        {"params": {"limit": 5}},
    )

    client.list_files(limit=7, after="cursor")
    assert client.calls[-1][2]["params"] == {"limit": 7, "after": "cursor"}

    file_obj = io.BytesIO(b"abc")
    assert client.upload_file(file_obj, "x.txt") == response.payload
    method, url, kwargs = client.calls[-1]
    assert method == "POST"
    assert url.endswith("/files")
    assert kwargs["data"] == {"purpose": "assistants"}
    assert kwargs["files"]["file"][0] == "x.txt"

    client.upload_file(
        io.BytesIO(b"x"),
        "expiring.txt",
        purpose="fine-tune",
        expires_after={"anchor": "created_at", "seconds": 60},
    )
    assert client.calls[-1][2]["data"]["purpose"] == "fine-tune"
    assert '"seconds": 60' in client.calls[-1][2]["data"]["expires_after"]

    assert client.delete_file("file-1") == response.payload
    assert client.calls[-1][:2] == (
        "DELETE",
        "https://api.example.test/v1/files/file-1",
    )

    assert client.retrieve_file("file-1") == response.payload
    assert client.calls[-1][:2] == (
        "GET",
        "https://api.example.test/v1/files/file-1",
    )

    assert client.download_file("file-1") == b"abc"
    assert client.calls[-1][:2] == (
        "GET",
        "https://api.example.test/v1/files/file-1/content",
    )


def test_vector_store_success_paths_include_optional_parameters():
    response = FakeResponse(payload={"id": "vs-1"})
    client = WrapperClient(response)

    assert client.list_vector_stores(limit=4) == response.payload
    assert client.calls[-1] == (
        "GET",
        "https://api.example.test/v1/vector_stores",
        {"params": {"limit": 4}},
    )

    client.create_vector_store("empty")
    assert client.calls[-1][2]["json"] == {"name": "empty"}

    client.create_vector_store(
        "full",
        file_ids=["f1", "f2"],
        chunking_strategy={"type": "auto"},
        expires_after={"anchor": "last_active_at", "days": 7},
    )
    assert client.calls[-1][2]["json"] == {
        "name": "full",
        "file_ids": ["f1", "f2"],
        "chunking_strategy": {"type": "auto"},
        "expires_after": {"anchor": "last_active_at", "days": 7},
    }

    assert client.get_vector_store("vs-1") == response.payload
    assert client.calls[-1][:2] == (
        "GET",
        "https://api.example.test/v1/vector_stores/vs-1",
    )

    assert client.delete_vector_store("vs-1") == response.payload
    assert client.calls[-1][:2] == (
        "DELETE",
        "https://api.example.test/v1/vector_stores/vs-1",
    )

    client.list_vs_files("vs-1", limit=3)
    assert client.calls[-1][2]["params"] == {"limit": 3}

    client.list_vs_files("vs-1", limit=6, filter_status="completed")
    assert client.calls[-1][2]["params"] == {"limit": 6, "filter": "completed"}

    client.add_file_to_vs("vs-1", "f1")
    assert client.calls[-1][2]["json"] == {"file_id": "f1"}

    client.add_file_to_vs("vs-1", "f2", chunking_strategy={"type": "static"})
    assert client.calls[-1][2]["json"] == {
        "file_id": "f2",
        "chunking_strategy": {"type": "static"},
    }

    assert client.remove_file_from_vs("vs-1", "f1") == response.payload
    assert client.calls[-1][:2] == (
        "DELETE",
        "https://api.example.test/v1/vector_stores/vs-1/files/f1",
    )


@pytest.mark.parametrize(
    ("method_name", "args", "kwargs", "context"),
    [
        ("list_files", (), {}, "List files"),
        ("upload_file", (io.BytesIO(b"x"), "x.txt"), {}, "Upload file"),
        ("delete_file", ("f1",), {}, "Delete file"),
        ("retrieve_file", ("f1",), {}, "Retrieve file"),
        ("download_file", ("f1",), {}, "Download file"),
        ("list_vector_stores", (), {}, "List VS"),
        ("create_vector_store", ("name",), {}, "Create VS"),
        ("get_vector_store", ("vs1",), {}, "Get VS"),
        ("delete_vector_store", ("vs1",), {}, "Delete VS"),
        ("list_vs_files", ("vs1",), {}, "List VS files"),
        ("add_file_to_vs", ("vs1", "f1"), {}, "Add file to VS"),
        ("remove_file_from_vs", ("vs1", "f1"), {}, "Remove file from VS"),
    ],
)
def test_api_methods_delegate_http_errors(method_name, args, kwargs, context):
    response = FakeResponse(status_code=500, payload={"error": {"message": "broken"}})
    client = WrapperClient(response)

    with pytest.raises(YandexClientError, match=context):
        getattr(client, method_name)(*args, **kwargs)

    assert client.error_contexts[-1] == (response, context)
