"""Exercise native case indexing through real context and Vector Store helpers."""

import hashlib
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from email import policy
from email.parser import BytesParser
from urllib.parse import urlsplit

import pytest

from scripts import logos_context as context
from scripts import logos_knowledge as knowledge
from scripts import semantic_pipeline as semantic

CACHE_SCOPE = {"cache_owner": "maksimp6", "cache_access_scope": "repository:maksimp6/Chat"}
CLI_SCOPE = ["--cache-owner", "maksimp6", "--cache-access-scope", "repository:maksimp6/Chat"]


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
        "project": "offline-project",
        "store_id": store_id,
    }

    def api(method, path, project, key, *, body=None, content_type="application/json", **kwargs):
        state["requests"].append((method, path))
        assert project == state["project"] and key == "offline-secret-key"
        store_id = state["store_id"]
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
            identifier = "new-context"
            while identifier in state["files"]:
                identifier += "-next"
            state["files"][identifier] = {
                "filename": file_part.get_filename(),
                "content": content,
            }
            data = {
                "id": identifier,
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
        packet_file[0], project="offline-project", key="offline-secret-key", **CACHE_SCOPE
    )


def test_full_context_is_indexed_in_existing_store_preserving_old_files(packet_file, provider):
    receipt = prepare(packet_file)
    document = provider["files"]["new-context"]["content"]
    assert document.endswith(b"\n") and len(document) > 65536
    assert "END_OF_OWNER_DIALOGUE" in document.decode()
    stable = json.loads(document.decode().split("\n\n", 1)[1])
    assert stable["context"] == context.knowledge_content(packet_file[1])
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
    assert (
        knowledge.main(["--context-file", str(packet_file[0]), "--out", str(output), *CLI_SCOPE])
        == 1
    )
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
    assert (
        knowledge.main(["--context-file", str(packet_file[0]), "--out", str(output), *CLI_SCOPE])
        == 1
    )
    receipt = json.loads(output.read_text())
    assert receipt == {"status": "failed", "error": "OSError", "generation_requests": 0}
    stdout = capsys.readouterr().out
    assert "private provider message" not in stdout and "offline-secret-key" not in stdout


def test_cli_writes_usable_indexed_receipt(packet_file, provider, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("YANDEX_PROJECT_ID", "offline-project")
    monkeypatch.setenv("YANDEX_API_KEY", "offline-secret-key")
    output = tmp_path / "knowledge.json"
    assert (
        knowledge.main(["--context-file", str(packet_file[0]), "--out", str(output), *CLI_SCOPE])
        == 0
    )
    receipt = json.loads(output.read_text())
    assert receipt["status"] == "indexed"
    assert receipt["vector_store_ids"] == [knowledge.DEFAULT_STORE_ID]
    assert capsys.readouterr().out.startswith("LOGOS_KNOWLEDGE=")


def _recapture(packet_file, *, captured_at="2026-10-24T00:00:00+00:00", change=None):
    path, original = packet_file
    packet = deepcopy(original)
    packet["captured_at"] = captured_at
    if change:
        change(packet)
    packet.pop("packet_sha256")
    context._seal(packet)
    path.write_text(json.dumps(packet), encoding="utf-8")
    return packet


def test_recaptured_identical_sources_reuse_file_and_preserve_each_capture(packet_file, provider):
    first = prepare(packet_file)
    second_packet = _recapture(packet_file)
    provider["requests"].clear()
    second = prepare(packet_file)
    assert first["context_sha256"] != second["context_sha256"]
    assert second["context_sha256"] == second_packet["packet_sha256"]
    assert first["captured_at"] != second["captured_at"]
    assert first["context_content_sha256"] == second["context_content_sha256"]
    assert first["document_sha256"] == second["document_sha256"]
    assert first["file_id"] == second["file_id"]
    assert all(method == "GET" for method, _ in provider["requests"])
    assert context.load_context(packet_file[0]) == second_packet


@pytest.mark.parametrize("change", ["source_version", "owner_text", "checkout", "redaction"])
def test_substantive_or_provenance_changes_invalidate_content(packet_file, provider, change):
    first = prepare(packet_file)

    def mutate(packet):
        if change == "source_version":
            packet["sources"][0]["version"] = "new-source-revision"
        elif change == "owner_text":
            original = packet["owner_context"]
            packet["owner_context"] = context._source(
                original["id"],
                original["kind"],
                original["url"],
                original["version"],
                "Substantively changed owner instruction",
            )
        elif change == "checkout":
            packet["checkout_sha"] = "c" * 40
            packet["case"]["head_sha"] = "c" * 40
            next(source for source in packet["sources"] if source["kind"] == "pull_request")[
                "head_sha"
            ] = "c" * 40
        else:
            packet["sources"][0]["redactions"] += 1
        packet["manifest"] = context._manifest(packet["owner_context"], packet["sources"])

    _recapture(packet_file, change=mutate)
    second = prepare(packet_file)
    assert first["document_sha256"] != second["document_sha256"]
    assert first["file_id"] != second["file_id"]
    assert "new-context" in second["attached_files"]


@pytest.mark.parametrize("field", ["cache_owner", "cache_access_scope"])
def test_authority_or_access_scope_does_not_reuse_another_scope(packet_file, provider, field):
    first = prepare(packet_file)
    scope = {**CACHE_SCOPE, field: "another-scope"}
    second = knowledge.prepare_knowledge(
        packet_file[0], project="offline-project", key="offline-secret-key", **scope
    )
    assert first["document_sha256"] != second["document_sha256"]
    assert first["file_id"] != second["file_id"]


@pytest.mark.parametrize("field", ["cache_owner", "cache_access_scope"])
def test_unknown_scope_fails_before_provider_operations(packet_file, provider, field):
    scope = {**CACHE_SCOPE, field: None}
    with pytest.raises(ValueError, match="cache_scope_missing"):
        knowledge.prepare_knowledge(
            packet_file[0], project="offline-project", key="offline-secret-key", **scope
        )
    assert provider["requests"] == []


@pytest.mark.parametrize("captured_at", [None, "", " ", "x" * 101])
def test_capture_provenance_is_checked_before_provider_access(packet_file, provider, captured_at):
    _recapture(packet_file, captured_at=captured_at)
    with pytest.raises(ValueError, match="capture_metadata_invalid"):
        prepare(packet_file)
    assert provider["requests"] == []


def test_processing_policy_and_index_settings_invalidate_reuse(packet_file, provider, monkeypatch):
    first = prepare(packet_file)
    monkeypatch.setattr(context, "REDACTION_POLICY_VERSION", "revised-redaction-policy")
    second = prepare(packet_file)
    assert first["file_id"] != second["file_id"]
    strategy = semantic.vector_store_chunking_strategy()
    monkeypatch.setattr(
        semantic, "vector_store_chunking_strategy", lambda: {**strategy, "revision": 2}
    )
    third = prepare(packet_file)
    assert third["file_id"] not in {first["file_id"], second["file_id"]}


def test_concurrent_local_preparation_performs_one_upload_and_attachment(
    packet_file, provider, monkeypatch
):
    original = semantic.yandex_api_request

    def delayed_upload(method, path, *args, **kwargs):
        if (method, path) == ("POST", "/files"):
            # Without the lock another caller observes the same missing file
            # before this upload is committed at the provider boundary.
            time.sleep(0.03)
        return original(method, path, *args, **kwargs)

    monkeypatch.setattr(semantic, "yandex_api_request", delayed_upload)
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: prepare(packet_file), range(4)))
    assert len({receipt["file_id"] for receipt in receipts}) == 1
    assert provider["requests"].count(("POST", "/files")) == 1
    assert (
        provider["requests"].count(("POST", f"/vector_stores/{knowledge.DEFAULT_STORE_ID}/files"))
        == 1
    )
    assert not any("search" in path or "responses" in path for _, path in provider["requests"])


def test_retry_after_upload_succeeded_but_index_failed_reuses_original_file(packet_file, provider):
    provider["index_status"] = "failed"
    with pytest.raises(ValueError, match="index_failed"):
        prepare(packet_file)
    provider["index_status"] = "completed"
    provider["requests"].clear()
    receipt = prepare(packet_file)
    assert receipt["status"] == "indexed"
    assert all(method == "GET" for method, _ in provider["requests"])


def test_reusable_view_removes_only_collection_metadata_not_source_evidence(packet_file):
    _, packet = packet_file
    snapshot = context.knowledge_content(packet)["snapshot"]
    assert "captured_at" not in snapshot and "packet_sha256" not in snapshot
    assert snapshot["sources"] == packet["sources"]
    assert snapshot["owner_context"] == packet["owner_context"]
    assert packet["captured_at"] and packet["packet_sha256"]


@pytest.mark.parametrize("field", ["project", "store_id"])
def test_storage_namespace_change_does_not_reuse_prior_scope(packet_file, provider, field):
    first = prepare(packet_file)
    provider[field] = "another-project" if field == "project" else "another-store"
    second = knowledge.prepare_knowledge(
        packet_file[0],
        project=provider["project"],
        key="offline-secret-key",
        store_id=provider["store_id"],
        **CACHE_SCOPE,
    )
    assert first["document_sha256"] != second["document_sha256"]
    assert first["file_id"] != second["file_id"]


def test_unsafe_lock_directory_fails_before_provider_access(
    packet_file, provider, tmp_path, monkeypatch
):
    monkeypatch.setattr(knowledge.tempfile, "gettempdir", lambda: str(tmp_path))
    directory = tmp_path / f"logos-knowledge-{knowledge.os.getuid()}"
    directory.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="lock_directory_unsafe"):
        prepare(packet_file)
    assert provider["requests"] == []


@pytest.mark.parametrize("kind", ["directory_symlink", "file_symlink", "file_permissions"])
def test_lock_paths_cannot_redirect_or_share_writable_files(
    packet_file, provider, tmp_path, monkeypatch, kind
):
    monkeypatch.setattr(knowledge.tempfile, "gettempdir", lambda: str(tmp_path))
    directory = tmp_path / f"logos-knowledge-{knowledge.os.getuid()}"
    victim = tmp_path / "untouched"
    victim.write_text("must not change")
    if kind == "directory_symlink":
        directory.symlink_to(tmp_path, target_is_directory=True)
    else:
        directory.mkdir(mode=0o700)
        identity = hashlib.sha256(
            context._canonical_json(["offline-project", knowledge.DEFAULT_STORE_ID]).encode()
        ).hexdigest()
        lock_file = directory / (identity + ".lock")
        if kind == "file_symlink":
            lock_file.symlink_to(victim)
        else:
            lock_file.write_text("")
            lock_file.chmod(0o644)
    with pytest.raises((OSError, ValueError)):
        prepare(packet_file)
    assert victim.read_text() == "must not change"
    assert provider["requests"] == []
