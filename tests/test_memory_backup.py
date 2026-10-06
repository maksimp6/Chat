import json

import pytest

from agent_memory.backup import (
    COMMIT_NAME,
    MANIFEST_NAME,
    PAYLOAD_NAME,
    MemoryBackupError,
    copy_verified_backup,
    create_backup,
    restore_backup,
)
from agent_memory.file_memory_db import FileMemoryDB


def _source(tmp_path):
    path = tmp_path / "source" / "alice.memory"
    db = FileMemoryDB(path)
    db.put("user:1", {"name": "Alice"})
    db.put("counter", 7)
    return db


def test_backup_restore_reconstructs_equivalent_state(tmp_path):
    db = _source(tmp_path)
    backup = tmp_path / "backup"

    manifest = create_backup(db, backup)
    restored = restore_backup(backup, tmp_path / "restored" / "alice.memory")

    assert manifest.sequence == db.sequence
    assert manifest.size == (backup / PAYLOAD_NAME).stat().st_size
    assert restored.sequence == db.sequence
    assert restored.items() == db.items()
    assert (backup / MANIFEST_NAME).is_file()
    assert (backup / COMMIT_NAME).read_text() == "committed\n"


def test_backup_manifest_matches_payload(tmp_path):
    db = _source(tmp_path)
    backup = tmp_path / "backup"
    manifest = create_backup(db, backup)

    raw = json.loads((backup / MANIFEST_NAME).read_text())
    assert raw == {
        "version": 1,
        "sequence": db.sequence,
        "size": manifest.size,
        "sha256": manifest.sha256,
    }


def test_restore_rejects_missing_commit_marker(tmp_path):
    db = _source(tmp_path)
    backup = tmp_path / "backup"
    create_backup(db, backup)
    (backup / COMMIT_NAME).unlink()

    with pytest.raises(MemoryBackupError, match="not committed"):
        restore_backup(backup, tmp_path / "restored.memory")


def test_restore_rejects_checksum_mismatch(tmp_path):
    db = _source(tmp_path)
    backup = tmp_path / "backup"
    create_backup(db, backup)
    with (backup / PAYLOAD_NAME).open("ab") as handle:
        handle.write(b"x")

    with pytest.raises(MemoryBackupError, match="size mismatch"):
        restore_backup(backup, tmp_path / "restored.memory")


def test_restore_rejects_tampered_same_size_payload(tmp_path):
    db = _source(tmp_path)
    backup = tmp_path / "backup"
    create_backup(db, backup)
    payload = bytearray((backup / PAYLOAD_NAME).read_bytes())
    payload[0] ^= 1
    (backup / PAYLOAD_NAME).write_bytes(payload)

    with pytest.raises(MemoryBackupError, match="checksum mismatch"):
        restore_backup(backup, tmp_path / "restored.memory")


@pytest.mark.parametrize(
    "manifest",
    [
        "{",
        '{"version":2,"sequence":1,"size":1,"sha256":"' + "0" * 64 + '"}',
        '{"version":1,"sequence":-1,"size":1,"sha256":"' + "0" * 64 + '"}',
        '{"version":1,"sequence":1,"size":1,"sha256":"bad"}',
        '{"version":1,"sequence":1}',
    ],
)
def test_restore_rejects_invalid_manifest(tmp_path, manifest):
    db = _source(tmp_path)
    backup = tmp_path / "backup"
    create_backup(db, backup)
    (backup / MANIFEST_NAME).write_text(manifest)

    with pytest.raises(MemoryBackupError):
        restore_backup(backup, tmp_path / "restored.memory")


def test_restore_rejects_existing_destination(tmp_path):
    db = _source(tmp_path)
    backup = tmp_path / "backup"
    create_backup(db, backup)
    target = tmp_path / "restored.memory"
    target.write_text("occupied")

    with pytest.raises(MemoryBackupError, match="already exists"):
        restore_backup(backup, target)


def test_restore_rejects_sequence_mismatch(tmp_path):
    db = _source(tmp_path)
    backup = tmp_path / "backup"
    create_backup(db, backup)
    manifest_path = backup / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["sequence"] += 1
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(MemoryBackupError, match="sequence mismatch"):
        restore_backup(backup, tmp_path / "restored.memory")


def test_copy_verified_backup_copies_only_after_restore_proof(tmp_path):
    db = _source(tmp_path)
    backup = tmp_path / "backup"
    destination = tmp_path / "transport-ready"
    create_backup(db, backup)

    copy_verified_backup(backup, destination)

    assert (destination / PAYLOAD_NAME).read_bytes() == (backup / PAYLOAD_NAME).read_bytes()
    assert (destination / COMMIT_NAME).is_file()


def test_create_backup_rejects_existing_directory(tmp_path):
    db = _source(tmp_path)
    backup = tmp_path / "backup"
    backup.mkdir()

    with pytest.raises(FileExistsError):
        create_backup(db, backup)
