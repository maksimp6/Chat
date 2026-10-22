"""Offline safety controls for task-history upload and direct Vector Store retrieval."""

from __future__ import annotations

import hashlib
import json
import subprocess
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

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
    assert '"max_chunk_size_tokens": 800' in output
    assert '"chunk_overlap_tokens": 100' in output
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
    assert "store_id=vs-1 files=1 issues=1 repository_files=0" in captured.out
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


@pytest.fixture
def repository_snapshot(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    secret = "ghp_" + "x" * 36
    original = BODY + "\nExample value: " + secret + "\n"
    (root / "README.md").write_text(original, encoding="utf-8")
    (root / "asset.bin").write_bytes(b"\x00\xffbinary payload")
    (root / ".env").write_text("sensitive file must not be read", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("symlink target must not be indexed", encoding="utf-8")
    (root / "link").symlink_to(outside)
    subprocess.run(["git", "add", "--", "."], cwd=root, check=True)
    (root / ".env").unlink()
    (root / "untracked.txt").write_text("untracked content", encoding="utf-8")
    files, excluded = pipeline.read_repository_snapshot(str(root))
    return files, excluded, original, secret


def test_repository_snapshot_reads_tracked_text_and_preserves_safe_metadata(repository_snapshot):
    files, excluded, original, secret = repository_snapshot

    assert excluded == 1
    assert [item["path"] for item in files] == ["README.md", "asset.bin", "link"]
    readme, binary, link = files
    assert readme["text"] == original.replace(secret, "[REDACTED]")
    assert readme["redactions"] == 1
    assert readme["sha256"] == hashlib.sha256(original.encode()).hexdigest()
    assert readme["index_sha256"] == hashlib.sha256(readme["text"].encode()).hexdigest()
    assert readme["index_sha256"] != readme["sha256"]
    assert binary["kind"] == "binary" and binary["text"] is None
    assert link["kind"] == "symlink" and link["text"] is None


def test_repository_document_preserves_identity_without_exposing_omitted_data(repository_snapshot):
    files, excluded, original, secret = repository_snapshot
    fingerprint, content = pipeline.build_repository_vector_store_document(files, excluded)

    assert (fingerprint, content) == pipeline.build_repository_vector_store_document(
        files, excluded
    )
    document = content.decode("utf-8")
    assert BODY in document and "[REDACTED]" in document
    assert 'Repository path JSON: "README.md"' in document
    assert files[0]["sha256"] in document and files[0]["index_sha256"] in document
    assert "Credential-bearing files excluded before reading: 1" in document
    assert secret not in document and original not in document
    assert "binary payload" not in document
    assert "symlink target must not be indexed" not in document
    assert "untracked content" not in document
    assert "sensitive file must not be read" not in document


@pytest.fixture
def vector_model_transport(monkeypatch):
    calls = []
    reply = {
        "answer": "Проверка офиса описана в журнале.",
        "refusal": False,
        "citations": [{"id": 1}],
    }

    class Response:
        status = 200

        @staticmethod
        def read(limit):
            assert limit == pipeline.MAX_RESPONSE_BYTES + 1
            return json.dumps(
                {
                    "id": "offline-response-1",
                    "status": "completed",
                    "usage": {"input_tokens": 123, "output_tokens": 45},
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": json.dumps(reply)}],
                        }
                    ],
                }
            ).encode()

    class Connection:
        def __init__(self, host, timeout):
            assert host == "ai.api.cloud.yandex.net" and timeout == 90

        @staticmethod
        def request(method, path, *, body, headers):
            calls.append((method, path, json.loads(body)))

        @staticmethod
        def getresponse():
            return Response()

        @staticmethod
        def close():
            pass

    monkeypatch.setattr(pipeline.http.client, "HTTPSConnection", Connection)
    return calls, reply


def test_vector_model_derives_exact_citations_and_preserves_unknown_semantics(
    vector_model_transport,
):
    calls, _ = vector_model_transport
    results = [{"file_id": "file-1", "score": 0.7, "content": BODY}]

    result, provider = pipeline.vector_store_model_result(
        "Как проверяют офис?", results, "file-1", [source()], None, {"file-1"}, "project", "key"
    )

    assert len(calls) == 1
    method, path, payload = calls[0]
    assert (method, path) == ("POST", "/v1/responses")
    assert payload["background"] is False and payload["store"] is False
    assert payload["max_output_tokens"] == 4096
    assert payload["text"]["format"]["schema"] == pipeline.VECTOR_STORE_RESPONSE_SCHEMA
    assert json.loads(payload["input"])["sources"][0]["text"] == BODY
    assert result["status"] == "CITATIONS_ONLY"
    assert result["semantic_entailment"] == "NOT_ASSESSED"
    assert result["citations"][0]["quote"] == BODY
    assert result["citations"][0]["source_match"]["verbatim"] is True
    assert provider["request_id"] == "offline-response-1"
    assert provider["usage"] == {"input_tokens": 123, "output_tokens": 45}


@pytest.mark.parametrize(
    "citations,error",
    [
        ([{"id": 2}], "unknown_candidate_id"),
        ([{"id": True}], "citation_types"),
        ([{"id": 1}, {"id": 1}], "duplicate_candidate_id"),
        ([{"id": 1, "quote": "invented quote"}], "citation_fields"),
    ],
)
def test_vector_model_rejects_invalid_selected_evidence(vector_model_transport, citations, error):
    calls, reply = vector_model_transport
    reply["citations"] = citations
    results = [{"file_id": "file-1", "content": BODY}]

    with pytest.raises(ValueError, match=error):
        pipeline.vector_store_model_result(
            "query", results, "file-1", [source()], None, {"file-1"}, "project", "key"
        )

    assert len(calls) == 1


def test_vector_model_accepts_refusal_after_one_completed_call(vector_model_transport):
    calls, reply = vector_model_transport
    reply.update({"answer": "", "refusal": True, "citations": []})

    result, _ = pipeline.vector_store_model_result(
        "query", [{"content": BODY}], "file-1", [source()], None, {"file-1"}, "project", "key"
    )

    assert len(calls) == 1
    assert result["status"] == "MODEL_REFUSAL" and result["model_called"] is True
    assert result["citations"] == []


def test_vector_model_rejects_foreign_file_before_call(vector_model_transport):
    calls, _ = vector_model_transport

    with pytest.raises(ValueError, match="vector_store_search_file_mismatch"):
        pipeline.vector_store_model_result(
            "query",
            [{"file_id": "foreign-file", "content": BODY}],
            "file-1",
            [source()],
            None,
            {"file-1"},
            "project",
            "key",
        )

    assert calls == []


@pytest.fixture
def vector_demo_api(monkeypatch, tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / "README.md").write_text(BODY, encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("YANDEX_API_KEY", "offline-key")
    monkeypatch.setenv("YANDEX_PROJECT_ID", "offline-project")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    issue = {"number": 9001, "title": TITLE, "body": BODY, "state": "closed"}
    monkeypatch.setattr(
        pipeline,
        "api_get",
        lambda path, token=None: [issue] if parse_qs(urlsplit(path).query)["page"] == ["1"] else [],
    )
    state = {"files": [], "attached": [], "calls": [], "fail_repository_upload": False}

    def upload_file(body, content_type):
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
        )
        uploaded = next(part for part in message.iter_parts() if part.get_filename())
        filename = uploaded.get_filename()
        if state["fail_repository_upload"] and filename.startswith("chat-repository-"):
            raise OSError("yandex_http_503")
        metadata = {
            "id": f"file-{len(state['files']) + 1}",
            "filename": filename,
            "bytes": len(uploaded.get_payload(decode=True)),
        }
        state["files"].append(metadata)
        return metadata

    def request(method, path, project, key, **kwargs):
        state["calls"].append((method, path))
        route = path.split("?", 1)[0]
        if method == "GET":
            return json.dumps(
                {
                    "/vector_stores": {"data": []},
                    "/files": {"data": state["files"]},
                    "/vector_stores/vs-1": {
                        "id": "vs-1",
                        "name": "existing",
                        "status": "completed",
                    },
                    "/vector_stores/vs-1/files": {"data": state["attached"]},
                }[route]
            ).encode()
        if route == "/files":
            return json.dumps(upload_file(kwargs["body"], kwargs["content_type"])).encode()
        payload = json.loads(kwargs["body"])
        if route == "/vector_stores":
            return json.dumps({"id": "vs-1", "name": payload["name"]}).encode()
        if route == "/vector_stores/vs-1/files":
            state["attached"].append({**payload, "status": "completed"})
            return json.dumps(payload).encode()
        assert route == "/vector_stores/vs-1/search"
        assert payload["query"] == "офис" and payload["max_num_results"] == 50
        return json.dumps({"data": []}).encode()

    monkeypatch.setattr(pipeline, "yandex_api_request", request)
    return state


def test_repository_vector_demo_uploads_and_indexes_two_files_before_one_search(
    vector_demo_api, capsys
):
    state = vector_demo_api

    assert (
        pipeline.main(
            [
                "--mode",
                "vector-store",
                "--query",
                "офис",
                "--include-repository",
                "--allow-vector-store-write",
                "--approval-reference",
                "offline-owner-approval",
            ]
        )
        == 0
    )

    assert len(state["files"]) == 2
    assert state["files"][0]["filename"].startswith("chat-task-history-")
    assert state["files"][1]["filename"].startswith("chat-repository-")
    assert [item["file_id"] for item in state["attached"]] == ["file-1", "file-2"]
    posts = [path for method, path in state["calls"] if method == "POST"]
    assert posts == [
        "/vector_stores",
        "/files",
        "/files",
        "/vector_stores/vs-1/files",
        "/vector_stores/vs-1/files",
        "/vector_stores/vs-1/search",
    ]
    assert state["calls"].count(("GET", "/vector_stores/vs-1")) == 2
    output = capsys.readouterr().out
    assert "files=2 issues=1 repository_files=1" in output
    assert '"model_called": false' in output


def test_existing_repository_vector_demo_reads_pair_without_upload_or_attach(
    vector_demo_api, capsys
):
    state = vector_demo_api
    state["files"] = [
        {"id": "file-1", "filename": "chat-task-history-existing.md", "bytes": 12},
        {"id": "file-2", "filename": "chat-repository-existing.md", "bytes": 20},
    ]
    state["attached"] = [
        {"file_id": "file-2", "status": "completed"},
        {"file_id": "file-1", "status": "completed"},
    ]

    assert (
        pipeline.main(
            [
                "--mode",
                "vector-store",
                "--query",
                "офис",
                "--include-repository",
                "--existing-store-id",
                "vs-1",
            ]
        )
        == 0
    )

    assert len(state["calls"]) == 8
    assert [call for call in state["calls"] if call[0] != "GET"] == [
        ("POST", "/vector_stores/vs-1/search")
    ]
    output = capsys.readouterr().out
    assert '"file_id": "file-2"' in output
    assert '"file_name": "chat-repository-existing.md"' in output
    assert "files=2 issues=1 repository_files=1" in output


def test_repository_upload_failure_reports_stage_without_retry(vector_demo_api, capsys):
    state = vector_demo_api
    state["fail_repository_upload"] = True

    assert (
        pipeline.main(
            [
                "--mode",
                "vector-store",
                "--query",
                "офис",
                "--include-repository",
                "--allow-vector-store-write",
                "--approval-reference",
                "offline-owner-approval",
            ]
        )
        == 3
    )

    assert state["calls"].count(("POST", "/files")) == 2
    assert len(state["files"]) == 1 and state["attached"] == []
    assert not any(path.endswith("/search") for _, path in state["calls"])
    captured = capsys.readouterr()
    assert "stage=ensure_repository_snapshot_file error=HTTP_503" in captured.err
    assert "Yandex AI Studio Vector Store demo" not in captured.out


def test_repository_citation_offsets_match_trimmed_quote():
    text = "  \n" + BODY + "  \n"
    repository_files = [
        {
            "path": "README.md",
            "kind": "text",
            "text": text,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "redactions": 0,
        }
    ]
    item = {"content": 'Repository path JSON: "README.md"\nContent:\n' + text}

    evidence = pipeline.verify_repository_vector_result(item, repository_files)

    assert evidence["verbatim"] is True
    assert evidence["quote"] == BODY
    assert text[evidence["source_start"] : evidence["source_end"]] == evidence["quote"]
