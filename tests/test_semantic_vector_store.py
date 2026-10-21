"""Offline safety controls for task-history upload and direct Vector Store retrieval."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts import semantic_pipeline as pipeline

TITLE = "Офис агентов"
BODY = "Симуляция офиса проверяется по журналу событий и результатам запуска."


def source():
    return {
        "issue": 9001,
        "title": TITLE,
        "text": BODY,
        "corpus_kind": "tasks",
        "sha256": hashlib.sha256(BODY.encode()).hexdigest(),
        "title_sha256": hashlib.sha256(TITLE.encode()).hexdigest(),
        "body_sha256": hashlib.sha256(BODY.encode()).hexdigest(),
        "body_offset": 0,
        "url": "https://github.com/maksimp6/Chat/issues/9001",
        "state": "closed",
        "state_reason": "completed",
        "closed_at": "2026-10-20T10:00:00Z",
        "updated_at": "2026-10-20T10:00:00Z",
        "retrieved_at": "2026-10-21T10:00:00+00:00",
    }


def test_vector_document_is_deterministic_and_preserves_issue_provenance():
    corpus = [source()]
    fingerprint, content = pipeline.build_vector_store_document(corpus)
    repeated_fingerprint, repeated_content = pipeline.build_vector_store_document(corpus)

    assert fingerprint == repeated_fingerprint == pipeline.corpus_fingerprint(corpus)
    assert content == repeated_content
    document = content.decode("utf-8")
    assert "## Issue 9001" in document
    assert TITLE in document and BODY in document
    assert "state_reason" in document and "completed" in document
    assert "title_sha256" in document and "body_sha256" in document
    assert source()["url"] in document
    assert "Snapshot retrieved at: " + source()["retrieved_at"] in document


def test_vector_document_fails_closed_when_source_hash_does_not_match():
    row = source()
    row["text"] = BODY + " tampered"
    with pytest.raises(ValueError, match="source_hash_mismatch"):
        pipeline.build_vector_store_document([row])


def test_vector_search_excerpt_resolves_to_exact_body_and_offsets():
    row = source()
    excerpt = "журналу событий и результатам"
    evidence = pipeline.verify_vector_result(
        {"content": [{"type": "text", "text": "Перед запуском: " + excerpt}]}, [row]
    )

    assert evidence == {
        "issue": 9001,
        "title": TITLE,
        "url": row["url"],
        "field": "body",
        "quote": excerpt,
        "verbatim": True,
        "sha256": row["sha256"],
        "source_start": BODY.index(excerpt),
        "source_end": BODY.index(excerpt) + len(excerpt),
        "body_start": BODY.index(excerpt),
        "body_end": BODY.index(excerpt) + len(excerpt),
    }


def test_vector_search_does_not_guess_between_duplicate_source_quotes():
    first = source()
    second = source()
    second["issue"] = 9002
    second["url"] = "https://github.com/maksimp6/Chat/issues/9002"
    evidence = pipeline.verify_vector_result(
        {"content": [{"type": "text", "text": BODY}]}, [first, second]
    )

    assert evidence == {"verbatim": False, "reason": "ambiguous_source_match"}


def test_existing_named_file_is_reused_only_when_bytes_match(monkeypatch):
    filename = "chat-task-history-abc.md"
    content = b"task snapshot"
    monkeypatch.setattr(
        pipeline,
        "list_yandex_objects",
        lambda *args: [{"id": "file-1", "filename": filename, "bytes": len(content)}],
    )
    requests = []

    def request(method, path, project, key, **kwargs):
        requests.append((method, path))
        return content

    monkeypatch.setattr(pipeline, "yandex_api_request", request)
    file_id, meta = pipeline.ensure_yandex_file(filename, content, "project", "key")

    assert file_id == "file-1"
    assert meta["filename"] == filename
    assert requests == [("GET", "/files/file-1/content")]


def test_existing_named_file_mismatch_is_not_overwritten(monkeypatch):
    filename = "chat-task-history-abc.md"
    monkeypatch.setattr(
        pipeline,
        "list_yandex_objects",
        lambda *args: [{"id": "file-1", "filename": filename, "bytes": 7}],
    )
    monkeypatch.setattr(pipeline, "yandex_api_request", lambda *args, **kwargs: b"different")

    with pytest.raises(ValueError, match="existing_vector_store_file_mismatch"):
        pipeline.ensure_yandex_file(filename, b"snapshot", "project", "key")


def test_existing_snapshot_reuses_first_retrieval_time(monkeypatch):
    filename = "chat-task-history-abc.md"
    original = b"Corpus fingerprint: abc\nSnapshot retrieved at: first\nbody\n"
    retry = b"Corpus fingerprint: abc\nSnapshot retrieved at: second\nbody\n"
    monkeypatch.setattr(
        pipeline,
        "list_yandex_objects",
        lambda *args: [{"id": "file-1", "filename": filename}],
    )
    monkeypatch.setattr(pipeline, "yandex_api_request", lambda *args, **kwargs: original)

    file_id, _ = pipeline.ensure_yandex_file(filename, retry, "project", "key")

    assert file_id == "file-1"


def test_vector_store_api_uses_fixed_yandex_host_and_project_headers(monkeypatch):
    calls = []

    class Response:
        status = 200

        @staticmethod
        def read(limit):
            assert limit == pipeline.MAX_VECTOR_RESPONSE_BYTES + 1
            return b'{"id":"vs-1"}'

    class Connection:
        def __init__(self, host, timeout):
            calls.append(("connection", host, timeout))

        def request(self, method, path, *, body, headers):
            calls.append(("request", method, path, json.loads(body), headers))

        @staticmethod
        def getresponse():
            return Response()

        @staticmethod
        def close():
            calls.append(("close",))

    monkeypatch.setattr(pipeline.http.client, "HTTPSConnection", Connection)

    response = pipeline.yandex_api_request(
        "POST", "/vector_stores", "offline-project", "offline-key", body=b'{"name":"test"}'
    )

    assert response == b'{"id":"vs-1"}'
    assert calls[0] == ("connection", pipeline.HOST, 90)
    assert calls[1][1:4] == ("POST", "/v1/vector_stores", {"name": "test"})
    assert calls[1][4]["OpenAI-Project"] == "offline-project"
    assert calls[1][4]["x-yc-project-id"] == "offline-project"
    assert calls[1][4]["Authorization"] == "Api-Key offline-key"
    assert set(calls[1][4]) == {
        "Authorization",
        "Accept",
        "OpenAI-Project",
        "x-yc-project-id",
        "Content-Type",
    }
    assert calls[-1] == ("close",)


def test_yandex_http_error_omits_response_body_and_dynamic_ids(monkeypatch):
    class Response:
        status = 400

        @staticmethod
        def read(limit):
            return b"private provider response"

    class Connection:
        def __init__(self, host, timeout):
            pass

        @staticmethod
        def request(method, path, *, body, headers):
            pass

        @staticmethod
        def getresponse():
            return Response()

        @staticmethod
        def close():
            pass

    monkeypatch.setattr(pipeline.http.client, "HTTPSConnection", Connection)

    with pytest.raises(pipeline.YandexHTTPError) as raised:
        pipeline.yandex_api_request(
            "GET", "/files/private-file-id/content?cursor=private-cursor", "project", "key"
        )

    assert pipeline.technical_error_code(raised.value) == (
        "HTTP_400 method=GET path=/files/{file_id}/content"
    )
    assert "private" not in str(raised.value)


@pytest.mark.parametrize(
    "error,expected",
    [
        (OSError("yandex_http_403"), "HTTP_403"),
        (OSError("sensitive response body"), "OSERROR"),
        (TimeoutError("sensitive endpoint details"), "TIMEOUTERROR"),
    ],
)
def test_technical_error_code_exposes_only_safe_diagnostics(error, expected):
    assert pipeline.technical_error_code(error) == expected


@pytest.mark.parametrize(
    "method,path,safe_path",
    [
        ("GET", "/files?limit=100&after=private-cursor", "/files"),
        ("GET", "/files/file-private-id/content", "/files/{file_id}/content"),
        ("POST", "/files", "/files"),
        ("GET", "/vector_stores/store-private-id", "/vector_stores/{store_id}"),
    ],
)
def test_yandex_http_error_reports_method_and_sanitized_route(method, path, safe_path):
    error = pipeline.YandexHTTPError(400, method, path)

    assert pipeline.technical_error_code(error) == (f"HTTP_400 method={method} path={safe_path}")
    assert "private" not in str(error)


def test_provider_error_diagnostic_keeps_code_but_discards_message():
    raw = json.dumps(
        {"error": {"code": "invalid_file", "message": "private response contents"}}
    ).encode()
    code = pipeline._safe_yandex_error_code(raw)
    error = pipeline.YandexHTTPError(400, "POST", "/files", code)

    assert pipeline.technical_error_code(error) == (
        "HTTP_400 method=POST path=/files provider_code=invalid_file"
    )
    assert "private response contents" not in str(error)


def test_index_wait_polls_until_file_is_completed(monkeypatch):
    store_statuses = iter(["in_progress", "completed"])
    file_statuses = iter(["in_progress", "completed"])
    sleeps = []

    def yandex_json(method, path, project, key, payload=None):
        assert method == "GET"
        return {"status": next(store_statuses)}

    def list_objects(path, project, key, collection_keys):
        return [{"file_id": "file-1", "status": next(file_statuses)}]

    monkeypatch.setattr(pipeline, "yandex_json_request", yandex_json)
    monkeypatch.setattr(pipeline, "list_yandex_objects", list_objects)
    monkeypatch.setattr(pipeline.time, "sleep", lambda seconds: sleeps.append(seconds))

    pipeline.wait_for_vector_store_index("vs-1", "file-1", "project", "key")

    assert sleeps == [pipeline.VECTOR_STORE_POLL_INTERVAL_SECONDS]


def test_file_attachment_reuses_existing_snapshot_without_second_write(monkeypatch):
    writes = []
    monkeypatch.setattr(
        pipeline,
        "list_yandex_objects",
        lambda *args: [
            {
                "file_id": "file-1",
                "id": "vsfile-1",
                "chunking_strategy": pipeline.vector_store_chunking_strategy(),
            }
        ],
    )
    monkeypatch.setattr(
        pipeline,
        "yandex_json_request",
        lambda *args, **kwargs: writes.append((args, kwargs)),
    )

    pipeline.ensure_file_attached("vs-1", "file-1", "project", "key")

    assert writes == []


def test_vector_store_creation_leaves_chunking_for_file_attachment(monkeypatch):
    requests = []
    monkeypatch.setattr(pipeline, "list_yandex_objects", lambda *args: [])

    def request(method, path, project, key, payload=None):
        requests.append((method, path, payload))
        return {"id": "vs-1", "name": "store"}

    monkeypatch.setattr(pipeline, "yandex_json_request", request)

    assert pipeline.ensure_vector_store("store", "project", "key") == "vs-1"
    assert requests == [
        (
            "POST",
            "/vector_stores",
            {"name": "store"},
        )
    ]


def test_vector_store_attachment_sets_and_confirms_documented_chunking(monkeypatch):
    listings = iter(
        [
            [],
            [
                {
                    "file_id": "file-1",
                    "id": "vsfile-1",
                    "chunking_strategy": pipeline.vector_store_chunking_strategy(),
                }
            ],
        ]
    )
    requests = []
    monkeypatch.setattr(pipeline, "list_yandex_objects", lambda *args: next(listings))

    def request(method, path, project, key, payload=None):
        requests.append((method, path, payload))
        return {"file_id": "file-1"}

    monkeypatch.setattr(pipeline, "yandex_json_request", request)

    pipeline.ensure_file_attached("vs-1", "file-1", "project", "key")

    assert requests == [
        (
            "POST",
            "/vector_stores/vs-1/files",
            {"file_id": "file-1", "chunking_strategy": pipeline.vector_store_chunking_strategy()},
        )
    ]


def test_vector_store_search_uses_max_recall_settings(monkeypatch):
    requests = []

    def request(method, path, project, key, payload=None):
        requests.append((method, path, payload))
        return {"data": [{"rank": rank} for rank in range(60)]}

    monkeypatch.setattr(pipeline, "yandex_json_request", request)

    results = pipeline.vector_store_search("vs-1", "query", "project", "key")

    assert len(results) == pipeline.MAX_VECTOR_SEARCH_RESULTS == 50
    assert requests == [
        (
            "POST",
            "/vector_stores/vs-1/search",
            {
                "query": "query",
                "rewrite_query": True,
                "max_num_results": 50,
                "ranking_options": {"ranker": "auto", "score_threshold": 0.0},
            },
        )
    ]


def test_vector_store_demo_uploads_once_then_shows_direct_retrieval(monkeypatch, tmp_path, capsys):
    row = source()
    calls = []
    monkeypatch.setattr(pipeline, "fetch_corpus", lambda *args: [row])
    monkeypatch.setattr(
        pipeline,
        "ensure_vector_store",
        lambda name, project, key: calls.append(("store", name)) or "vs-1",
    )
    monkeypatch.setattr(
        pipeline,
        "ensure_yandex_file",
        lambda name, content, project, key: (
            calls.append(("file", name, content)) or ("file-1", {"bytes": len(content)})
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "ensure_file_attached",
        lambda store, file, project, key: calls.append(("attach", store, file)),
    )
    monkeypatch.setattr(
        pipeline,
        "wait_for_vector_store_index",
        lambda store, file, project, key: calls.append(("indexed", store, file)),
    )

    def search(store_id, query, project, key):
        calls.append(("search", store_id, query))
        return [
            {
                "file_id": "file-1",
                "file_name": "chat-task-history.md",
                "score": 0.95,
                "content": [{"type": "text", "text": BODY}],
            }
        ]

    monkeypatch.setattr(pipeline, "vector_store_search", search)
    monkeypatch.setenv("YANDEX_API_KEY", "offline-key")
    monkeypatch.setenv("YANDEX_PROJECT_ID", "offline-project")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    args = SimpleNamespace(
        allow_vector_store_write=True,
        approval_reference="owner-confirmed-vector-store-demo",
        corpus="tasks",
        publish_issue=None,
        allow_model_call=False,
    )

    assert pipeline.run_vector_store_demo(args, "симуляция офиса") == 0
    output = capsys.readouterr().out
    assert "model_called" in output and "false" in output
    assert '"max_num_results": 50' in output
    assert '"max_chunk_size_tokens": 4096' in output
    assert "verbatim" in output and "https://github.com/maksimp6/Chat/issues/9001" in output
    assert [call[0] for call in calls] == ["store", "file", "attach", "indexed", "search"]
    assert calls[-1] == ("search", "vs-1", "симуляция офиса")
    assert "Yandex AI Studio Vector Store demo" in summary.read_text(encoding="utf-8")


def test_vector_store_demo_reports_safe_search_failure_after_indexing(monkeypatch, capsys):
    row = source()
    monkeypatch.setattr(pipeline, "fetch_corpus", lambda *args: [row])
    monkeypatch.setattr(pipeline, "ensure_vector_store", lambda *args: "vs-1")
    monkeypatch.setattr(
        pipeline,
        "ensure_yandex_file",
        lambda *args: ("file-1", {"bytes": 1}),
    )
    monkeypatch.setattr(pipeline, "ensure_file_attached", lambda *args: None)
    monkeypatch.setattr(pipeline, "wait_for_vector_store_index", lambda *args: None)
    monkeypatch.setattr(
        pipeline,
        "vector_store_search",
        lambda *args: (_ for _ in ()).throw(OSError("yandex_http_403")),
    )
    monkeypatch.setenv("YANDEX_API_KEY", "must-not-be-logged")
    monkeypatch.setenv("YANDEX_PROJECT_ID", "must-not-be-logged")
    args = SimpleNamespace(
        allow_vector_store_write=True,
        approval_reference="owner-confirmed-vector-store-demo",
        corpus="tasks",
        publish_issue=None,
        allow_model_call=False,
    )

    with pytest.raises(OSError):
        pipeline.run_vector_store_demo(args, "симуляция офиса")

    captured = capsys.readouterr()
    assert "store_id=vs-1 file_id=file-1" in captured.out
    assert "stage=direct_vector_search error=HTTP_403" in captured.err
    assert "must-not-be-logged" not in captured.out + captured.err


def test_vector_store_write_requires_explicit_approval_before_source_read(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "fetch_corpus",
        lambda *args: pytest.fail("must reject before reading or writing"),
    )
    args = SimpleNamespace(
        allow_vector_store_write=False,
        approval_reference="",
        corpus="tasks",
    )

    with pytest.raises(ValueError, match="vector_store_write_not_authorized"):
        pipeline.run_vector_store_demo(args, "симуляция офиса")


def test_multipart_file_contains_assistant_purpose_and_snapshot_content():
    body, content_type = pipeline._multipart_file("snapshot.md", b"all task data")

    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="purpose"' in body and b"assistants" in body
    assert b'filename="snapshot.md"' in body and b"all task data" in body
    assert b"Content-Type: text/markdown\r\n" in body
