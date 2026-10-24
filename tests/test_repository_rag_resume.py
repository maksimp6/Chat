"""Receipt-selected recovery cannot create Files, invent originals or mix scopes."""

import copy
import json
from pathlib import Path

import pytest

from scripts import repository_rag as rag
from scripts import repository_rag_resume as resume


REPOSITORY = "maksimp6/Chat"
STORE = "store1"


def make_source(tmp_path, *, metadata=True):
    archive = rag.CorpusArchive(tmp_path / "original", REPOSITORY)
    archive.add(
        "git_blob",
        "git:blob:abc",
        "https://api.github.com/repos/maksimp6/Chat/git/blobs/abc",
        "abc",
        b"Exact source text with evidence.\n",
        {"git_oid": "abc"} if metadata else None,
    )
    return archive


def provider(monkeypatch, documents, *, repository=REPOSITORY):
    prefix = "chat-repository-archive-v1-" + rag.digest(REPOSITORY.encode())[:16] + "-"
    files, contents, attached, calls = [], {}, [], []
    for index, document in enumerate(documents):
        raw = rag.make_pack(repository, [document])
        file_id = f"file{index}"
        files.append(
            {"id": file_id, "filename": prefix + rag.digest(raw) + ".md", "bytes": len(raw)}
        )
        contents[file_id] = raw

    def listing(path, *args):
        calls.append(("GET", path))
        return copy.deepcopy(files if path == "/files" else attached)

    def request(method, path, *args, **kwargs):
        calls.append((method, path))
        assert method == "GET", "resume must never POST Files"
        return contents[path.split("/")[2]]

    def json_request(method, path, project, key, payload=None):
        calls.append((method, path))
        if method == "GET":
            return {"id": STORE, "name": "chat-repo-test", "status": "completed"}
        assert path == f"/vector_stores/{STORE}/files"
        row = {"file_id": payload["file_id"], "status": "completed"}
        attached.append(row)
        return row

    monkeypatch.setattr(rag.semantic, "list_yandex_objects", listing)
    monkeypatch.setattr(rag.semantic, "yandex_api_request", request)
    monkeypatch.setattr(rag.semantic, "yandex_json_request", json_request)
    receipt = {
        "status": "provider_inspected",
        "generation_requests": 0,
        "provider_inventory": {
            "status": "observed_without_content_verification",
            "store_id": STORE,
            "generation_requests": 0,
            "candidate_pack_files": copy.deepcopy(files),
        },
    }
    return receipt, files, contents, attached, calls


def invoke(tmp_path, receipt, *, archive=None):
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))
    archive = archive or rag.CorpusArchive(tmp_path / "restored", REPOSITORY)
    return archive, resume.resume_from_receipt(archive, path, STORE, "project", "key")


def documents(archive):
    return [path.read_bytes() for path in rag.prepared_documents(archive).values()]


def mutations(calls):
    return [call for call in calls if call[0] != "GET"]


def test_resume_pairs_split_packs_and_never_uploads_or_claims_originals(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    original_documents = documents(source)
    receipt, _, _, attached, calls = provider(monkeypatch, list(reversed(original_documents)))
    archive, result = invoke(tmp_path, receipt)
    assert result["documents"] == 2 and result["files_uploaded"] == 0
    assert result["generation_requests"] == 0 and not result["original_bytes_recovered"]
    assert all(pack["reused"] and pack["attachment_allowed"] for pack in result["packs"])
    assert len(attached) == 2
    assert set(rag.prepared_documents(archive)) == set(rag.prepared_documents(source))
    assert list((archive.directory / "objects").iterdir()) == []
    assert len(archive.entries) == 1
    assert all(
        entry["archive"] == "provider_sanitized_copy_only" for entry in archive.entries.values()
    )
    assert {item["reason"] for item in archive.gaps} == {
        "raw_originals_not_recovered",
        "resumed_corpus_completeness_not_established",
    }
    assert mutations(calls) == [("POST", f"/vector_stores/{STORE}/files")] * 2
    calls.clear()
    _, repeated = invoke(tmp_path, receipt, archive=archive)
    assert repeated == result and not mutations(calls)
    assert len(archive.gaps) == 2


def test_content_without_provenance_is_restored_without_fabrication(tmp_path, monkeypatch):
    source = make_source(tmp_path, metadata=False)
    receipt, _, _, _, _ = provider(monkeypatch, documents(source))
    archive, result = invoke(tmp_path, receipt)
    entry = next(iter(archive.entries.values()))
    assert result["documents"] == 1
    assert entry["metadata"] == {} and "provenance_document_sha256" not in entry


@pytest.mark.parametrize(
    "field,value",
    [("status", "blocked"), ("generation_requests", 1), ("generation_requests", False)],
)
def test_bad_receipt_fails_before_provider_calls(tmp_path, monkeypatch, field, value):
    source = make_source(tmp_path)
    receipt, _, _, _, calls = provider(monkeypatch, documents(source))
    receipt[field] = value
    with pytest.raises(ValueError, match="resume_receipt_invalid"):
        invoke(tmp_path, receipt)
    assert not calls


@pytest.mark.parametrize(
    "field,value", [("store_id", "wrong-store"), ("generation_requests", False)]
)
def test_wrong_inventory_identity_fails_before_provider_calls(tmp_path, monkeypatch, field, value):
    source = make_source(tmp_path)
    receipt, _, _, _, calls = provider(monkeypatch, documents(source))
    receipt["provider_inventory"][field] = value
    with pytest.raises(ValueError, match="resume_inventory_invalid"):
        invoke(tmp_path, receipt)
    assert not calls


def test_pack_bytes_are_rechecked_against_current_inventory(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    receipt, files, _, _, calls = provider(monkeypatch, documents(source))
    files[-1]["bytes"] += 1
    with pytest.raises(ValueError, match="resume_current_file_mismatch"):
        invoke(tmp_path, receipt)
    assert not mutations(calls)


def test_last_tampered_pack_prevents_every_attachment(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    receipt, _, contents, attached, calls = provider(monkeypatch, documents(source))
    contents["file1"] += b"tamper"
    with pytest.raises(ValueError, match="existing_pack_hash_mismatch"):
        invoke(tmp_path, receipt)
    assert not attached and not mutations(calls)
    assert not list((tmp_path / "restored" / "documents").iterdir())


def test_pack_manifest_wrong_repository_is_rejected_before_mutation(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    receipt, _, _, _, calls = provider(monkeypatch, documents(source), repository="other/repo")
    with pytest.raises(ValueError, match="pack_repository_mismatch"):
        invoke(tmp_path, receipt)
    assert not mutations(calls)


def change_identity(document, field, value):
    header, encoded, rest = document.split(b"\n\n", 2)
    identity = json.loads(encoded)
    identity[field] = value
    return header + b"\n\n" + rag.canonical(identity) + b"\n\n" + rest


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("repository", "other/repo", "resume_document_repository_mismatch"),
        ("redaction_policy", "old-policy", "resume_document_redaction_policy_mismatch"),
        ("raw_sha256", "not-a-hash", "resume_document_original_hash_invalid"),
        ("raw_bytes", False, "resume_document_original_size_invalid"),
    ],
)
def test_document_identity_is_independently_validated(tmp_path, monkeypatch, field, value, error):
    source = make_source(tmp_path, metadata=False)
    tampered = change_identity(documents(source)[0], field, value)
    receipt, _, _, _, calls = provider(monkeypatch, [tampered])
    with pytest.raises(ValueError, match=error):
        invoke(tmp_path, receipt)
    assert not mutations(calls)


def test_unknown_document_header_is_rejected(tmp_path, monkeypatch):
    receipt, _, _, _, calls = provider(monkeypatch, [b"# Unverified custom text\n"])
    with pytest.raises(ValueError, match="resume_document_header_invalid"):
        invoke(tmp_path, receipt)
    assert not mutations(calls)


def test_provenance_without_its_content_cannot_be_attached(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    provenance = next(item for item in documents(source) if item.startswith(resume._PROVENANCE))
    receipt, _, _, _, calls = provider(monkeypatch, [provenance])
    with pytest.raises(ValueError, match="resume_provenance_content_missing"):
        invoke(tmp_path, receipt)
    assert not mutations(calls)


def test_provenance_identity_must_match_the_exact_referenced_content(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    items = documents(source)
    provenance = next(item for item in items if item.startswith(resume._PROVENANCE))
    payload = json.loads(provenance[len(resume._PROVENANCE) :])
    payload["version"] = "different-version"
    changed = resume._PROVENANCE + rag.canonical(payload) + b"\n"
    items[items.index(provenance)] = changed
    receipt, _, _, _, calls = provider(monkeypatch, items)
    with pytest.raises(ValueError, match="resume_provenance_content_mismatch"):
        invoke(tmp_path, receipt)
    assert not mutations(calls)


def test_receipt_duplicate_or_foreign_filename_is_rejected(tmp_path, monkeypatch):
    source = make_source(tmp_path, metadata=False)
    receipt, _, _, _, calls = provider(monkeypatch, documents(source))
    receipt["provider_inventory"]["candidate_pack_files"] *= 2
    with pytest.raises(ValueError, match="resume_duplicate_candidate"):
        invoke(tmp_path, receipt)
    assert not calls
    receipt["provider_inventory"]["candidate_pack_files"] = receipt["provider_inventory"][
        "candidate_pack_files"
    ][:1]
    receipt["provider_inventory"]["candidate_pack_files"][0]["filename"] = "foreign-prefix.md"
    with pytest.raises(ValueError, match="resume_pack_filename_invalid"):
        invoke(tmp_path, receipt)
    assert not calls


def test_unrelated_local_document_is_preserved_and_blocks_attachment(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    receipt, _, _, _, calls = provider(monkeypatch, documents(source))
    archive = rag.CorpusArchive(tmp_path / "restored", REPOSITORY)
    orphan = archive.directory / "documents" / ("a" * 64 + ".md")
    orphan.write_bytes(b"preserve unrelated local data")
    with pytest.raises(ValueError, match="resume_archive_documents_conflict"):
        invoke(tmp_path, receipt, archive=archive)
    assert orphan.read_bytes() == b"preserve unrelated local data"
    assert not mutations(calls)


def test_all_document_and_provenance_validation_precedes_attachment(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    receipt, _, _, _, calls = provider(monkeypatch, documents(source))
    original = rag.prepared_documents
    verified = []

    def prepared(archive):
        assert not mutations(calls)
        result = original(archive)
        verified.append(set(result))
        return result

    monkeypatch.setattr(rag, "prepared_documents", prepared)
    archive, result = invoke(tmp_path, receipt)
    assert verified == [{sha for item in result["packs"] for sha in item["documents"]}]
    assert archive.entries


def test_declared_total_byte_budget_blocks_before_downloads(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    receipt, _, _, _, calls = provider(monkeypatch, documents(source))
    monkeypatch.setattr(resume, "MAX_RECEIPT_TOTAL_BYTES", 1)
    with pytest.raises(ValueError, match="resume_receipt_total_size_limit"):
        invoke(tmp_path, receipt)
    assert not calls


def test_candidate_count_budget_blocks_before_provider_calls(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    receipt, _, _, _, calls = provider(monkeypatch, documents(source))
    monkeypatch.setattr(resume, "MAX_RECEIPT_FILES", 1)
    with pytest.raises(ValueError, match="resume_candidate_count_invalid"):
        invoke(tmp_path, receipt)
    assert not calls


def test_attachment_checkpoint_is_explicitly_bound_to_restored_archive(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    receipt, _, _, _, _ = provider(monkeypatch, documents(source))
    received = []

    def attach(*args, checkpoint_path):
        received.append(checkpoint_path)

    monkeypatch.setattr(rag, "_attach_and_wait", attach)
    archive, _ = invoke(tmp_path, receipt)
    assert received == [archive.directory / "index-checkpoint.json"]
