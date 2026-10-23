"""Exercise native case indexing through real context and Vector Store helpers."""

import hashlib
import json
import subprocess
from email import policy
from email.parser import BytesParser
from urllib.parse import urlsplit

import pytest

from scripts import logos_context as context
from scripts import logos_knowledge as knowledge
from scripts import semantic_pipeline as semantic


@pytest.fixture
def packet_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in context.REQUIRED_FILES:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Complete guidance for " + name, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "context",
        ],
        cwd=repo,
        check=True,
    )

    def github(path):
        if "?" in path:
            return []
        return {
            "id": 1435 if "/pulls/" in path else 1413,
            "title": "Case",
            "body": "Unabridged project case",
            "head": {"sha": context._head(repo)},
            "base": {"sha": "b" * 40},
        }

    packet = context.build_context(
        repo,
        owner_context="Full owner dialogue " * 5000 + "END_OF_OWNER_DIALOGUE",
        get_json=github,
    )
    path = tmp_path / "context.json"
    path.write_text(json.dumps(packet), encoding="utf-8")
    return path, packet


@pytest.fixture
def provider(monkeypatch):
    store_id = knowledge.DEFAULT_STORE_ID
    state = {
        "name": "chat-repo-existing-snapshot-c800-o100",
        "files": {
            "old-code": {"filename": "chat-repository-old.md", "content": b"Old source"},
            "old-history": {"filename": "chat-task-history-old.md", "content": b"Old tasks"},
        },
        "attached": ["old-code", "old-history"],
        "index_status": "completed",
        "drop_old": False,
        "requests": [],
    }

    def api(method, path, project, key, *, body=None, content_type="application/json", **kwargs):
        state["requests"].append((method, path))
        assert project == "offline-project" and key == "offline-secret-key"
        route = urlsplit(path).path
        if method == "GET" and route == f"/vector_stores/{store_id}":
            data = {"id": store_id, "name": state["name"], "status": "completed"}
        elif method == "GET" and route == "/files":
            data = {
                "data": [
                    {"id": identifier, "filename": item["filename"], "bytes": len(item["content"])}
                    for identifier, item in state["files"].items()
                ]
            }
        elif method == "GET" and route == f"/vector_stores/{store_id}/files":
            data = {
                "data": [
                    {
                        "id": identifier,
                        **(
                            {"status": state["index_status"]}
                            if state["index_status"] is not None
                            else {}
                        ),
                    }
                    for identifier in state["attached"]
                ]
            }
        elif method == "GET" and route.startswith("/files/") and route.endswith("/content"):
            return state["files"][route.split("/")[2]]["content"]
        elif method == "POST" and route == "/files":
            message = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
            )
            parts = list(message.iter_parts())
            assert parts[0].get_content().strip() == "assistants"
            file_part = parts[1]
            content = file_part.get_payload(decode=True)
            state["files"]["new-context"] = {
                "filename": file_part.get_filename(),
                "content": content,
            }
            data = {
                "id": "new-context",
                "filename": file_part.get_filename(),
                "bytes": len(content),
            }
        elif method == "POST" and route == f"/vector_stores/{store_id}/files":
            payload = json.loads(body)
            assert payload["chunking_strategy"] == semantic.vector_store_chunking_strategy()
            if state["drop_old"]:
                state["attached"].clear()
            state["attached"].append(payload["file_id"])
            data = {"id": payload["file_id"]}
        else:
            pytest.fail(f"Unexpected transport operation {method} {path}")
        return json.dumps(data).encode()

    monkeypatch.setattr(semantic, "yandex_api_request", api)
    return state


def prepare(packet_file):
    return knowledge.prepare_knowledge(
        packet_file[0], project="offline-project", key="offline-secret-key"
    )


def test_full_context_is_indexed_in_existing_store_preserving_old_files(packet_file, provider):
    receipt = prepare(packet_file)
    document = provider["files"]["new-context"]["content"]
    assert document.endswith(b"\n") and len(document) > 65536
    assert "END_OF_OWNER_DIALOGUE" in document.decode()
    assert context.render_context(packet_file[1]) in document.decode()
    digest = hashlib.sha256(document).hexdigest()
    assert receipt["document_sha256"] == digest
    assert receipt["context_sha256"] == packet_file[1]["packet_sha256"]
    assert receipt["filename"] == f"chat-task-history-context-{digest}.md"
    assert receipt["status"] == "indexed"
    assert receipt["vector_store_ids"] == [knowledge.DEFAULT_STORE_ID]
    assert receipt["attached_files"] == ["new-context", "old-code", "old-history"]
    assert receipt["sources"] == len(packet_file[1]["sources"]) + 1
    assert receipt["bytes"] == len(document)
    assert receipt["generation_requests"] == 0
    assert "offline-secret-key" not in json.dumps(receipt)
    assert all(method in {"GET", "POST"} for method, _ in provider["requests"])
    assert ("POST", "/vector_stores") not in provider["requests"]
    assert all("responses" not in path for _, path in provider["requests"])


def test_same_context_reuses_identical_file_and_attachment_without_writes(packet_file, provider):
    first = prepare(packet_file)
    provider["requests"].clear()
    second = prepare(packet_file)
    assert first == second
    assert all(method == "GET" for method, _ in provider["requests"])
    assert ("GET", "/files/new-context/content") in provider["requests"]


def test_existing_file_with_different_content_is_not_overwritten(packet_file, provider):
    prepare(packet_file)
    provider["files"]["new-context"]["content"] = b"tampered"
    provider["requests"].clear()
    with pytest.raises(ValueError, match="existing_vector_store_file_mismatch"):
        prepare(packet_file)
    assert all(method == "GET" for method, _ in provider["requests"])


def test_context_integrity_failure_stops_before_any_provider_access(packet_file, provider):
    path, packet = packet_file
    packet["sources"][0]["text"] = "tampered"
    path.write_text(json.dumps(packet))
    with pytest.raises(context.ContextError, match="digest mismatch"):
        prepare(packet_file)
    assert provider["requests"] == []


def test_missing_or_foreign_project_store_is_not_recreated(packet_file, provider):
    provider["name"] = "unrelated-knowledge-store"
    with pytest.raises(ValueError, match="existing_project_store_invalid"):
        prepare(packet_file)
    assert provider["requests"] == [("GET", f"/vector_stores/{knowledge.DEFAULT_STORE_ID}")]


def test_loss_of_prior_attachments_blocks_success_receipt(packet_file, provider):
    provider["drop_old"] = True
    with pytest.raises(ValueError, match="attachment_receipt_invalid"):
        prepare(packet_file)


def test_index_failure_is_not_reported_as_available_rag(packet_file, provider):
    provider["index_status"] = "failed"
    with pytest.raises(ValueError, match="index_failed"):
        prepare(packet_file)


@pytest.mark.parametrize(
    "file_status,error_code",
    [
        (None, "knowledge_target_file_index_not_completed"),
        ("in_progress", "vector_store_index_timeout"),
        ("queued", "vector_store_index_timeout"),
        ("failed", "vector_store_index_failed"),
    ],
)
def test_completed_store_without_completed_target_file_cannot_emit_indexed_receipt(
    packet_file, provider, monkeypatch, tmp_path, capsys, file_status, error_code
):
    provider["index_status"] = file_status
    monkeypatch.setattr(semantic, "VECTOR_STORE_POLL_ATTEMPTS", 1)
    monkeypatch.setenv("YANDEX_PROJECT_ID", "offline-project")
    monkeypatch.setenv("YANDEX_API_KEY", "offline-secret-key")
    output = tmp_path / "knowledge.json"
    assert knowledge.main(["--context-file", str(packet_file[0]), "--out", str(output)]) == 1
    receipt = json.loads(output.read_text())
    assert receipt["status"] == "failed"
    assert receipt["error_code"] == error_code
    assert '"status": "indexed"' not in capsys.readouterr().out


@pytest.mark.parametrize("store_id", ["", "../store", "store?other=1", None])
def test_invalid_store_id_never_reaches_transport(packet_file, provider, store_id):
    with pytest.raises(ValueError, match="store_id_invalid"):
        knowledge.prepare_knowledge(
            packet_file[0], project="offline-project", key="offline-secret-key", store_id=store_id
        )
    assert provider["requests"] == []


def test_missing_credentials_never_reaches_transport(packet_file, provider):
    with pytest.raises(ValueError, match="credentials_missing"):
        knowledge.prepare_knowledge(packet_file[0], project="offline-project", key=" ")
    assert provider["requests"] == []


def test_provider_file_limit_rejects_without_silent_clipping(packet_file, provider, monkeypatch):
    monkeypatch.setattr(semantic, "MAX_VECTOR_FILE_BYTES", 10)
    with pytest.raises(ValueError, match="exceeds_provider_file_limit"):
        prepare(packet_file)
    assert provider["requests"] == []


def test_cli_records_failure_without_raw_provider_message_or_secret(
    packet_file, provider, monkeypatch, tmp_path, capsys
):
    def reject(*args, **kwargs):
        raise OSError("private provider message offline-secret-key")

    monkeypatch.setattr(semantic, "yandex_api_request", reject)
    monkeypatch.setenv("YANDEX_PROJECT_ID", "offline-project")
    monkeypatch.setenv("YANDEX_API_KEY", "offline-secret-key")
    output = tmp_path / "knowledge.json"
    assert knowledge.main(["--context-file", str(packet_file[0]), "--out", str(output)]) == 1
    receipt = json.loads(output.read_text())
    assert receipt == {"status": "failed", "error": "OSError", "generation_requests": 0}
    stdout = capsys.readouterr().out
    assert "private provider message" not in stdout and "offline-secret-key" not in stdout


def test_cli_writes_usable_indexed_receipt(packet_file, provider, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("YANDEX_PROJECT_ID", "offline-project")
    monkeypatch.setenv("YANDEX_API_KEY", "offline-secret-key")
    output = tmp_path / "knowledge.json"
    assert knowledge.main(["--context-file", str(packet_file[0]), "--out", str(output)]) == 0
    receipt = json.loads(output.read_text())
    assert receipt["status"] == "indexed"
    assert receipt["vector_store_ids"] == [knowledge.DEFAULT_STORE_ID]
    assert capsys.readouterr().out.startswith("LOGOS_KNOWLEDGE=")
