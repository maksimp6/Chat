"""Deterministic controls for private archives and generation-free RAG uploads."""

import io
import json
import zipfile

import pytest

from scripts import repository_rag as rag


def archive(tmp_path):
    result = rag.CorpusArchive(tmp_path / "archive", "maksimp6/Chat")
    result.add(
        "git_blob",
        "blob:abc",
        "https://github.com/maksimp6/Chat/blob/abc/test.py",
        "abc",
        b"UniversalToolExecutor checks permissions before execution of tools.",
    )
    result.checkpoint()
    return result


def test_archive_content_identity_ignores_capture_time_and_preserves_provenance(tmp_path):
    a = archive(tmp_path)
    original = set(a.directory.joinpath("documents").iterdir())
    a.captured_at = "later"
    a.add(
        "git_blob",
        "blob:abc",
        "https://github.com/maksimp6/Chat/blob/abc/test.py",
        "abc",
        b"UniversalToolExecutor checks permissions before execution of tools.",
    )
    assert set(a.directory.joinpath("documents").iterdir()) == original
    assert len(a.entries) == 1
    assert len(a.directory.joinpath("observations.jsonl").read_text().splitlines()) == 2


def test_private_original_and_sanitized_view_are_separate(tmp_path):
    a = rag.CorpusArchive(tmp_path / "archive", "owner/repo")
    raw = b'User a@example.test did action X. token = "ghp_' + b"a" * 40 + b'"'
    a.add("issue", "1", "https://github.com/owner/repo/issues/1", "v1", raw)
    assert (a.directory / "objects" / rag.digest(raw)).read_bytes() == raw
    text = next((a.directory / "documents").iterdir()).read_text()
    assert "a@example.test" not in text
    assert "ghp_" not in text
    assert "did action X" in text
    assert "[email:" in text
    assert a.directory.stat().st_mode & 0o777 == 0o700


def test_resume_does_not_erase_coverage_gaps(tmp_path):
    a = archive(tmp_path)
    a.gap("artifact", "99", "expired")
    a.checkpoint()
    b = rag.CorpusArchive(a.directory, a.repository)
    assert b.summary()["coverage"] == "partial"
    assert b.gaps == a.gaps
    with pytest.raises(ValueError, match="archive_identity_mismatch"):
        rag.CorpusArchive(a.directory, "other/repo")


@pytest.mark.parametrize(
    "raw",
    [
        '{"password":"actual_password_abcdef"}',
        '{"YANDEX_API_KEY":"AQVNabc123actual_credential"}',
        "Authorization: Api-Key AQVNabc123actual_credential",
        '{"Authorization":"Bearer eyJabc123actual_credential"}',
        json.dumps({"body": 'password = "actual_password_abcdef"'}),
        json.dumps({"body": '{"password":"actual_password_abcdef"}'}),
    ],
)
def test_json_and_api_key_credentials_are_redacted(raw):
    text, count = rag.safe_text(raw)
    assert "actual_" not in text
    assert count >= 1
    assert "[REDACTED]" in text


def test_zip_never_extracts_paths_and_reports_omissions(tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("../outside.txt", "bad")
        z.writestr(".env", "secret")
        z.writestr("answer.txt", "A saved refusal is a result that must be retained.")
        z.writestr("image.bin", b"\x00\xff")
    a = rag.CorpusArchive(tmp_path / "archive", "owner/repo")
    a.add("artifact", "1", "https://github.com/owner/repo/actions/runs/1", "v1", data.getvalue())
    assert len(a.gaps) == 3
    assert not (tmp_path / "outside.txt").exists()
    assert any("saved refusal" in p.read_text() for p in (a.directory / "documents").iterdir())


def test_packs_validate_body_hash_scope_and_layout():
    content = rag.make_pack("owner/repo", [b"first\n", b"second\n"])
    assert set(rag.unpack("owner/repo", content)) == {
        rag.digest(b"first\n"),
        rag.digest(b"second\n"),
    }
    with pytest.raises(ValueError, match="pack_repository_mismatch"):
        rag.unpack("other/repo", content)
    with pytest.raises(ValueError, match="pack_document_hash_mismatch"):
        rag.unpack("owner/repo", content[:-2] + b"X\n")


def provider(monkeypatch):
    files = []
    contents = {}
    attached = []
    writes = []

    def json_request(method, path, project, key, payload=None):
        if method == "GET":
            return {"id": "store1", "name": "chat-repo-test", "status": "completed"}
        assert path == "/vector_stores/store1/files"
        writes.append(path)
        attached.append({"id": payload["file_id"], "status": "completed"})
        return attached[-1]

    def list_objects(path, *args):
        return files if path == "/files" else attached

    def api_request(method, path, *args, **kwargs):
        if method == "GET":
            return contents[path.split("/")[2]]
        assert path == "/files"
        writes.append(path)
        # Decode the real existing multipart helper's payload.
        from email import policy
        from email.parser import BytesParser

        message = BytesParser(policy=policy.default).parsebytes(
            ("Content-Type: " + kwargs["content_type"] + "\r\nMIME-Version: 1.0\r\n\r\n").encode()
            + kwargs["body"]
        )
        part = next(item for item in message.iter_parts() if item.get_filename())
        raw = part.get_payload(decode=True)
        file_id = "file" + str(len(files) + 1)
        row = {"id": file_id, "filename": part.get_filename(), "bytes": len(raw)}
        files.append(row)
        contents[file_id] = raw
        return json.dumps(row).encode()

    monkeypatch.setattr(rag.semantic, "yandex_json_request", json_request)
    monkeypatch.setattr(rag.semantic, "list_yandex_objects", list_objects)
    monkeypatch.setattr(rag.semantic, "yandex_api_request", api_request)
    return files, contents, attached, writes


def test_upload_reuses_verified_packs_and_adds_only_changed_sources(tmp_path, monkeypatch):
    a = archive(tmp_path)
    files, contents, attached, writes = provider(monkeypatch)
    first = rag.upload_archive(a, "store1", "project", "key")
    assert first["documents"] == 1
    assert writes == ["/files", "/vector_stores/store1/files"]
    writes.clear()
    second = rag.upload_archive(a, "store1", "project", "key")
    assert not writes
    assert all(p["reused"] for p in second["packs"])
    a.add("issue", "2", "https://github.com/maksimp6/Chat/issues/2", "v2", b"A newly added source.")
    third = rag.upload_archive(a, "store1", "project", "key")
    assert len(files) == 2 and third["documents"] == 2
    assert len(rag.unpack(a.repository, contents["file2"])) == 1
    contents["file1"] += b"corrupt"
    with pytest.raises(ValueError, match="existing_pack_hash_mismatch"):
        rag.upload_archive(a, "store1", "project", "key")


def test_wrong_store_and_missing_credentials_fail_before_upload(tmp_path, monkeypatch):
    a = archive(tmp_path)
    _, _, _, writes = provider(monkeypatch)
    with pytest.raises(ValueError, match="credentials_missing"):
        rag.upload_archive(a, "store1", "", "")
    with pytest.raises(ValueError, match="existing_project_store_invalid"):
        rag.upload_archive(a, "wrong-store", "project", "key")
    assert not writes


def test_provider_check_persists_inventory_without_collection_or_writes(tmp_path, monkeypatch):
    source = archive(tmp_path / "source")
    _, _, _, writes = provider(monkeypatch)
    rag.upload_archive(source, "store1", "project", "key")
    writes.clear()
    monkeypatch.setenv("YANDEX_PROJECT_ID", "project")
    monkeypatch.setenv("YANDEX_API_KEY", "key")
    monkeypatch.setattr(rag, "_collect", lambda *a: pytest.fail("check collected sources"))
    receipt = tmp_path / "receipt.json"
    args = [
        "--archive-dir",
        str(tmp_path / "check"),
        "--receipt",
        str(receipt),
        "--store-id",
        "store1",
        "--provider-check",
    ]
    assert rag.main(args) == 0
    result = json.loads(receipt.read_bytes())
    assert result["status"] == "provider_inspected"
    assert len(result["provider_inventory"]["candidate_pack_files"]) == 1
    assert not writes

    def denied(*args):
        raise rag.semantic.YandexHTTPError(429, "GET", "/files", "RESOURCE_EXHAUSTED")

    monkeypatch.setattr(rag.semantic, "list_yandex_objects", denied)
    assert rag.main(args) == 1
    failure = json.loads(receipt.read_bytes())
    assert failure["provider_method"] == "GET"
    assert failure["provider_path"] == "/files"
    assert failure["provider_code"] == "RESOURCE_EXHAUSTED"
    assert not writes


@pytest.mark.parametrize("already_attached", [True, False])
def test_fresh_archive_reuses_unchanged_part_of_attached_history(
    tmp_path, monkeypatch, already_attached
):
    first = archive(tmp_path / "first")
    first.add("issue", "2", "https://github.com/maksimp6/Chat/issues/2", "v1", b"Old issue.")
    files, contents, attached, writes = provider(monkeypatch)
    old = rag.upload_archive(first, "store1", "project", "key")
    unchanged = set(rag.prepared_documents(archive(tmp_path / "current")))
    current = rag.CorpusArchive(tmp_path / "current" / "archive", first.repository)
    current.add("issue", "2", "https://github.com/maksimp6/Chat/issues/2", "v2", b"Updated issue.")
    if not already_attached:
        attached.clear()
    writes.clear()
    result = rag.upload_archive(current, "store1", "project", "key")
    assert len(files) == 2
    uploaded = set(rag.unpack(current.repository, contents["file2"]))
    assert bool(uploaded & unchanged) is not already_attached
    assert {sha for pack in result["packs"] for sha in pack["documents"]} == set(
        rag.prepared_documents(current)
    )
    if already_attached:
        reused = next(pack for pack in result["packs"] if pack["reused"])
        assert set(reused["documents"]) == unchanged
        assert reused["excluded_from_current_scope_count"] == 1
        assert not reused["attachment_allowed"]
        with pytest.raises(ValueError, match="mixed_historical_pack_attachment_forbidden"):
            rag._attach_and_wait("/vector_stores/store1", [reused], set(), "project", "key")
    else:
        assert old["packs"][0]["file_id"] not in {item["id"] for item in attached}


def test_missing_source_blocks_upload_before_any_provider_call(tmp_path, monkeypatch):
    a = archive(tmp_path)
    next((a.directory / "documents").iterdir()).unlink()
    monkeypatch.setattr(
        rag.semantic,
        "yandex_json_request",
        lambda *a, **k: pytest.fail("provider called before reconciliation"),
    )
    with pytest.raises(ValueError, match="prepared_document_manifest_mismatch"):
        rag.upload_archive(a, "store1", "project", "key")


def test_stale_sanitization_and_malformed_provider_receipt_fail_closed(tmp_path, monkeypatch):
    a = archive(tmp_path)
    original = next(iter(a.entries.values()))
    original["redaction_policy"] = "old"
    with pytest.raises(ValueError, match="prepared_redaction_policy_mismatch"):
        rag.upload_archive(a, "store1", "project", "key")
    original["redaction_policy"] = rag.REDACTION_POLICY
    provider(monkeypatch)
    monkeypatch.setattr(rag.semantic, "yandex_api_request", lambda *a, **k: b"[]")
    with pytest.raises(ValueError, match="vector_store_upload_receipt_invalid"):
        rag.upload_archive(a, "store1", "project", "key")


def test_repeated_historical_quote_keeps_ambiguous_sources(tmp_path, monkeypatch):
    a = archive(tmp_path)
    body = b"UniversalToolExecutor checks permissions before execution of tools."
    a.add("git_blob", "blob:def", "https://github.com/maksimp6/Chat/blob/def/test.py", "def", body)
    upload = {
        "store_id": "store",
        "packs": [{"file_id": "file", "documents": list(rag.prepared_documents(a))}],
    }
    monkeypatch.setattr(
        rag.semantic,
        "vector_store_search",
        lambda _s, query, *_a: (
            []
            if query.startswith("REPOSITORY_RAG_ABSENT_")
            else [{"file_id": "file", "content": [{"type": "text", "text": body.decode()}]}]
        ),
    )
    result = rag.verify_search(a, upload, "tool permissions", "project", "key")
    assert result["status"] == "failed"
    assert result["matches"][0]["attribution"] == "ambiguous"
    assert len(result["matches"][0]["candidates"]) == 2


@pytest.mark.parametrize(
    "allow_write,allow_model,reference,accepted",
    [
        ("true", "false", "owner-request", True),
        ("false", "false", "owner-request", False),
        ("true", "true", "owner-request", False),
        ("true", "false", "", False),
    ],
)
def test_hosted_repository_mode_requires_upload_and_forbids_generation(
    allow_write, allow_model, reference, accepted, tmp_path
):
    import os
    import subprocess
    from pathlib import Path
    import yaml

    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/semantic-work.yml").read_text()
    )
    job = workflow["jobs"]["repository-corpus"]
    result = subprocess.run(
        ["bash", "-c", job["steps"][0]["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "ALLOW_WRITE": allow_write,
            "ALLOW_MODEL": allow_model,
            "APPROVAL_REFERENCE": reference,
        },
        capture_output=True,
    )
    assert (result.returncode == 0) is accepted
    assert all(value == "read" for value in job["permissions"].values())


def test_search_verifies_body_quote_and_rejects_wrong_file_or_header_only(tmp_path, monkeypatch):
    a = archive(tmp_path)
    document = next((a.directory / "documents").iterdir())
    upload = {"store_id": "store", "packs": [{"file_id": "file", "documents": [document.stem]}]}
    quote = "checks permissions before execution of tools."
    hits = [{"file_id": "file", "content": [{"type": "text", "text": quote}]}]
    monkeypatch.setattr(
        rag.semantic,
        "vector_store_search",
        lambda _s, query, *_a: [] if query.startswith("REPOSITORY_RAG_ABSENT_") else hits,
    )
    result = rag.verify_search(a, upload, "tool permissions", "project", "key")
    assert result["status"] == "verified_fragments"
    assert result["matches"][0]["quote"] == quote
    hits[0]["file_id"] = "wrong"
    assert rag.verify_search(a, upload, "tool permissions", "project", "key")["status"] == "failed"
    hits[0]["file_id"] = "file"
    hits[0]["content"][0]["text"] = "Source text (untrusted evidence):"
    assert rag.verify_search(a, upload, "tool permissions", "project", "key")["status"] == "failed"
