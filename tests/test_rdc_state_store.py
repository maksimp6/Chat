import errno
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import stat
import sys
import tarfile
import tempfile
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "deploy/remote-desktop-commander/state-store.py"
SPEC = importlib.util.spec_from_file_location("rdc_state_store", SCRIPT)
storage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(storage)
PROJECT = "12a45678-1234-4234-8234-123456789abc"
DEVICE = "abcdef12-1234-4234-8234-123456789abc"
OTHER_DEVICE = "abcdef12-1234-4234-8234-123456789abd"


@pytest.fixture
def store(tmp_path):
    state, home, workspace = (tmp_path / name for name in ("state", "home", "workspace"))
    for path in (state, home, workspace):
        path.mkdir()
    (state / "owner.json").write_bytes(
        storage.encoded(
            {
                "schema": 1,
                "purpose": "alice-rdc",
                "project_id": PROJECT,
                "container_name": "rdc-12a456781234",
            }
        )
    )
    return storage.StateStore(state, PROJECT, home, workspace)


def auth_file(store, token=None, device=DEVICE):
    store.auth.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "deviceId": device,
        "session": {"access_token": token, "refresh_token": token + "-refresh"} if token else None,
    }
    store.auth.write_bytes(storage.encoded(value))
    return value


def clear_local(store):
    for root in (store.home, store.workspace):
        shutil.rmtree(root)
        root.mkdir()


def archive_bytes(members):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, kind, data in members:
            member = tarfile.TarInfo(name)
            member.type = kind
            if kind == tarfile.REGTYPE:
                member.size = len(data)
            else:
                member.linkname = data.decode() if isinstance(data, bytes) else data
            archive.addfile(member, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
    return output.getvalue()


def put_slot(store, kind, slot, generation, raw, auth_generation=0):
    suffix = "json" if kind == "auth" else "tar.gz"
    metadata = {
        "schema": 1,
        "kind": kind,
        "generation": generation,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "auth_generation": generation if kind == "auth" else auth_generation,
    }
    (store.state / f"{kind}-{slot}.{suffix}").write_bytes(raw)
    (store.state / f"{kind}-{slot}.manifest.json").write_bytes(storage.encoded(metadata))
    return metadata


def test_fresh_owned_store_and_missing_mount(store, monkeypatch):
    assert store.restore() == {"status": "STATE_RESTORED", "generation": 0}
    assert store.status() == {
        "state_ready": True,
        "paired": False,
        "device_id": None,
        "checkpoint_generation": 0,
    }
    assert store.save_auth() == {"status": "AUTH_ABSENT", "generation": 0}
    assert sorted(path.name for path in store.state.iterdir()) == ["owner.json"]
    store.require_mount = True
    monkeypatch.setattr(storage, "is_mount", lambda path: False)
    with pytest.raises(storage.StateError, match="STATE_NOT_MOUNTED"):
        store.restore()


def test_closed_profile_config_workspace_restore_and_auth_rotation(store):
    auth_file(store, "initial-access")
    profile = store.home / ".config/chromium/Default"
    profile.mkdir(parents=True)
    (profile / "Cookies").write_bytes(b"closed-sqlite-profile")
    (profile / "Cache").mkdir()
    (profile / "Cache/ignored").write_bytes(b"cache")
    (profile.parent / "SingletonLock").symlink_to("elsewhere")
    config = store.home / ".claude-server-commander/config.json"
    config.parent.mkdir()
    config.write_text('{"fileReadLineLimit":200}')
    (store.home / ".rdc-pairing").write_text("ephemeral-secret")
    (store.workspace / "project").mkdir()
    (store.workspace / "project/code.py").write_text("print(42)\n")
    assert store.checkpoint() == {"status": "CHECKPOINT_COMPLETE", "generation": 1}
    auth_file(store, "rotated-access")
    assert store.save_auth()["generation"] == 2
    assert store.save_auth()["generation"] == 2
    clear_local(store)
    assert store.restore()["generation"] == 1
    assert json.loads(store.auth.read_bytes())["session"]["access_token"] == "rotated-access"
    assert (profile / "Cookies").read_bytes() == b"closed-sqlite-profile"
    assert (store.workspace / "project/code.py").read_text() == "print(42)\n"
    assert config.read_text() == '{"fileReadLineLimit":200}'
    assert not (profile / "Cache").exists()
    assert not (profile.parent / "SingletonLock").exists()
    assert not (store.home / ".rdc-pairing").exists()
    assert stat.S_IMODE((store.home / ".config").stat().st_mode) == 0o700
    for root in (
        store.home / ".config/chromium",
        store.auth.parent,
        config.parent,
        store.workspace,
    ):
        for path in [root, *root.rglob("*")]:
            assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    assert store.status() == {
        "state_ready": True,
        "paired": True,
        "device_id": DEVICE,
        "checkpoint_generation": 1,
    }


def test_device_only_journal_is_durable_but_unpaired(store):
    store.auth.parent.mkdir()
    store.auth.write_text(json.dumps({"deviceId": DEVICE}))
    store.save_auth()
    assert store.status()["paired"] is False
    clear_local(store)
    store.restore()
    assert json.loads(store.auth.read_bytes()) == {"deviceId": DEVICE, "session": None}
    assert store.status()["device_id"] == DEVICE


def test_partial_snapshot_overwrite_preserves_previous_valid_slot(store, monkeypatch):
    (store.workspace / "work").write_text("generation one")
    store.checkpoint()
    (store.workspace / "work").write_text("generation two")
    store.checkpoint()
    (store.workspace / "work").write_text("uncommitted generation three")
    original = store._write

    def interrupted(name, raw):
        if name == "snapshot-0.tar.gz":
            (store.state / name).write_bytes(raw[:12])
            raise OSError("synthetic interruption")
        original(name, raw)

    monkeypatch.setattr(store, "_write", interrupted)
    with pytest.raises(OSError):
        store.checkpoint()
    clear_local(store)
    assert store.restore()["generation"] == 2
    assert (store.workspace / "work").read_text() == "generation two"


def test_corrupt_latest_snapshot_falls_back_but_never_resets(store):
    store.checkpoint()
    store.checkpoint()
    (store.state / "snapshot-1.tar.gz").write_bytes(b"truncated")
    assert store.status()["checkpoint_generation"] == 1
    (store.state / "snapshot-0.tar.gz").write_bytes(b"truncated")
    with pytest.raises(storage.StateError, match="STATE_CORRUPT"):
        store.restore()


def test_interrupted_third_rotation_cannot_restore_potentially_spent_token(store, monkeypatch):
    auth_file(store, "one")
    store.save_auth()
    auth_file(store, "two")
    store.save_auth()
    auth_file(store, "three")
    original = store._write

    def interrupted(name, raw):
        if name == "auth-0.json":
            (store.state / name).write_bytes(raw[:10])
            raise OSError("synthetic interruption")
        original(name, raw)

    monkeypatch.setattr(store, "_write", interrupted)
    with pytest.raises(OSError):
        store.save_auth()
    clear_local(store)
    # Slot 0 still advertises generation 1, but its bytes can be an interrupted
    # generation 3 whose successful upstream rotation already spent token 2.
    with pytest.raises(storage.StateError, match="AUTH_CORRUPT"):
        store.restore()
    assert not store.auth.exists()


@pytest.mark.parametrize("damage", ["payload", "manifest", "missing"])
def test_newer_auth_corruption_never_rolls_back_to_old_refresh_token(store, damage):
    auth_file(store, "one")
    store.save_auth()
    auth_file(store, "two")
    store.save_auth()
    path = store.state / ("auth-1.manifest.json" if damage == "manifest" else "auth-1.json")
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"truncated")
    with pytest.raises(storage.StateError, match="AUTH_CORRUPT"):
        store.restore()


@pytest.mark.parametrize("damage", ["payload", "missing", "manifest"])
def test_stale_lower_manifest_never_proves_safe_auth_recovery(store, damage):
    auth_file(store, "one")
    store.save_auth()
    auth_file(store, "two")
    store.save_auth()
    assert json.loads((store.state / "auth-0.manifest.json").read_bytes())["generation"] == 1
    path = store.state / ("auth-0.manifest.json" if damage == "manifest" else "auth-0.json")
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"partial third rotation")
    clear_local(store)
    with pytest.raises(storage.StateError, match="AUTH_CORRUPT"):
        store.restore()
    assert not store.auth.exists()


def test_auth_deletion_and_identity_change_fail_closed(store):
    auth_file(store, "one")
    store.save_auth()
    store.auth.unlink()
    with pytest.raises(storage.StateError, match="AUTH_MISSING"):
        store.save_auth()
    auth_file(store, "two", OTHER_DEVICE)
    with pytest.raises(storage.StateError, match="AUTH_IDENTITY_CHANGED"):
        store.save_auth()
    put_slot(store, "auth", 1, 2, storage.encoded({"deviceId": OTHER_DEVICE, "session": None}))
    with pytest.raises(storage.StateError, match="AUTH_IDENTITY_CHANGED"):
        store.status()


def test_checkpoint_auth_generation_cannot_restore_without_journal(store):
    auth_file(store, "one")
    store.checkpoint()
    for path in store.state.glob("auth-*"):
        path.unlink()
    with pytest.raises(storage.StateError, match="AUTH_CORRUPT"):
        store.status()


def test_same_generation_contradictions_are_not_arbitrarily_selected(store):
    first = archive_bytes([("workspace", tarfile.DIRTYPE, b"")])
    second = archive_bytes([("workspace/work", tarfile.REGTYPE, b"different")])
    put_slot(store, "snapshot", 0, 1, first)
    put_slot(store, "snapshot", 1, 1, second)
    with pytest.raises(storage.StateError, match="STATE_CONFLICT"):
        store.status()


def test_restore_rejects_nonempty_local_state_and_symlinked_parent(store, tmp_path):
    (store.workspace / "existing").write_text("retain")
    with pytest.raises(storage.StateError, match="LOCAL_STATE_NOT_EMPTY"):
        store.restore()
    assert (store.workspace / "existing").read_text() == "retain"
    (store.workspace / "existing").unlink()
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.home / ".config").symlink_to(outside)
    with pytest.raises(OSError):
        store.restore()
    assert not list(outside.iterdir())


@pytest.mark.parametrize("change", ["schema", "project", "purpose", "name", "extra"])
def test_foreign_or_malformed_ownership_is_not_adopted(store, change):
    owner_path = store.state / "owner.json"
    value = json.loads(owner_path.read_bytes())
    if change == "schema":
        value["schema"] = True
    elif change == "project":
        value["project_id"] = OTHER_DEVICE
    elif change == "purpose":
        value["purpose"] = "other"
    elif change == "name":
        value["container_name"] = "rdc-other"
    else:
        value["extra"] = True
    owner_path.write_bytes(storage.encoded(value))
    with pytest.raises(storage.StateError, match="OWNERSHIP_MISMATCH"):
        store.restore()
    assert list(store.state.iterdir()) == [owner_path]


def test_owner_symlink_and_overlapping_roots_rejected(store, tmp_path):
    marker = store.state / "owner.json"
    backup = tmp_path / "owner-copy"
    marker.rename(backup)
    marker.symlink_to(backup)
    with pytest.raises(OSError):
        store.validate()
    with pytest.raises(storage.StateError, match="INVALID_PATHS"):
        storage.StateStore(store.state, PROJECT, store.state, store.workspace)


@pytest.mark.parametrize("value", [None, "not-a-uuid", PROJECT.upper(), 4])
def test_project_identity_must_be_canonical_uuid(value, tmp_path):
    with pytest.raises(storage.StateError, match="INVALID_IDENTITY"):
        storage.StateStore(tmp_path / "state", value, tmp_path / "home", tmp_path / "workspace")


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b"[]",
        b'{"deviceId":"x"}',
        b'{"deviceId":"x","deviceId":"x"}',
        storage.encoded({"deviceId": DEVICE, "extra": 1}),
        storage.encoded({"deviceId": DEVICE, "session": []}),
        storage.encoded({"deviceId": DEVICE, "session": {"access_token": "x"}}),
        storage.encoded(
            {"deviceId": DEVICE, "session": {"access_token": "", "refresh_token": "x"}}
        ),
        b" " * 65537,
    ],
)
def test_invalid_auth_is_not_journaled(store, raw):
    store.auth.parent.mkdir()
    store.auth.write_bytes(raw)
    with pytest.raises(storage.StateError):
        store.save_auth()
    assert not list(store.state.glob("auth-*"))


@pytest.mark.parametrize(
    "name,kind",
    [
        ("/workspace/escape", tarfile.REGTYPE),
        ("workspace/../escape", tarfile.REGTYPE),
        ("workspace/./escape", tarfile.REGTYPE),
        ("workspace//escape", tarfile.REGTYPE),
        ("workspace/back\\slash", tarfile.REGTYPE),
        ("workspace/control\n", tarfile.REGTYPE),
        ("workspace/" + "a" * 4096, tarfile.REGTYPE),
        ("home/other/secrets", tarfile.REGTYPE),
        ("home/.config/chromium/SingletonLock", tarfile.REGTYPE),
        ("home/.config/chromium/Default/Cache/blob", tarfile.REGTYPE),
        ("workspace/link", tarfile.SYMTYPE),
        ("workspace/link", tarfile.LNKTYPE),
        ("workspace/device", tarfile.CHRTYPE),
        ("workspace/pipe", tarfile.FIFOTYPE),
        ("workspace", tarfile.REGTYPE),
        ("home/.config/chromium", tarfile.REGTYPE),
    ],
)
def test_unsafe_archive_members_fail_before_local_install(store, name, kind):
    raw = archive_bytes([(name, kind, b"bad")])
    put_slot(store, "snapshot", 0, 1, raw)
    with pytest.raises(storage.StateError, match="STATE_CORRUPT"):
        store.restore()
    assert not list(store.home.iterdir())
    assert not list(store.workspace.iterdir())


def test_archive_bounds_duplicate_and_crc(store, monkeypatch, tmp_path):
    path = tmp_path / "archive"
    path.write_bytes(archive_bytes([("workspace/work", tarfile.REGTYPE, b"12345")]))
    monkeypatch.setattr(storage, "MAX_CONTENT", 4)
    with pytest.raises(storage.StateError, match="STATE_TOO_LARGE"):
        storage.inspect_archive(path)
    monkeypatch.setattr(storage, "MAX_CONTENT", 100)
    monkeypatch.setattr(storage, "MAX_FILES", 0)
    with pytest.raises(storage.StateError, match="UNSAFE_ARCHIVE"):
        storage.inspect_archive(path)
    monkeypatch.setattr(storage, "MAX_FILES", 100)
    path.write_bytes(
        archive_bytes(
            [("workspace/work", tarfile.REGTYPE, b"x"), ("workspace/work", tarfile.REGTYPE, b"x")]
        )
    )
    with pytest.raises(storage.StateError, match="UNSAFE_ARCHIVE"):
        storage.inspect_archive(path)
    good = archive_bytes([("workspace/work", tarfile.REGTYPE, b"x")])
    path.write_bytes(good[:-4])
    with pytest.raises(storage.StateError, match="INVALID_STATE"):
        storage.inspect_archive(path)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "socket"])
def test_unsafe_local_workspace_is_never_followed_or_uploaded(store, tmp_path, kind):
    other = tmp_path / "secret"
    other.write_text("secret outside workspace")
    target = store.workspace / "entry"
    connection = None
    if kind == "symlink":
        target.symlink_to(other)
    elif kind == "hardlink":
        os.link(other, target)
    else:
        connection = socket.socket(socket.AF_UNIX)
        connection.bind(str(target))
    try:
        with pytest.raises(storage.StateError, match="UNSAFE_LOCAL_STATE"):
            store.checkpoint()
        assert not list(store.state.glob("snapshot-*"))
        assert other.read_text() == "secret outside workspace"
    finally:
        if connection:
            connection.close()


def test_snapshot_creation_bounds_before_publication(store, monkeypatch):
    (store.workspace / "work").write_bytes(b"12345")
    monkeypatch.setattr(storage, "MAX_CONTENT", 4)
    with pytest.raises(storage.StateError, match="STATE_TOO_LARGE"):
        store.checkpoint()
    monkeypatch.setattr(storage, "MAX_CONTENT", 100)
    monkeypatch.setattr(storage, "MAX_FILES", 1)
    with pytest.raises(storage.StateError, match="STATE_TOO_LARGE"):
        store.checkpoint()
    monkeypatch.setattr(storage, "MAX_FILES", 100)
    monkeypatch.setattr(storage, "MAX_ARCHIVE", 1)
    with pytest.raises(storage.StateError, match="STATE_TOO_LARGE"):
        store.checkpoint()
    assert not list(store.state.glob("snapshot-*"))


@pytest.mark.parametrize("operation", ["status", "save-auth", "checkpoint"])
def test_cli_only_emits_sanitized_metadata(store, monkeypatch, capsys, operation):
    auth_file(store, "never-print-this-token")
    store.save_auth()
    monkeypatch.setattr(storage, "StateStore", lambda *args, **kwargs: store)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), operation])
    assert storage.main() == 0
    output = capsys.readouterr()
    assert not output.err
    value = json.loads(output.out)
    assert "never-print-this-token" not in output.out
    assert str(store.home) not in output.out
    assert set(value) == (
        {"state_ready", "paired", "device_id", "checkpoint_generation"}
        if operation == "status"
        else {"status", "generation"}
    )


def test_cli_safe_error_and_main_guard(monkeypatch, capsys):
    monkeypatch.delenv("ALICE_RDC_PROJECT_ID", raising=False)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "status"])
    assert storage.main() == 1
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err) == {"status": "STATE_FAILED", "error": "INVALID_IDENTITY"}
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert result.value.code == 1
    assert "INVALID_IDENTITY" in capsys.readouterr().err


def test_cli_io_failure_never_exposes_exception_message(store, monkeypatch, capsys):
    def forbidden():
        raise OSError("secret token and private path")

    monkeypatch.setattr(store, "status", forbidden)
    monkeypatch.setattr(storage, "StateStore", lambda *args, **kwargs: store)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "status"])
    assert storage.main() == 1
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err) == {"status": "STATE_FAILED", "error": "STATE_IO_FAILED"}


def test_mountinfo_detects_current_mount_and_absent_path(tmp_path):
    assert storage.is_mount("/") is True
    assert storage.is_mount(tmp_path / "not-a-mount") is False


def test_managed_mount_write_roundtrip_does_not_leave_test_objects(store, monkeypatch):
    original = storage.read_regular

    def mismatch(path, limit):
        return (
            b"wrong" if Path(path).name.startswith(".alice-rdc-check-") else original(path, limit)
        )

    monkeypatch.setattr(storage, "read_regular", mismatch)
    with pytest.raises(storage.StateError, match="STATE_MOUNT_UNAVAILABLE"):
        store.validate(writable=True)
    assert sorted(path.name for path in store.state.iterdir()) == ["owner.json"]


def test_journal_write_refuses_link_and_verifies_closed_bytes(store, tmp_path, monkeypatch):
    target = store.state / "auth-0.json"
    outside = tmp_path / "outside"
    outside.write_text("retain")
    target.symlink_to(outside)
    with pytest.raises(storage.StateError, match="INVALID_STATE"):
        store._write(target.name, b"replacement")
    assert outside.read_text() == "retain"
    target.unlink()
    original = storage.read_regular

    def mismatch(path, limit):
        return b"wrong" if Path(path).name == "auth-0.json" else original(path, limit)

    monkeypatch.setattr(storage, "read_regular", mismatch)
    with pytest.raises(storage.StateError, match="STATE_WRITE_FAILED"):
        store._write(target.name, b"replacement")


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", True),
        ("kind", "other"),
        ("generation", 0),
        ("generation", True),
        ("generation", storage.MAX_GENERATION + 1),
        ("auth_generation", -1),
        ("sha256", "z" * 64),
        ("sha256", 123),
    ],
)
def test_invalid_manifests_cannot_become_empty_state(store, field, value):
    raw = archive_bytes([("workspace", tarfile.DIRTYPE, b"")])
    metadata = put_slot(store, "snapshot", 0, 1, raw)
    metadata[field] = value
    (store.state / "snapshot-0.manifest.json").write_bytes(storage.encoded(metadata))
    with pytest.raises(storage.StateError, match="STATE_CORRUPT"):
        store.status()


def test_archive_auth_must_match_durable_journal(store):
    raw = archive_bytes(
        [
            (
                "home/.desktop-commander-device/device.json",
                tarfile.REGTYPE,
                storage.encoded({"deviceId": DEVICE, "session": None}),
            )
        ]
    )
    put_slot(store, "snapshot", 0, 1, raw)
    with pytest.raises(storage.StateError, match="AUTH_CORRUPT"):
        store.restore()


def test_workspace_owner_execute_survives_without_group_world_permissions(store):
    script = store.workspace / "run.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o777)
    store.checkpoint()
    clear_local(store)
    store.restore()
    assert stat.S_IMODE(script.stat().st_mode) == 0o700


def test_auth_validator_direct_size_bound():
    with pytest.raises(storage.StateError, match="AUTH_INVALID"):
        storage.normalized_auth(b" " * (storage.MAX_AUTH + 1))


def test_read_regular_rejects_concurrent_in_place_change(store, monkeypatch):
    target = store.workspace / "changing"
    target.write_bytes(b"old")
    original = storage.os.fstat
    first = True

    def change_after_stat(fd):
        nonlocal first
        result = original(fd)
        if first:
            first = False
            target.write_bytes(b"new and longer")
        return result

    monkeypatch.setattr(storage.os, "fstat", change_after_stat)
    with pytest.raises(storage.StateError, match="STATE_CHANGED"):
        storage.read_regular(target, 100)


def test_archive_allowed_ancestors_and_nonzero_trailing_data(tmp_path):
    path = tmp_path / "archive"
    raw = archive_bytes([("home", tarfile.DIRTYPE, b""), ("home/.config", tarfile.DIRTYPE, b"")])
    path.write_bytes(raw)
    storage.inspect_archive(path)
    path.write_bytes(raw + gzip.compress(b"\0" * 65536 + b"unexpected trailing data"))
    with pytest.raises(storage.StateError, match="UNSAFE_ARCHIVE"):
        storage.inspect_archive(path)


def test_decompression_budget_includes_extended_headers(monkeypatch):
    monkeypatch.setattr(storage, "MAX_CONTENT", 0)
    monkeypatch.setattr(storage, "MAX_FILES", 0)
    source = storage.BoundedGzip(io.BytesIO(b"\0" * (1024 * 1024 + 1)))
    with pytest.raises(storage.StateError, match="STATE_TOO_LARGE"):
        source.read(1024 * 1024 + 1)


def test_unexpected_short_archive_reader_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "archive"
    path.write_bytes(archive_bytes([("workspace/work", tarfile.REGTYPE, b"promised content")]))
    monkeypatch.setattr(storage.tarfile.TarFile, "extractfile", lambda *args: io.BytesIO(b""))
    with pytest.raises(storage.StateError, match="INVALID_STATE"):
        storage.inspect_archive(path)


def test_post_open_mount_file_type_check_before_write(store, monkeypatch):
    target = store.state / "auth-0.json"
    target.write_bytes(b"old")
    original = storage.os.fstat

    def unexpected_hardlink(fd):
        value = original(fd)
        return SimpleNamespace(st_mode=value.st_mode, st_nlink=2)

    monkeypatch.setattr(storage.os, "fstat", unexpected_hardlink)
    with pytest.raises(storage.StateError, match="INVALID_STATE"):
        store._write(target.name, b"replacement")


def test_incomplete_metadata_and_wrong_auth_generation_fail_closed(store):
    path = store.state / "snapshot-0.manifest.json"
    path.write_bytes(storage.encoded({"schema": 1}))
    with pytest.raises(storage.StateError, match="STATE_CORRUPT"):
        store.status()
    path.unlink()
    metadata = put_slot(store, "auth", 0, 1, storage.encoded({"deviceId": DEVICE, "session": None}))
    metadata["auth_generation"] = 0
    (store.state / "auth-0.manifest.json").write_bytes(storage.encoded(metadata))
    with pytest.raises(storage.StateError, match="STATE_CORRUPT"):
        store.status()


@pytest.mark.parametrize("kind", ["auth", "snapshot"])
def test_closed_slot_write_requires_the_committed_generation(store, monkeypatch, kind):
    if kind == "auth":
        auth_file(store, "one")
        store.save_auth()
        auth_file(store, "two")
    else:
        store.checkpoint()
    original = store._write

    def lost_write(name, raw):
        if name.startswith(kind + "-1"):
            return
        original(name, raw)

    monkeypatch.setattr(store, "_write", lost_write)
    with pytest.raises(storage.StateError, match="STATE_WRITE_FAILED"):
        (store.save_auth if kind == "auth" else store.checkpoint)()


@pytest.mark.parametrize("when", ["before_open", "after_read"])
def test_checkpoint_rejects_file_identity_or_content_change(store, monkeypatch, when):
    target = store.workspace / "work"
    target.write_bytes(b"original")
    if when == "before_open":
        original = storage.os.open

        def replaced_before_open(path, flags, *args, **kwargs):
            if path == "work" and kwargs.get("dir_fd") is not None:
                target.rename(store.workspace / "old-work")
                target.write_bytes(b"replacement")
            return original(path, flags, *args, **kwargs)

        monkeypatch.setattr(storage.os, "open", replaced_before_open)
    else:
        original = storage.tarfile.TarFile.addfile

        def changed_after_read(archive, info, *args):
            result = original(archive, info, *args)
            if info.name == "workspace/work":
                target.write_bytes(b"changed after read with different size")
            return result

        monkeypatch.setattr(storage.tarfile.TarFile, "addfile", changed_after_read)
    with pytest.raises(storage.StateError, match="STATE_CHANGED"):
        store.checkpoint()
    assert not list(store.state.glob("snapshot-*"))


def test_local_install_refuses_existing_file_even_after_staging(store, tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "work").write_text("new")
    (store.workspace / "work").write_text("retain")
    with pytest.raises(storage.StateError, match="LOCAL_STATE_NOT_EMPTY"):
        store._install(staging, store.workspace)
    assert (store.workspace / "work").read_text() == "retain"


@pytest.mark.parametrize("reverse", [False, True])
def test_archive_file_directory_contradiction_rejected_before_restore(store, reverse):
    members = [
        ("workspace/file", tarfile.REGTYPE, b"file"),
        ("workspace/file/child", tarfile.REGTYPE, b"child"),
    ]
    put_slot(store, "snapshot", 0, 1, archive_bytes(members[::-1] if reverse else members))
    with pytest.raises(storage.StateError, match="STATE_CORRUPT"):
        store.status()


def test_restore_across_real_filesystems_stages_under_home(store, monkeypatch):
    second_filesystem = Path("/dev/shm")
    if not second_filesystem.is_dir() or not os.access(second_filesystem, os.W_OK):
        pytest.skip("a second writable local filesystem is unavailable")
    if second_filesystem.stat().st_dev == store.workspace.stat().st_dev:
        pytest.skip("test paths do not reside on different filesystems")
    auth_file(store, "durable-session")
    profile = store.home / ".config/chromium/Default"
    profile.mkdir(parents=True)
    (profile / "Cookies").write_bytes(b"closed-profile")
    script = store.workspace / "run.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o751)
    store.checkpoint()
    durable = {path.name: path.read_bytes() for path in store.state.iterdir()}
    with tempfile.TemporaryDirectory(prefix="rdc-recovery-test-", dir=second_filesystem) as mounted:
        home = Path(mounted, "home")
        home.mkdir()
        workspace = store.workspace.parent / "restored-workspace"
        workspace.mkdir()
        restored = storage.StateStore(store.state, PROJECT, home, workspace)
        original = storage.tempfile.TemporaryDirectory
        staging_paths = []

        def private_home_staging(*args, **kwargs):
            assert kwargs["dir"] == home
            directory = original(*args, **kwargs)
            path = Path(directory.name)
            assert path.parent == home
            assert stat.S_IMODE(path.stat().st_mode) == 0o700
            staging_paths.append(path)
            return directory

        monkeypatch.setattr(storage.tempfile, "TemporaryDirectory", private_home_staging)
        assert restored.restore() == {"status": "STATE_RESTORED", "generation": 1}
        assert (
            json.loads(restored.auth.read_bytes())["session"]["access_token"] == "durable-session"
        )
        assert (home / ".config/chromium/Default/Cookies").read_bytes() == b"closed-profile"
        assert (workspace / "run.sh").read_text() == "#!/bin/sh\nexit 0\n"
        assert stat.S_IMODE((workspace / "run.sh").stat().st_mode) == 0o700
        assert stat.S_IMODE(restored.auth.stat().st_mode) == 0o600
        assert home.stat().st_dev != workspace.stat().st_dev
        assert staging_paths and all(not path.exists() for path in staging_paths)
        assert {path.name: path.read_bytes() for path in store.state.iterdir()} == durable


def test_failed_copy_removes_only_its_partial_destination(store, tmp_path, monkeypatch):
    staging = tmp_path / "private-staging"
    staging.mkdir(mode=0o700)
    source = staging / "work"
    content = b"closed workspace contents"
    source.write_bytes(content)
    original = storage.os.fdopen

    class DiskFullWriter:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def write(self, block):
            self.handle.write(block[:3])
            self.handle.flush()
            raise OSError(errno.ENOSPC, "synthetic local disk full")

    def disk_full(fd, mode):
        handle = original(fd, mode)
        return DiskFullWriter(handle) if mode == "wb" else handle

    monkeypatch.setattr(storage.os, "fdopen", disk_full)
    with pytest.raises(OSError) as failure:
        store._install(staging, store.workspace)
    assert failure.value.errno == errno.ENOSPC
    assert source.read_bytes() == content
    assert not list(store.workspace.iterdir())


@pytest.mark.parametrize("change", ["grow", "shrink", "mtime"])
def test_copy_detects_changed_staging_without_publishing_file(store, tmp_path, monkeypatch, change):
    staging = tmp_path / "private-staging"
    staging.mkdir(mode=0o700)
    source = staging / "work"
    source.write_bytes(b"original")
    original = storage.os.fdopen

    class ChangedReader:
        def __init__(self, handle):
            self.handle = handle
            self.changed = False

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def fileno(self):
            return self.handle.fileno()

        def read(self, size):
            if not self.changed:
                self.changed = True
                if change == "grow":
                    source.write_bytes(b"unexpected larger file")
                elif change == "shrink":
                    source.write_bytes(b"")
                else:
                    value = source.stat()
                    os.utime(source, ns=(value.st_atime_ns, value.st_mtime_ns + 1))
            return self.handle.read(size)

    def changed(fd, mode):
        handle = original(fd, mode)
        return ChangedReader(handle) if mode == "rb" else handle

    monkeypatch.setattr(storage.os, "fdopen", changed)
    with pytest.raises(storage.StateError, match="STATE_CHANGED"):
        store._install(staging, store.workspace)
    assert source.exists()
    assert not list(store.workspace.iterdir())


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "oversize"])
def test_copy_refuses_unsafe_or_oversized_staging(store, tmp_path, monkeypatch, kind):
    staging = tmp_path / "private-staging"
    staging.mkdir(mode=0o700)
    outside = tmp_path / "retain"
    outside.write_bytes(b"private outside contents")
    source = staging / "work"
    if kind == "symlink":
        source.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, source)
    elif kind == "fifo":
        os.mkfifo(source)
    else:
        source.write_bytes(b"more than four bytes")
        monkeypatch.setattr(storage, "MAX_CONTENT", 4)
    with pytest.raises((storage.StateError, OSError)):
        store._install(staging, store.workspace)
    assert outside.read_bytes() == b"private outside contents"
    assert not list(store.workspace.iterdir())


def test_exclusive_copy_preserves_a_destination_created_during_install(
    store, tmp_path, monkeypatch
):
    staging = tmp_path / "private-staging"
    staging.mkdir(mode=0o700)
    source = staging / "work"
    source.write_bytes(b"new contents")
    outside = tmp_path / "retain"
    outside.write_bytes(b"existing contents")
    original = storage.os.open

    def raced_destination(path, flags, *args, **kwargs):
        if path == "work" and flags & os.O_CREAT:
            os.symlink(outside, path, dir_fd=kwargs["dir_fd"])
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(storage.os, "open", raced_destination)
    with pytest.raises(FileExistsError):
        store._install(staging, store.workspace)
    assert source.read_bytes() == b"new contents"
    assert outside.read_bytes() == b"existing contents"
    assert (store.workspace / "work").is_symlink()
