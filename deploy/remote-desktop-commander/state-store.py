#!/usr/bin/env python3
"""Closed POSIX snapshots on a managed Object Storage mount; no app credentials.

The supervisor MUST stop RDC and Chromium before checkpoint. Object Storage is
used only for regular files: writes are closed and verified, with no rename,
fsync, locks, links or live browser databases on the mount. Workspace links are
unsupported. An abrupt loss can lose changes since the last closed checkpoint.
"""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
import tempfile
import uuid


MAX_ARCHIVE = 128 * 1024 * 1024
MAX_CONTENT = 256 * 1024 * 1024
MAX_FILES = 20000
MAX_AUTH = 65536
MAX_METADATA = 4096
MAX_GENERATION = 2**53 - 1
HOME_TREES = (".desktop-commander-device", ".claude-server-commander", ".config/chromium")
CACHE_DIRS = {"Cache", "Code Cache", "GPUCache", "ShaderCache", "GrShaderCache", "DawnCache"}


class StateError(Exception):
    """Only fixed error codes may leave this helper."""


def fail(code):
    raise StateError(code)


def canonical_uuid(value):
    try:
        parsed = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        fail("INVALID_IDENTITY")
    if value != parsed:
        fail("INVALID_IDENTITY")
    return parsed


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def object_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                fail("INVALID_STATE")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError):
        fail("INVALID_STATE")
    if not isinstance(value, dict):
        fail("INVALID_STATE")
    return value


def normalized_auth(raw):
    if len(raw) > MAX_AUTH:
        fail("AUTH_INVALID")
    value = object_json(raw)
    if set(value) - {"deviceId", "session"} or "deviceId" not in value:
        fail("AUTH_INVALID")
    device_id = canonical_uuid(value["deviceId"])
    session = value.get("session")
    if session is not None:
        if not isinstance(session, dict) or set(session) != {"access_token", "refresh_token"}:
            fail("AUTH_INVALID")
        if any(
            not isinstance(token, str) or not token or len(token) > 16384
            for token in session.values()
        ):
            fail("AUTH_INVALID")
    return {"deviceId": device_id, "session": session}


def directory_fd(path):
    """Open every component without following a symlink."""
    path = Path(path).absolute()
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_regular(path, limit):
    parent = directory_fd(Path(path).parent)
    try:
        fd = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            fail("INVALID_STATE")
        raw = handle.read(limit + 1)
        after = os.fstat(handle.fileno())
        if len(raw) > limit or (before.st_size, before.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ):
            fail("STATE_CHANGED")
        return raw


def is_mount(path):
    escaped = (
        str(Path(path).absolute())
        .replace("\\", "\\134")
        .replace(" ", "\\040")
        .replace("\t", "\\011")
        .replace("\n", "\\012")
    )
    with open("/proc/self/mountinfo", encoding="utf-8") as handle:
        return any(line.split()[4] == escaped for line in handle)


def safe_member(member):
    name = member.name
    parts = name.split("/")
    if not name or len(name.encode()) > 4096 or any(part in {"", ".", ".."} for part in parts):
        fail("UNSAFE_ARCHIVE")
    if any(ord(c) < 32 or ord(c) == 127 for c in name) or "\\" in name:
        fail("UNSAFE_ARCHIVE")
    if not (member.isfile() or member.isdir()) or member.issparse():
        fail("UNSAFE_ARCHIVE")
    if parts[0] == "workspace":
        if name == "workspace" and not member.isdir():
            fail("UNSAFE_ARCHIVE")
        return
    if name in {"home", "home/.config"} and member.isdir():
        return
    if not any(
        name == "home/" + tree or name.startswith("home/" + tree + "/") for tree in HOME_TREES
    ):
        fail("UNSAFE_ARCHIVE")
    if name in {"home/" + tree for tree in HOME_TREES} and not member.isdir():
        fail("UNSAFE_ARCHIVE")
    if name.startswith("home/.config/chromium/") and any(
        part.startswith("Singleton") or part in CACHE_DIRS for part in parts[3:]
    ):
        fail("UNSAFE_ARCHIVE")


class BoundedGzip:
    """Bound expanded bytes including tar's extended headers and padding."""

    def __init__(self, source):
        self.source = source
        self.remaining = MAX_CONTENT + MAX_FILES * 8192 + 1024 * 1024

    def read(self, size):
        block = self.source.read(min(size, self.remaining + 1))
        self.remaining -= len(block)
        if self.remaining < 0:
            fail("STATE_TOO_LARGE")
        return block


def inspect_archive(path, destination=None):
    seen = {}
    total = 0
    try:
        with gzip.open(path, "rb") as compressed:
            bounded = BoundedGzip(compressed)
            _inspect_tar(bounded, destination, seen, total)
    except (tarfile.TarError, EOFError, gzip.BadGzipFile, ValueError):
        fail("INVALID_STATE")


def _inspect_tar(compressed, destination, seen, total):
    required_dirs = set()
    with tarfile.open(fileobj=compressed, mode="r|") as archive:
        for member in archive:
            safe_member(member)
            if member.name in seen or len(seen) >= MAX_FILES:
                fail("UNSAFE_ARCHIVE")
            parts = member.name.split("/")
            ancestors = {"/".join(parts[:index]) for index in range(1, len(parts))}
            if any(seen.get(name) is False for name in ancestors) or (
                member.isfile() and member.name in required_dirs
            ):
                fail("UNSAFE_ARCHIVE")
            required_dirs.update(ancestors)
            seen[member.name] = member.isdir()
            total += member.size
            if total > MAX_CONTENT or member.size < 0:
                fail("STATE_TOO_LARGE")
            target = Path(destination, *PurePosixPath(member.name).parts) if destination else None
            if member.isdir():
                if target:
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    target.chmod(0o700)
                continue
            source = archive.extractfile(member)
            output = None
            if target:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                output = open(target, "xb")
                os.chmod(
                    target,
                    0o700
                    if member.name.startswith("workspace/") and member.mode & 0o100
                    else 0o600,
                )
            try:
                remaining = member.size
                while remaining:
                    block = source.read(min(65536, remaining))
                    if not block:
                        fail("INVALID_STATE")
                    remaining -= len(block)
                    if output:
                        output.write(block)
            finally:
                source.close()
                if output:
                    output.close()
        # Read through the gzip trailer so CRC/truncation errors cannot hide
        # after tar's end marker; reject nonzero unbuffered trailing data.
        tail_size = 0
        while block := compressed.read(65536):
            tail_size += len(block)
            if tail_size > 1024 * 1024 or any(block):
                fail("UNSAFE_ARCHIVE")


class StateStore:
    def __init__(
        self,
        state_path,
        project_id,
        home_path="/home/node",
        workspace_path="/workspace",
        require_mount=False,
    ):
        self.state = Path(state_path).absolute()
        self.project = canonical_uuid(project_id)
        self.home = Path(home_path).absolute()
        self.workspace = Path(workspace_path).absolute()
        self.auth = self.home / ".desktop-commander-device/device.json"
        self.require_mount = require_mount
        roots = (self.state, self.home, self.workspace)
        if any(
            first == second or first in second.parents or second in first.parents
            for i, first in enumerate(roots)
            for second in roots[i + 1 :]
        ):
            fail("INVALID_PATHS")

    def validate(self, writable=False):
        fd = directory_fd(self.state)
        os.close(fd)
        if self.require_mount and not is_mount(self.state):
            fail("STATE_NOT_MOUNTED")
        expected = {
            "schema": 1,
            "purpose": "alice-rdc",
            "project_id": self.project,
            "container_name": "rdc-" + uuid.UUID(self.project).hex[:12],
        }
        owner = object_json(read_regular(self.state / "owner.json", MAX_METADATA))
        if owner != expected or type(owner.get("schema")) is not int:
            fail("OWNERSHIP_MISMATCH")
        if writable:
            name = ".alice-rdc-check-" + uuid.uuid4().hex
            target = self.state / name
            created = False
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                created = True
                with os.fdopen(fd, "wb") as handle:
                    handle.write(b"alice-rdc-state-check\n")
                if read_regular(target, 128) != b"alice-rdc-state-check\n":
                    fail("STATE_MOUNT_UNAVAILABLE")
            finally:
                if created:
                    target.unlink()

    def _write(self, name, data):
        target = self.state / name
        try:
            previous = target.lstat()
            if not stat.S_ISREG(previous.st_mode) or previous.st_nlink != 1:
                fail("INVALID_STATE")
        except FileNotFoundError:
            pass
        fd = os.open(
            target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
        )
        with os.fdopen(fd, "wb") as handle:
            current = os.fstat(handle.fileno())
            if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                fail("INVALID_STATE")
            handle.write(data)
        if read_regular(target, max(len(data), 1)) != data:
            fail("STATE_WRITE_FAILED")

    def _metadata(self, name, kind):
        value = object_json(read_regular(self.state / name, MAX_METADATA))
        if set(value) != {"schema", "kind", "generation", "sha256", "size", "auth_generation"}:
            fail("INVALID_STATE")
        if value["schema"] != 1 or type(value["schema"]) is not int or value["kind"] != kind:
            fail("INVALID_STATE")
        for key in ("generation", "size", "auth_generation"):
            if type(value[key]) is not int or value[key] < (1 if key == "generation" else 0):
                fail("INVALID_STATE")
        if value["generation"] > MAX_GENERATION or value["auth_generation"] > MAX_GENERATION:
            fail("INVALID_STATE")
        digest = value["sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            fail("INVALID_STATE")
        return value

    def _slots(self, kind):
        valid = []
        invalid = []
        present = False
        limit = MAX_AUTH if kind == "auth" else MAX_ARCHIVE
        suffix = "json" if kind == "auth" else "tar.gz"
        for slot in (0, 1):
            name = f"{kind}-{slot}.{suffix}"
            manifest = f"{kind}-{slot}.manifest.json"
            if not os.path.lexists(self.state / name) and not os.path.lexists(
                self.state / manifest
            ):
                continue
            present = True
            metadata = None
            try:
                metadata = self._metadata(manifest, kind)
                raw = read_regular(self.state / name, limit)
                if (
                    len(raw) != metadata["size"]
                    or hashlib.sha256(raw).hexdigest() != metadata["sha256"]
                ):
                    fail("INVALID_STATE")
                if kind == "auth":
                    auth = normalized_auth(raw)
                    if metadata["auth_generation"] != metadata["generation"]:
                        fail("INVALID_STATE")
                else:
                    auth = None
                    with tempfile.NamedTemporaryFile() as local:
                        local.write(raw)
                        local.flush()
                        inspect_archive(local.name)
                valid.append({"slot": slot, "metadata": metadata, "raw": raw, "auth": auth})
            except (StateError, OSError):
                invalid.append(metadata)
        if not valid:
            if present:
                fail("STATE_CORRUPT")
            return None
        valid.sort(key=lambda item: item["metadata"]["generation"])
        newest = valid[-1]
        if (
            len(valid) == 2
            and valid[0]["metadata"]["generation"] == newest["metadata"]["generation"]
            and valid[0]["metadata"] != newest["metadata"]
        ):
            fail("STATE_CONFLICT")
        if kind == "auth" and invalid:
            # Even an older slot's manifest may precede a partial overwrite for
            # a newer rotation. Its previous refresh token may already be spent.
            # No corrupt auth slot is safe evidence for token rollback.
            fail("AUTH_CORRUPT")
        if kind == "auth" and any(
            item["auth"]["deviceId"] != newest["auth"]["deviceId"] for item in valid
        ):
            fail("AUTH_IDENTITY_CHANGED")
        return newest

    def _durable(self):
        snapshot = self._slots("snapshot")
        auth = self._slots("auth")
        if snapshot and snapshot["metadata"]["auth_generation"] > (
            auth["metadata"]["generation"] if auth else 0
        ):
            fail("AUTH_CORRUPT")
        return snapshot, auth

    def save_auth(self):
        self.validate(writable=True)
        previous = self._slots("auth")
        try:
            auth = normalized_auth(read_regular(self.auth, MAX_AUTH))
        except FileNotFoundError:
            if previous:
                fail("AUTH_MISSING")
            return {"status": "AUTH_ABSENT", "generation": 0}
        raw = encoded(auth)
        if previous and previous["auth"]["deviceId"] != auth["deviceId"]:
            fail("AUTH_IDENTITY_CHANGED")
        if previous and previous["raw"] == raw:
            return {"status": "AUTH_SAVED", "generation": previous["metadata"]["generation"]}
        generation = previous["metadata"]["generation"] + 1 if previous else 1
        slot = 1 - previous["slot"] if previous else 0
        metadata = {
            "schema": 1,
            "kind": "auth",
            "generation": generation,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
            "auth_generation": generation,
        }
        self._write(f"auth-{slot}.json", raw)
        self._write(f"auth-{slot}.manifest.json", encoded(metadata))
        if self._slots("auth")["metadata"]["generation"] != generation:
            fail("STATE_WRITE_FAILED")
        return {"status": "AUTH_SAVED", "generation": generation}

    def status(self):
        self.validate()
        snapshot, auth = self._durable()
        device = auth["auth"] if auth else None
        return {
            "state_ready": True,
            "paired": bool(device and device["session"]),
            "device_id": device["deviceId"] if device else None,
            "checkpoint_generation": snapshot["metadata"]["generation"] if snapshot else 0,
        }

    def restore(self):
        self.validate(writable=True)
        snapshot, auth = self._durable()
        for base, trees in ((self.home, HOME_TREES), (self.workspace, (".",))):
            fd = directory_fd(base)
            os.close(fd)
            for tree in trees:
                target = base / tree
                for ancestor in target.parents:
                    if ancestor == base:
                        break
                    if os.path.lexists(ancestor):
                        fd = directory_fd(ancestor)
                        os.close(fd)
                if os.path.lexists(target):
                    fd = directory_fd(target)
                    try:
                        if os.listdir(fd):
                            fail("LOCAL_STATE_NOT_EMPTY")
                    finally:
                        os.close(fd)
        # Expanded state belongs on local home storage, not Compose's bounded
        # /tmp tmpfs and never on the Object Storage mount.
        with tempfile.TemporaryDirectory(prefix=".rdc-restore-", dir=self.home) as staging:
            if snapshot:
                archive_path = Path(staging, "snapshot.tar.gz")
                archive_path.write_bytes(snapshot["raw"])
                inspect_archive(archive_path, Path(staging, "restored"))
                archived_auth = Path(staging, "restored/home/.desktop-commander-device/device.json")
                if archived_auth.exists():
                    device = normalized_auth(read_regular(archived_auth, MAX_AUTH))
                    if not auth or device["deviceId"] != auth["auth"]["deviceId"]:
                        fail("AUTH_CORRUPT")
            if auth:
                target = Path(staging, "restored/home/.desktop-commander-device/device.json")
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                target.write_bytes(encoded(auth["auth"]))
                target.chmod(0o600)
            restored = Path(staging, "restored")
            if restored.exists():
                # All paths were validated before touching live state. Local
                # POSIX files can be installed normally; no Object Storage rename.
                for relative, destination in [
                    ("home/" + tree, self.home / tree) for tree in HOME_TREES
                ] + [("workspace", self.workspace)]:
                    source = restored / relative
                    if source.exists():
                        if relative == "home/.config/chromium":
                            self.home.joinpath(".config").mkdir(mode=0o700, exist_ok=True)
                            self.home.joinpath(".config").chmod(0o700)
                        self._install(source, destination)
        return {
            "status": "STATE_RESTORED",
            "generation": snapshot["metadata"]["generation"] if snapshot else 0,
        }

    @staticmethod
    def _install(source, target):
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = directory_fd(target)
        try:
            os.fchmod(fd, 0o700)
            for child in source.iterdir():
                destination = target / child.name
                if stat.S_ISDIR(child.lstat().st_mode):
                    StateStore._install(child, destination)
                else:
                    if os.path.lexists(destination):
                        fail("LOCAL_STATE_NOT_EMPTY")
                    StateStore._copy_file(child, fd)
        finally:
            os.close(fd)

    @staticmethod
    def _copy_file(child, target_fd):
        """Private exclusive copies work across separate local filesystems."""
        source_parent = directory_fd(child.parent)
        created = False
        complete = False
        try:
            source_fd = os.open(
                child.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_parent
            )
            with os.fdopen(source_fd, "rb") as source:
                before = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_nlink != 1
                    or before.st_size > MAX_CONTENT
                ):
                    fail("INVALID_STATE")
                output_fd = os.open(
                    child.name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=target_fd,
                )
                created = True
                with os.fdopen(output_fd, "wb") as output:
                    copied = 0
                    while block := source.read(65536):
                        copied += len(block)
                        if copied > before.st_size:
                            fail("STATE_CHANGED")
                        output.write(block)
                    after = os.fstat(source.fileno())
                    if copied != before.st_size or (
                        before.st_size,
                        before.st_mtime_ns,
                    ) != (after.st_size, after.st_mtime_ns):
                        fail("STATE_CHANGED")
                    os.fchmod(output.fileno(), 0o700 if before.st_mode & 0o100 else 0o600)
            os.unlink(child.name, dir_fd=source_parent)
            complete = True
        finally:
            if created and not complete:
                os.unlink(child.name, dir_fd=target_fd)
            os.close(source_parent)

    def checkpoint(self):
        """Caller has quiesced both processes; never call on open databases."""
        self.validate(writable=True)
        previous, _ = self._durable()
        auth_generation = self.save_auth()["generation"]
        generation = previous["metadata"]["generation"] + 1 if previous else 1
        with tempfile.NamedTemporaryFile() as local:
            count = [0, 0]
            with tarfile.open(fileobj=local, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
                for tree in HOME_TREES:
                    root = self.home / tree
                    if os.path.lexists(root):
                        self._archive_tree(archive, root, "home/" + tree, count)
                if os.path.lexists(self.workspace):
                    self._archive_tree(archive, self.workspace, "workspace", count)
            local.flush()
            if local.tell() > MAX_ARCHIVE:
                fail("STATE_TOO_LARGE")
            inspect_archive(local.name)
            local.seek(0)
            raw = local.read()
        slot = 1 - previous["slot"] if previous else 0
        metadata = {
            "schema": 1,
            "kind": "snapshot",
            "generation": generation,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
            "auth_generation": auth_generation,
        }
        self._write(f"snapshot-{slot}.tar.gz", raw)
        self._write(f"snapshot-{slot}.manifest.json", encoded(metadata))
        if self._slots("snapshot")["metadata"]["generation"] != generation:
            fail("STATE_WRITE_FAILED")
        return {"status": "CHECKPOINT_COMPLETE", "generation": generation}

    def _archive_tree(self, archive, root, prefix, count):
        fd = directory_fd(root)
        try:
            self._archive_directory(archive, fd, prefix, count)
        finally:
            os.close(fd)

    def _archive_directory(self, archive, fd, prefix, count):
        info = tarfile.TarInfo(prefix)
        info.type = tarfile.DIRTYPE
        info.mode = 0o700
        archive.addfile(info)
        count[0] += 1
        for name in sorted(os.listdir(fd)):
            if prefix.startswith("home/.config/chromium") and (
                name.startswith("Singleton") or name in CACHE_DIRS
            ):
                continue
            count[0] += 1
            if count[0] > MAX_FILES:
                fail("STATE_TOO_LARGE")
            entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(entry.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    self._archive_directory(archive, child, prefix + "/" + name, count)
                finally:
                    os.close(child)
            elif stat.S_ISREG(entry.st_mode) and entry.st_nlink == 1:
                count[1] += entry.st_size
                if count[1] > MAX_CONTENT:
                    fail("STATE_TOO_LARGE")
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                with os.fdopen(child, "rb") as handle:
                    before = os.fstat(handle.fileno())
                    if (
                        not stat.S_ISREG(before.st_mode)
                        or before.st_nlink != 1
                        or (before.st_dev, before.st_ino, before.st_size)
                        != (entry.st_dev, entry.st_ino, entry.st_size)
                    ):
                        fail("STATE_CHANGED")
                    info = tarfile.TarInfo(prefix + "/" + name)
                    info.size = before.st_size
                    info.mode = (
                        0o700
                        if prefix.startswith("workspace") and before.st_mode & 0o100
                        else 0o600
                    )
                    safe_member(info)
                    archive.addfile(info, handle)
                    after = os.fstat(handle.fileno())
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        fail("STATE_CHANGED")
            else:
                fail("UNSAFE_LOCAL_STATE")


def main():
    parser = argparse.ArgumentParser(description="RDC closed state storage")
    parser.add_argument("operation", choices=("restore", "save-auth", "checkpoint", "status"))
    args = parser.parse_args()
    try:
        store = StateStore(
            os.environ.get("ALICE_RDC_STATE_PATH", "/rdc-state"),
            os.environ.get("ALICE_RDC_PROJECT_ID"),
            require_mount=True,
        )
        result = getattr(store, args.operation.replace("-", "_"))()
    except StateError as error:
        print(json.dumps({"status": "STATE_FAILED", "error": str(error)}), file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, RecursionError):
        print('{"status":"STATE_FAILED","error":"STATE_IO_FAILED"}', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
