"""Trusted source-file scope for the headless read-only Issue worker."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import subprocess

from runtime.request_context import current_runtime_data_root, current_runtime_id
from tool_providers.universal_tool_platform import (
    ReadOnlyToolPolicy,
    UniversalToolCall,
    UniversalToolDefinition,
)

ISSUE_READ_TOOLS = frozenset(
    {"read_file", "python_ast_outline", "calculate_hash", "file_stat", "list_directory"}
)
ISSUE_REVIEW_POLICY = ReadOnlyToolPolicy(ISSUE_READ_TOOLS, name="issue-review-v1")
SENSITIVE_SUFFIXES = frozenset({".pem", ".key", ".jks", ".keystore", ".p12", ".pfx", ".memory"})
SENSITIVE_NAMES = frozenset({"credentials.json", "service_account.json", "id_rsa", "id_ed25519"})


def _is_source_path(path: Path) -> bool:
    return (
        ".git" not in path.parts
        and not path.name.lower().startswith(".env")
        and path.name.lower() not in SENSITIVE_NAMES
        and path.suffix.lower() not in SENSITIVE_SUFFIXES
    )


@dataclass(frozen=True)
class RepositoryReviewPolicy(ReadOnlyToolPolicy):
    root: Path = Path(".")
    source_files: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "root", self.root.resolve())
        object.__setattr__(self, "source_files", frozenset(self.source_files))

    def __call__(self, definition: UniversalToolDefinition, call: UniversalToolCall) -> bool:
        if not self.allows_tool(definition):
            return False
        from tool_providers import filesystem

        # Do not silently authorize one root while the legacy provider reads another.
        if Path(filesystem.BASE_DIR).resolve() != self.root:
            return False
        raw_path = call.arguments.get("path") or "."
        if not isinstance(raw_path, str) or Path(raw_path).is_absolute():
            return False
        try:
            relative = (self.root / raw_path).resolve().relative_to(self.root).as_posix()
        except (OSError, ValueError, RuntimeError):
            return False
        if definition.name == "list_directory":
            return relative == "." or any(
                source.startswith(relative + "/") for source in self.source_files
            )
        return relative in self.source_files and _is_source_path(Path(relative))


def require_external_review_store(repo_root: Path) -> None:
    """Validate storage isolation before importing app or creating credentials."""
    # A runtime-bound store takes precedence over LOGOS_MEMORY_PATH. This
    # standalone worker must not inherit another runtime's storage authority.
    if current_runtime_id() is not None or current_runtime_data_root() is not None:
        raise PermissionError("Issue review cannot inherit an active runtime context")
    configured = os.environ.get("LOGOS_MEMORY_PATH", "").strip()
    if not configured:
        raise PermissionError("Issue review requires an explicit external LOGOS_MEMORY_PATH")
    root = repo_root.resolve()
    location = Path(configured).expanduser().resolve()
    if location == root or root in location.parents:
        raise PermissionError("Issue review storage must be outside the permitted checkout")


def build_review_policy(repo_root: Path) -> RepositoryReviewPolicy:
    root = repo_root.resolve()
    output = subprocess.check_output(["git", "ls-files", "--cached", "-z"], cwd=root)
    source_files = frozenset(
        path for path in output.decode("utf-8").split("\0") if path and _is_source_path(Path(path))
    )
    return RepositoryReviewPolicy(
        ISSUE_READ_TOOLS, name=ISSUE_REVIEW_POLICY.name, root=root, source_files=source_files
    )
