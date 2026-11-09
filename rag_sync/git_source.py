"""New git history since the last sync: one text record per commit.

A record carries the commit (sha, parents, author, date, message), its changes against
the first parent and the full text of every file version the commit adds or changes.
A binary file is named with its size; a credential file (redact.path_is_sensitive) is
named only. History is immutable, so a record is written once and never rewritten.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

from rag_sync.redact import mask, path_is_sensitive

_STATUS = {"A": "added", "M": "modified", "D": "deleted", "T": "type changed"}


class GitError(RuntimeError):
    pass


def repository_root() -> Path:
    """The checkout the app runs from (LOGOS_LOCAL_REPO_DIR on the device), else this source tree."""
    configured = os.environ.get("LOGOS_LOCAL_REPO_DIR")
    return Path(configured) if configured else Path(__file__).resolve().parent.parent


def _git(root: Path, *args: str) -> bytes:
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}
    try:
        return subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            check=True,
            capture_output=True,
            env=env,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", b"") or b""
        raise GitError(
            f"git {args[0]}: {detail.decode(errors='replace').strip() or exc}"
        ) from exc


def new_commits(root: Path, since: str | None) -> list[str]:
    """Commits reachable from HEAD and not from ``since``, oldest first."""
    spec = [f"{since}..HEAD"] if since else ["HEAD"]
    return _git(root, "rev-list", "--reverse", "--topo-order", *spec).decode().split()


def _changes(
    root: Path, sha: str, parent: str | None
) -> list[tuple[str, str, str | None]]:
    # --no-commit-id: with one commit (the root) diff-tree would print its id as the first field.
    args = ["diff-tree", "-r", "-z", "--no-commit-id", "--name-status", "-M"]
    args += [parent, sha] if parent else ["--root", sha]
    fields = _git(root, *args).decode(errors="replace").split("\0")
    changes, index = [], 0
    while index < len(fields) and fields[index]:
        status = fields[index]
        if status[:1] in ("R", "C"):
            changes.append((status[:1], fields[index + 2], fields[index + 1]))
            index += 3
        else:
            changes.append((status[:1], fields[index + 1], None))
            index += 2
    return changes


def _file_text(root: Path, sha: str, path: str) -> str:
    if path_is_sensitive(path):
        return "(credential file: contents not read)"
    raw = _git(root, "show", f"{sha}:{path}")
    if b"\0" in raw:
        return f"(binary file, {len(raw)} bytes)"
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return f"(non-UTF-8 file, {len(raw)} bytes)"


def commit_record(root: Path, sha: str) -> tuple[str, int]:
    """One commit as text, and how many values were masked in it."""
    raw = _git(root, "show", "-s", "--format=%H%n%P%n%an%n%aI%n%B", sha).decode(
        errors="replace"
    )
    full, parents, author, date, message = (raw.split("\n", 4) + [""] * 5)[:5]
    parent = parents.split()[0] if parents.split() else None
    lines = [
        f"## Commit {full}",
        f"Parents: {parents or '(root)'}",
        f"Author: {author}",
        f"Date: {date}",
        "",
        message.strip(),
        "",
    ]
    bodies = []
    for status, path, old in _changes(root, sha, parent):
        label = {"R": "renamed", "C": "copied"}.get(status, _STATUS.get(status, status))
        lines.append(f"- {label}: {path}" + (f" (from {old})" if old else ""))
        if status != "D":
            bodies.append(f"### {path} @ {full[:12]}\n\n" + _file_text(root, sha, path))
    text, masked = mask(
        "\n".join(lines) + ("\n\n" + "\n\n".join(bodies) if bodies else "")
    )
    return text, masked


def records(root: Path, since: str | None) -> Iterator[tuple[str, str, int]]:
    """(commit sha, record text, masked count) for every new commit, oldest first."""
    for sha in new_commits(root, since):
        text, masked = commit_record(root, sha)
        yield sha, text, masked
