"""Collect immutable reachable Git evidence for the existing repository RAG.

No checkout file, Git filter, hook, remote, or model is executed. The caller owns
fetching the authorized refs first; gaps describe the observed local boundary.
"""

from __future__ import annotations
from core.env import env_snapshot

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote, quote_from_bytes

from scripts.semantic_pipeline import _repository_path_is_sensitive

MAX_OBJECT_BYTES = 128 * 1024 * 1024
_OBJECT_ID = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")
_LFS_POINTER = re.compile(
    rb"version https://git-lfs.github.com/spec/v1\r?\n"
    rb"(?:ext-[^\n]+\r?\n)*oid sha256:([a-f0-9]{64})\r?\nsize ([0-9]+)\r?\n?\Z"
)


class GitReadError(ValueError):
    """Safe error without Git diagnostics, source contents, or credentials."""


def _json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode()


def _git(root: Path, *args: str) -> bytes:
    env = {
        **env_snapshot(),
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        return subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            timeout=120,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise GitReadError("git_read_failed") from None


def _read_object(root: Path, oid: str, kind: str, sink) -> bytes | None:
    if not _OBJECT_ID.fullmatch(oid):
        raise GitReadError("git_object_id_invalid")
    source_id = f"git:{kind}:{oid}"
    try:
        size = int(_git(root, "cat-file", "-s", oid))
        if size > MAX_OBJECT_BYTES:
            sink.gap(kind, source_id, "git_object_size_limit", {"bytes": size})
            return None
        raw = _git(root, "cat-file", kind, oid)
        algorithm = "sha1" if len(oid) == 40 else "sha256"
        digest = hashlib.new(
            algorithm, f"{kind} {len(raw)}\0".encode() + raw
        ).hexdigest()
        if digest != oid or size != len(raw):
            sink.gap(kind, source_id, "git_object_hash_mismatch")
            return None
        return raw
    except (GitReadError, ValueError):
        sink.gap(kind, source_id, "git_object_unavailable")
        return None


def _changes(root: Path, sha: str, parent: str | None) -> list[dict]:
    args = [
        "diff-tree",
        "--no-commit-id",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        "-r",
        "--raw",
        "-z",
    ]
    args.extend([parent, sha] if parent else ["--root", sha])
    fields = _git(root, *args).split(b"\0")
    changes = []
    for index in range(0, len(fields) - 1, 2):
        details, path_bytes = fields[index : index + 2]
        if not details.startswith(b":"):
            raise GitReadError("git_changes_invalid")
        old_mode, new_mode, old_oid, new_oid, status = (
            details[1:].decode("ascii").split()
        )
        path = path_bytes.decode("utf-8", errors="replace")
        changes.append(
            {
                "path": path,
                "path_hex": path_bytes.hex(),
                "old_mode": old_mode,
                "mode": new_mode,
                "old_oid": old_oid,
                "oid": new_oid,
                "status": status,
                "source_url_path": quote_from_bytes(path_bytes, safe="/"),
            }
        )
    return changes


def _lfs_payload(root: Path, raw: bytes, sink, url: str, pointer_oid: str) -> None:
    match = _LFS_POINTER.fullmatch(raw)
    if not match:
        if raw.startswith(b"version https://git-lfs.github.com/spec/"):
            sink.gap("git_lfs", f"git:lfs-pointer:{pointer_oid}", "lfs_pointer_invalid")
        return
    oid, expected_size = match[1].decode(), int(match[2])
    source_id = f"git:lfs:{oid}"
    metadata = {
        "lfs_oid_sha256": oid,
        "bytes": expected_size,
        "pointer_git_oid": pointer_oid,
    }
    # Standard local object store only. Custom storage can point outside the
    # authorized repository and requires a separately verified storage source.
    common = Path(os.fsdecode(_git(root, "rev-parse", "--git-common-dir").strip()))
    if not common.is_absolute():
        common = root / common
    path = common / "lfs" / "objects" / oid[:2] / oid[2:4] / oid
    if expected_size > MAX_OBJECT_BYTES:
        sink.gap("git_lfs", source_id, "lfs_object_size_limit", metadata)
        return
    try:
        candidate = common
        for part in ("lfs", "objects", oid[:2], oid[2:4], oid):
            candidate = candidate / part
            if candidate.is_symlink():
                sink.gap("git_lfs", source_id, "lfs_object_symlink", metadata)
                return
        with path.open("rb") as stream:
            content = stream.read(MAX_OBJECT_BYTES + 1)
    except OSError:
        sink.gap("git_lfs", source_id, "lfs_payload_not_available_locally", metadata)
        return
    if len(content) != expected_size or hashlib.sha256(content).hexdigest() != oid:
        sink.gap("git_lfs", source_id, "lfs_payload_hash_or_size_mismatch", metadata)
        return
    sink.add("git_lfs", source_id, url, oid, content, metadata)


def _is_partial_clone(root: Path) -> bool:
    # Git versions vary in GIT_NO_LAZY_FETCH support. Reject configured
    # promisor/partial clones before any object read to guarantee no fetch.
    config = _git(root, "config", "--list").decode("utf-8", errors="replace")
    return any(
        line.lower().startswith("extensions.partialclone=")
        or re.match(r"remote\..*\.promisor=", line, re.I)
        or re.match(r"remote\..*\.partialclonefilter=", line, re.I)
        for line in config.splitlines()
    )


def _collect_refs(
    root: Path, sink, base: str
) -> tuple[list[bytes], set[str], set[str]]:
    refs = _git(
        root, "for-each-ref", "--format=%(refname)%00%(objectname)%00%(objecttype)"
    ).splitlines()
    tips, tags = set(), set()
    for line in refs:
        name_bytes, oid_bytes, kind_bytes = line.split(b"\0")
        name, oid, kind = name_bytes.decode(), oid_bytes.decode(), kind_bytes.decode()
        if name.startswith("refs/replace/"):
            sink.gap("git_ref", f"git:ref:{name}", "replacement_ref_not_followed")
            continue
        sink.add(
            "git_ref",
            f"git:ref:{name}",
            base + "/tree/" + quote(name, safe=""),
            oid,
            _json({"ref": name, "oid": oid, "object_type": kind}),
            {"access_scope": "repository"},
        )
        if kind == "tag":
            tags.add(oid)
        try:
            tips.add(
                _git(root, "rev-parse", "--verify", oid + "^{commit}").strip().decode()
            )
        except GitReadError:
            sink.gap(
                "git_ref",
                f"git:ref:{name}",
                "ref_does_not_resolve_to_commit",
                {"oid": oid},
            )
    return refs, tips, tags


def _collect_head(root: Path, sink, base: str, tips: set[str]) -> None:
    try:
        head = _git(root, "rev-parse", "--verify", "HEAD^{commit}").strip().decode()
        tips.add(head)
        sink.add(
            "git_ref",
            "git:ref:HEAD",
            base + "/commit/" + head,
            head,
            _json({"ref": "HEAD", "oid": head, "object_type": "commit"}),
        )
    except GitReadError:
        sink.gap("git_ref", "git:ref:HEAD", "head_unavailable")


def _collect_tags(root: Path, sink, base: str, tags: set[str]) -> None:
    seen_tags = set()
    while tags:
        oid = tags.pop()
        if oid in seen_tags:
            continue
        seen_tags.add(oid)
        raw = _read_object(root, oid, "tag", sink)
        if raw is not None:
            sink.add("git_tag", f"git:tag:{oid}", base + "/tree/" + oid, oid, raw)
            header = raw.split(b"\n\n", 1)[0].splitlines()
            if b"type tag" in header:
                tags.update(
                    line[7:].decode() for line in header if line.startswith(b"object ")
                )


def _boundary_changes(root: Path, sha: str) -> list[dict]:
    # A shallow boundary lacks its parent object. Enumerate its available
    # tree as a root while retaining the history gap.
    entries = _git(root, "ls-tree", "-rz", sha).split(b"\0")
    changes = []
    for entry in entries:
        if not entry:
            continue
        details, path_bytes = entry.split(b"\t", 1)
        mode, _, oid = details.decode().split()
        changes.append(
            {
                "path": path_bytes.decode("utf-8", errors="replace"),
                "path_hex": path_bytes.hex(),
                "mode": mode,
                "oid": oid,
                "status": "BOUNDARY",
                "old_oid": None,
                "old_mode": None,
                "source_url_path": quote_from_bytes(path_bytes, safe="/"),
            }
        )
    return changes


def _register_blob_occurrences(
    sink,
    base: str,
    sha: str,
    changes: list[dict],
    blobs: dict,
    sensitive_oids: set[str],
) -> None:
    for change in changes:
        path, oid, mode = change["path"], change["oid"], change["mode"]
        if mode == "000000":
            continue
        metadata = {
            "path": path,
            "path_hex": change["path_hex"],
            "commit": sha,
            "mode": mode,
            "url": base + "/blob/" + sha + "/" + change["source_url_path"],
        }
        if _repository_path_is_sensitive(path):
            sensitive_oids.add(oid)
            sink.gap(
                "git_blob",
                f"git:path:{sha}:{path}",
                "sensitive_path_excluded",
                metadata,
            )
        elif mode == "160000":
            sink.gap(
                "git_submodule",
                f"git:submodule:{sha}:{path}",
                "submodule_repository_not_collected",
                {**metadata, "oid": oid},
            )
        elif mode not in {"100644", "100755", "120000"}:
            sink.gap(
                "git_blob", f"git:path:{sha}:{path}", "git_mode_unsupported", metadata
            )
        else:
            blobs.setdefault(oid, []).append(metadata)


def _collect_commit(
    root: Path, sink, base: str, sha: str, shallow: bool, commit_set: set[str]
) -> list[dict]:
    raw = _read_object(root, sha, "commit", sink)
    if raw is None:
        return []
    header = raw.split(b"\n\n", 1)[0].splitlines()
    parents = [line[7:].decode() for line in header if line.startswith(b"parent ")]
    tree = next(
        (line[5:].decode() for line in header if line.startswith(b"tree ")), None
    )
    sink.add(
        "git_commit",
        f"git:commit:{sha}",
        base + "/commit/" + sha,
        sha,
        raw,
        {"parents": parents, "tree": tree, "access_scope": "repository"},
    )
    try:
        parent = parents[0] if parents else None
        if shallow and parent and parent not in commit_set:
            changes = _boundary_changes(root, sha)
        else:
            changes = _changes(root, sha, parent)
    except GitReadError:
        sink.gap("git_tree", f"git:changes:{sha}", "git_tree_unavailable")
        return []
    sink.add(
        "git_changes",
        f"git:changes:{sha}",
        base + "/commit/" + sha,
        sha,
        _json(
            {
                "commit": sha,
                "comparison_parent": parents[0] if parents else None,
                "changes": changes,
            }
        ),
        {"tree": tree, "changes": len(changes)},
    )
    return changes


def _collect_blobs(root: Path, sink, blobs: dict, sensitive_oids: set[str]) -> None:
    for oid, occurrences in sorted(blobs.items()):
        if oid in sensitive_oids:
            sink.gap(
                "git_blob", f"git:blob:{oid}", "blob_also_present_at_sensitive_path"
            )
            continue
        raw = _read_object(root, oid, "blob", sink)
        if raw is None:
            continue
        is_pointer = raw.startswith(b"version https://git-lfs.github.com/spec/")
        # Provenance grows in separate change maps. Keeping it out of the
        # blob document avoids uploading identical content again merely
        # because a new branch/path/commit points at the same object.
        sink.add(
            "git_lfs_pointer" if is_pointer else "git_blob",
            f"git:blob:{oid}",
            f"https://api.github.com/repos/{sink.repository}/git/blobs/{oid}",
            oid,
            raw,
            {
                "git_oid": oid,
                "access_scope": "repository",
                "path_provenance": "git_changes",
                "pointer_only": is_pointer,
                "targets_followed": False,
            },
        )
        if is_pointer:
            _lfs_payload(root, raw, sink, occurrences[0]["url"], oid)


def collect_git(root: Path, sink) -> None:
    """Emit refs, commits, first-parent changes, unique blobs, and explicit gaps.

    ``sink`` implements ``add(kind, id, url, version, raw, metadata=None)`` and
    ``gap(kind, id, reason, metadata=None)`` and supplies ``repository``. The
    first-parent change maps plus all commits preserve complete tree history,
    including merge results and deletions, without repeating unchanged blobs.
    """
    root = Path(root)
    base = f"https://github.com/{sink.repository}"
    try:
        shallow = _git(root, "rev-parse", "--is-shallow-repository").strip() == b"true"
        if shallow:
            sink.gap("git_history", "git:history", "shallow_history")
        if _is_partial_clone(root):
            sink.gap(
                "git_history", "git:history", "partial_clone_requires_explicit_fetch"
            )
            return
        refs, tips, tags = _collect_refs(root, sink, base)
        _collect_head(root, sink, base, tips)
        _collect_tags(root, sink, base, tags)
        if not tips:
            return
        commits = (
            _git(root, "rev-list", "--topo-order", "--reverse", *sorted(tips))
            .decode()
            .splitlines()
        )
        commit_set = set(commits)
        blobs: dict[str, list[dict]] = {}
        sensitive_oids = set()
        for sha in commits:
            changes = _collect_commit(root, sink, base, sha, shallow, commit_set)
            _register_blob_occurrences(sink, base, sha, changes, blobs, sensitive_oids)
        _collect_blobs(root, sink, blobs, sensitive_oids)
        sink.add(
            "git_scope",
            "git:scope",
            base,
            hashlib.sha256(_json(sorted(tips))).hexdigest(),
            _json(
                {
                    "scope": "all_available_refs_and_HEAD_reachable_history",
                    "refs": len(refs),
                    "commits": len(commits),
                    "unique_blob_objects": len(blobs),
                    "shallow": shallow,
                    "remote_ref_completeness": "caller_must_reconcile_fetch",
                    "unreachable_objects": "outside_ref_scope",
                }
            ),
        )
    except (GitReadError, ValueError, UnicodeError):
        sink.gap("git_history", "git:history", "git_enumeration_failed")
