"""Read-only provider failure diagnostics retain codes without private messages."""

import json
from types import SimpleNamespace

import pytest

from scripts import repository_rag as rag


def test_inventory_failure_codes_and_missing_detail_are_read_only(monkeypatch):
    prefix = "chat-repository-archive-v1-" + rag.digest(b"owner/repo")[:16] + "-"
    files = [
        {"id": name, "filename": prefix + name + ".md", "bytes": 1}
        for name in ["known", "missing", "unsafe"]
    ]
    rows = [
        {
            "id": "known",
            "status": "failed",
            "last_error": {"code": "invalid_file", "message": "private-document-token"},
        },
        {"id": "missing", "status": "failed"},
        {
            "id": "unsafe",
            "status": "failed",
            "last_error": {"code": "private_document_token", "message": "private-document-token"},
        },
        {"id": "foreign", "status": "failed"},
    ]
    calls = []
    monkeypatch.setattr(rag, "_validate_store", lambda *args: "/vector_stores/store")
    monkeypatch.setattr(
        rag.semantic, "list_yandex_objects", lambda path, *args: files if path == "/files" else rows
    )

    def read(method, path, *args):
        calls.append((method, path))
        assert method == "GET" and path == "/vector_stores/store/files/missing"
        return {
            "id": "missing",
            "status": "failed",
            "last_error": {"code": "unsupported_file", "message": "private-document-token"},
        }

    monkeypatch.setattr(rag.semantic, "yandex_json_request", read)
    result = rag.provider_inventory("owner/repo", "store", "project", "key")
    attached = {row["file_id"]: row for row in result["attachments"]}
    assert attached["known"]["last_error"] == {"code": "invalid_file"}
    assert attached["missing"]["last_error"] == {"code": "unsupported_file"}
    assert attached["unsafe"]["last_error_diagnostic"] == "unrecognized_code"
    assert len(calls) == 1 and len(attached) == 3
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize(
    "code", ["secret with spaces", "ghp_" + "a" * 40, {"secret": "value"}, True, 999]
)
def test_unrecognized_error_codes_are_omitted_without_detail_retry(monkeypatch, code):
    monkeypatch.setattr(
        rag.semantic, "yandex_json_request", lambda *args: pytest.fail("unexpected GET")
    )
    rows = rag._attachment_states(
        [{"id": "a", "status": "failed", "last_error": {"code": code, "message": "private"}}],
        "/vector_stores/store",
        "p",
        "k",
    )
    assert rows == [
        {"file_id": "a", "status": "failed", "last_error_diagnostic": "unrecognized_code"}
    ]


def test_terminal_index_checkpoint_and_progress_omit_provider_messages(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        rag.semantic,
        "list_yandex_objects",
        lambda *args: [
            {
                "id": "a",
                "status": "failed",
                "last_error": {"code": "server_error", "message": "private-source-and-token"},
            }
        ],
    )
    monkeypatch.setattr(
        rag.semantic,
        "yandex_json_request",
        lambda *args: pytest.fail("unexpected provider request"),
    )
    checkpoint = tmp_path / "index.json"
    with pytest.raises(ValueError, match="vector_store_index_failed"):
        rag._attach_and_wait(
            "/vector_stores/store", [{"file_id": "a"}], {"a"}, "p", "k", checkpoint_path=checkpoint
        )
    saved = checkpoint.read_text()
    assert json.loads(saved)["files"] == {"a": "failed"}
    assert json.loads(saved)["progress"]["terminal_errors"][0]["last_error"] == {
        "code": "server_error"
    }
    output = capsys.readouterr().out
    assert "server_error" in output
    assert "private" not in saved + output


def test_detail_lookup_is_bounded_and_failure_keeps_listing(monkeypatch):
    monkeypatch.setattr(rag, "INDEX_DIAGNOSTIC_LIMIT", 1)
    calls = []

    def denied(method, path, *args):
        calls.append((method, path))
        raise rag.semantic.YandexHTTPError(403, method, path)

    monkeypatch.setattr(rag.semantic, "yandex_json_request", denied)
    rows = rag._attachment_states(
        [{"id": "a", "status": "failed"}, {"id": "b", "status": "expired"}],
        "/vector_stores/store",
        "p",
        "k",
    )
    assert len(calls) == 1 and calls[0][0] == "GET"
    assert rows[0]["last_error_lookup"] == "http_403"
    assert rows[1]["last_error_lookup"] == "read_limit_reached"
    assert {row["status"] for row in rows} == {"failed", "expired"}


@pytest.mark.parametrize("limit", [64, 2 * rag.semantic.MAX_VECTOR_FILE_BYTES])
def test_read_pack_propagates_remaining_download_bound(monkeypatch, limit):
    raw = rag.make_pack("owner/repo", [b"source"])
    calls = []

    def read(method, path, *args, **kwargs):
        calls.append((method, path, kwargs["max_response_bytes"]))
        return raw

    monkeypatch.setattr(rag.semantic, "yandex_api_request", read)
    rag._read_pack(
        SimpleNamespace(repository="owner/repo"),
        {"id": "a", "filename": "prefix-" + rag.digest(raw) + ".md"},
        "prefix-",
        "p",
        "k",
        max_response_bytes=limit,
    )
    assert calls == [("GET", "/files/a/content", min(limit, rag.semantic.MAX_VECTOR_FILE_BYTES))]
