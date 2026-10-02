"""Durable, tool-free native Claude dialogue turns.

The native JSONL transcript is private data, not a prompt cache. Each command is
committed before a subprocess starts. A worker crash or ambiguous provider error
blocks automatic replay; only a proved pre-spawn failure is safe to retry. Linux
flock leases serialize drains across processes without blocking event ingestion.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path


_ROLES = frozenset({"dialogue", "security-reviewer"})
_ENVIRONMENT = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
    }
)
_MAX_TRANSCRIPT_BYTES = 32 * 1024 * 1024
_MAX_MESSAGE_LENGTH = 128 * 1024
_MAX_ANSWER_LENGTH = 128 * 1024
_MAX_COMMANDS_PER_WAKE = 8
_MODEL = "claude-sonnet-4-6"
_TIMEOUT_SECONDS = 120
_MAX_TURNS = 6
_MAX_BUDGET_USD = "0.50"
_STATE_FIELDS = frozenset(
    {
        "schema",
        "session_id",
        "goal",
        "work_items",
        "native_session_id",
        "status",
        "reason",
        "role",
        "role_epoch",
        "queue",
        "received_event_ids",
        "completed_event_ids",
        "turn_count",
        "in_flight",
        "snapshot",
        "results",
    }
)
_STATE_REASONS = {
    "sleeping": {"awaiting_comment", "pending_comments"},
    "running": {"running_command"},
    "blocked": {"provider_outcome_unknown", "missing_native_history", "pre_spawn_failed"},
    "closed": {"session_closed"},
}


def _canonical_uuid(value) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _bounded_string(value, maximum: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def _nonnegative_number(value) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _validate_native_history(content: bytes, native_id: str, prompt: str | None = None) -> None:
    """Check owned turn evidence while preserving all unknown metadata bytes."""
    found_user = False
    found_assistant = False
    for line in content.decode("utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError("invalid native transcript record")
        if "sessionId" in record and record["sessionId"] != native_id:
            raise ValueError("native transcript identity mismatch")
        if record.get("sessionId") != native_id:
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        if record.get("type") == "user" and message.get("role") == "user":
            value = message.get("content")
            matches = prompt is None or value == prompt
            if isinstance(value, list):
                matches = prompt is None or any(
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and block.get("text") == prompt
                    for block in value
                )
            if matches:
                found_user = True
                found_assistant = False
        elif (
            record.get("type") == "assistant" and message.get("role") == "assistant" and found_user
        ):
            found_assistant = True
    if not found_user or not found_assistant:
        raise ValueError("native transcript lacks the completed owned turn")


def _validate_checkpoint(state: dict, native_session_id: str, transcript: bytes) -> None:
    """Validate a selected session, never a host filesystem/configuration archive."""
    if not isinstance(state, dict) or set(state) != _STATE_FIELDS:
        raise ValueError("invalid checkpoint state schema")
    if type(state["schema"]) is not int or state["schema"] != 1:
        raise ValueError("unsupported checkpoint schema")
    if not _canonical_uuid(state["session_id"]) or not _canonical_uuid(native_session_id):
        raise ValueError("invalid checkpoint identity")
    if state["native_session_id"] != native_session_id:
        raise ValueError("checkpoint native identity mismatch")
    if not _bounded_string(state["goal"], _MAX_MESSAGE_LENGTH):
        raise ValueError("invalid checkpoint goal")
    expected = str(uuid.uuid5(uuid.NAMESPACE_URL, "alice-pro:claude-dialogue:" + state["goal"]))
    if state["session_id"] != expected:
        raise ValueError("checkpoint goal identity mismatch")
    if not isinstance(state["role"], str) or state["role"] not in _ROLES:
        raise ValueError("unsupported checkpoint role")
    if type(state["role_epoch"]) is not int or state["role_epoch"] < 0:
        raise ValueError("invalid checkpoint role epoch")
    if not isinstance(state["status"], str) or state["status"] not in _STATE_REASONS:
        raise ValueError("invalid checkpoint status")
    if (
        not isinstance(state["reason"], str)
        or state["reason"] not in _STATE_REASONS[state["status"]]
    ):
        raise ValueError("invalid checkpoint reason")
    items = state["work_items"]
    if not isinstance(items, list) or len(items) > 64:
        raise ValueError("invalid checkpoint work items")
    for item in items:
        if not isinstance(item, dict) or set(item) != {"type", "id"}:
            raise ValueError("invalid checkpoint work item")
        if (
            not isinstance(item["type"], str)
            or item["type"] not in {"issue", "pr"}
            or not isinstance(item["id"], str)
        ):
            raise ValueError("invalid checkpoint work item")
        if not re.fullmatch(r"[1-9][0-9]{0,15}", item["id"]):
            raise ValueError("invalid checkpoint work item identity")
    queue = state["queue"]
    if not isinstance(queue, list) or len(queue) > 4096:
        raise ValueError("invalid checkpoint queue")
    queued_ids = []
    for item in queue:
        if not isinstance(item, dict) or set(item) != {"event_id", "message"}:
            raise ValueError("invalid checkpoint instruction")
        if not _bounded_string(item["event_id"], 256) or not _bounded_string(
            item["message"], _MAX_MESSAGE_LENGTH
        ):
            raise ValueError("invalid checkpoint instruction")
        queued_ids.append(item["event_id"])
    for field in ("received_event_ids", "completed_event_ids"):
        values = state[field]
        if not isinstance(values, list) or len(values) > 8192:
            raise ValueError("invalid checkpoint cursor")
        if any(not _bounded_string(value, 256) for value in values) or len(set(values)) != len(
            values
        ):
            raise ValueError("invalid checkpoint event identity")
    if state["received_event_ids"] != state["completed_event_ids"] + queued_ids:
        raise ValueError("checkpoint queue and completed cursor mismatch")
    if type(state["turn_count"]) is not int or state["turn_count"] != len(
        state["completed_event_ids"]
    ):
        raise ValueError("invalid checkpoint turn count")
    in_flight = state["in_flight"]
    if in_flight is not None:
        if not isinstance(in_flight, dict) or set(in_flight) != {"event_id", "operation_id"}:
            raise ValueError("invalid checkpoint invocation")
        if (
            not queued_ids
            or in_flight["event_id"] != queued_ids[0]
            or not _canonical_uuid(in_flight["operation_id"])
        ):
            raise ValueError("invalid checkpoint invocation identity")
        if state["status"] not in {"running", "blocked"}:
            raise ValueError("invalid checkpoint invocation status")
        if state["reason"] == "pre_spawn_failed":
            raise ValueError("pre-spawn checkpoint cannot contain an unresolved invocation")
    elif state["status"] == "running":
        raise ValueError("running checkpoint lacks invocation identity")
    results = state["results"]
    if not isinstance(results, list) or len(results) != state["turn_count"]:
        raise ValueError("invalid checkpoint results")
    for result, event_id in zip(results, state["completed_event_ids"]):
        fields = {"event_id", "answer", "usage", "total_cost_usd", "provider_session_id"}
        if not isinstance(result, dict) or set(result) != fields:
            raise ValueError("invalid checkpoint result schema")
        if result["event_id"] != event_id or result["provider_session_id"] != native_session_id:
            raise ValueError("invalid checkpoint result identity")
        if not isinstance(result["answer"], str) or len(result["answer"]) > _MAX_ANSWER_LENGTH:
            raise ValueError("invalid checkpoint answer")
        usage = result["usage"]
        if not isinstance(usage, dict) or len(usage) > 64:
            raise ValueError("invalid checkpoint reported usage")
        if any(
            not _bounded_string(key, 128) or not _nonnegative_number(value)
            for key, value in usage.items()
        ):
            raise ValueError("invalid checkpoint reported usage")
        if result["total_cost_usd"] is not None and not _nonnegative_number(
            result["total_cost_usd"]
        ):
            raise ValueError("invalid checkpoint reported cost")
    if not isinstance(transcript, bytes) or len(transcript) > _MAX_TRANSCRIPT_BYTES:
        raise ValueError("invalid checkpoint transcript")
    snapshot = state["snapshot"]
    if snapshot is None:
        if transcript or state["turn_count"]:
            raise ValueError("checkpoint native history is missing")
    else:
        if not isinstance(snapshot, dict) or set(snapshot) != {"generation", "project", "sha256"}:
            raise ValueError("invalid checkpoint snapshot schema")
        if not _canonical_uuid(snapshot["generation"]):
            raise ValueError("invalid checkpoint snapshot generation")
        project = snapshot["project"]
        if (
            not isinstance(project, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", project)
            or project in {".", ".."}
        ):
            raise ValueError("invalid checkpoint native project")
        if not isinstance(snapshot["sha256"], str) or not re.fullmatch(
            r"[a-f0-9]{64}", snapshot["sha256"]
        ):
            raise ValueError("invalid checkpoint snapshot integrity")
        if not transcript or hashlib.sha256(transcript).hexdigest() != snapshot["sha256"]:
            raise ValueError("checkpoint transcript integrity failure")
        _validate_native_history(transcript, native_session_id)
    if len(json.dumps(state, allow_nan=False).encode("utf-8")) > _MAX_TRANSCRIPT_BYTES:
        raise ValueError("checkpoint state size limit exceeded")


def _reject_symlinks(path: Path) -> None:
    if any(component.is_symlink() for component in (path, *path.parents)):
        raise ValueError("private path must not contain a symlink")


def _private_directory(path: Path) -> None:
    """Create a private directory without following an existing symlink."""
    _reject_symlinks(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError("private directory is unavailable")
    path.chmod(0o700)


def _atomic_write(path: Path, content: bytes) -> None:
    _reject_symlinks(path)
    _private_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_private_file(path: Path, maximum: int = _MAX_TRANSCRIPT_BYTES) -> bytes:
    _reject_symlinks(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
            raise ValueError("invalid private file")
        content = stream.read(maximum + 1)
    if len(content) > maximum:
        raise ValueError("private file size limit exceeded")
    return content


class PersistentClaudeRunner:
    """A bounded dialogue worker; it grants no repository or deployment tools.

    ``state_dir`` is an operator-owned private location separate from the app
    database. Hosted transports must encrypt it before moving it between hosts.
    This class never restores configuration, credentials, hooks, or plugins.
    """

    def __init__(self, *, state_dir: Path, workspace: Path, claude_config_dir: Path):
        self.state_dir = Path(state_dir).absolute()
        self.workspace = Path(workspace).resolve(strict=True)
        self.claude_config_dir = Path(claude_config_dir).absolute()
        if not self.workspace.is_dir():
            raise ValueError("workspace must be a directory")
        for path in (self.state_dir, self.state_dir / "sessions", self.state_dir / "snapshots"):
            _private_directory(path)

    def _session_path(self, session: str) -> Path:
        try:
            identity = str(uuid.UUID(session))
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("invalid session identity") from error
        if identity != session:
            raise ValueError("invalid session identity")
        return self.state_dir / "sessions" / f"{identity}.json"

    @contextmanager
    def _lock(self, session: str, suffix: str, *, blocking: bool = True):
        path = self._session_path(session).with_suffix(suffix)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(descriptor, flags)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _load(self, session: str) -> dict:
        state = json.loads(_read_private_file(self._session_path(session)))
        if state.get("schema") != 1 or state.get("session_id") != session:
            raise ValueError("invalid runner state")
        if state.get("role") not in _ROLES:
            raise ValueError("unsupported stored role")
        return state

    def _save(self, state: dict) -> None:
        _atomic_write(
            self._session_path(state["session_id"]),
            json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        )

    @staticmethod
    def _public_state(state: dict) -> dict:
        result = copy.deepcopy(state)
        for field in ("native_session_id", "snapshot", "results"):
            result.pop(field, None)
        return result

    def open_session(self, goal: str, *, work_items=()) -> str:
        if not isinstance(goal, str) or not goal.strip() or len(goal) > _MAX_MESSAGE_LENGTH:
            raise ValueError("a bounded goal is required")
        items = []
        for item in work_items:
            if not isinstance(item, dict) or set(item) != {"type", "id"}:
                raise ValueError("invalid work item")
            if item["type"] not in {"issue", "pr"} or not isinstance(item["id"], str):
                raise ValueError("invalid work item")
            if not re.fullmatch(r"[1-9][0-9]{0,15}", item["id"]):
                raise ValueError("invalid work item identity")
            if item not in items:
                items.append(dict(item))
        session = str(uuid.uuid5(uuid.NAMESPACE_URL, "alice-pro:claude-dialogue:" + goal))
        with self._lock(session, ".state-lock"):
            if self._session_path(session).exists():
                state = self._load(session)
                for item in items:
                    if item not in state["work_items"]:
                        state["work_items"].append(item)
                self._save(state)
            else:
                self._save(
                    {
                        "schema": 1,
                        "session_id": session,
                        "goal": goal,
                        "work_items": items,
                        "native_session_id": str(uuid.uuid4()),
                        "status": "sleeping",
                        "reason": "awaiting_comment",
                        "role": "dialogue",
                        "role_epoch": 0,
                        "queue": [],
                        "received_event_ids": [],
                        "completed_event_ids": [],
                        "turn_count": 0,
                        "in_flight": None,
                        "snapshot": None,
                        "results": [],
                    }
                )
        return session

    def get_state(self, session: str) -> dict:
        with self._lock(session, ".state-lock"):
            return self._public_state(self._load(session))

    def get_results(self, session: str) -> list[dict]:
        """Return private final answers and reported usage, never raw CLI data.

        A transport must redact configured secrets before publishing any answer.
        Provider IDs are metadata, not proof that a transcript was persisted.
        """
        with self._lock(session, ".state-lock"):
            return copy.deepcopy(self._load(session)["results"])

    def export_checkpoint(self, session: str) -> dict:
        """Export one private snapshot at a native turn boundary.

        Opaque bytes must remain encrypted in the hosted transport. Unresolved
        blocked commands may be exported; an actively executing turn may not.
        """
        with self._lock(session, ".drain-lock", blocking=False) as acquired:
            if not acquired:
                raise RuntimeError("checkpoint export requires an inactive boundary")
            with self._lock(session, ".state-lock"):
                state = self._load(session)
                if state["status"] == "running":
                    raise RuntimeError("checkpoint export requires an inactive boundary")
                transcript = b""
                if state["snapshot"]:
                    generation = state["snapshot"]["generation"]
                    if not _canonical_uuid(generation):
                        raise ValueError("invalid checkpoint generation")
                    source = self.state_dir / "snapshots" / session / f"{generation}.jsonl"
                    transcript = _read_private_file(source)
                _validate_checkpoint(state, state["native_session_id"], transcript)
                return {
                    "state": copy.deepcopy(state),
                    "native_session_id": state["native_session_id"],
                    "transcript": transcript,
                }

    def restore_checkpoint(self, *, state: dict, native_session_id: str, transcript: bytes) -> str:
        """Restore only validated session bytes into this operator's workspace.

        Host-local generation/project paths are checked but never imported as
        destination paths. An existing different checkpoint is refused rather
        than overriding its completed events or queued commands.
        """
        _validate_checkpoint(state, native_session_id, transcript)
        restored = copy.deepcopy(state)
        session = restored["session_id"]
        with self._lock(session, ".drain-lock", blocking=False) as acquired:
            if not acquired:
                raise RuntimeError("checkpoint restore requires an inactive boundary")
            with self._lock(session, ".state-lock"):
                if self._session_path(session).exists():
                    current = self._load(session)
                    incoming_compare = copy.deepcopy(restored)
                    current_compare = copy.deepcopy(current)
                    for record in (incoming_compare, current_compare):
                        if record["snapshot"]:
                            record["snapshot"] = {"sha256": record["snapshot"]["sha256"]}
                    if incoming_compare != current_compare:
                        raise ValueError(
                            "stale or conflicting checkpoint cannot overwrite local state"
                        )
                    return session
                _private_directory(self.claude_config_dir)
                if transcript:
                    generation = str(uuid.uuid4())
                    project = re.sub(r"[^A-Za-z0-9_.-]", "-", str(self.workspace))[:255]
                    restored["snapshot"] = {
                        "generation": generation,
                        "project": project,
                        "sha256": hashlib.sha256(transcript).hexdigest(),
                    }
                    destination = self.state_dir / "snapshots" / session / f"{generation}.jsonl"
                    _atomic_write(destination, transcript)
                    self._restore_history(restored)
                if restored["status"] == "running":
                    restored.update(status="blocked", reason="provider_outcome_unknown")
                self._save(restored)
                return session

    def enqueue(
        self, session: str, event_id: str, message: str, *, authorized: bool = True
    ) -> bool:
        if authorized is not True:
            return False
        if not isinstance(event_id, str) or not event_id or len(event_id) > 256:
            raise ValueError("invalid event identity")
        if (
            not isinstance(message, str)
            or not message.strip()
            or len(message) > _MAX_MESSAGE_LENGTH
        ):
            raise ValueError("a bounded message is required")
        with self._lock(session, ".state-lock"):
            state = self._load(session)
            if state["status"] == "closed" or event_id in state["received_event_ids"]:
                return False
            state["queue"].append({"event_id": event_id, "message": message})
            state["received_event_ids"].append(event_id)
            self._save(state)
        return True

    def close(self, session: str) -> None:
        with self._lock(session, ".drain-lock", blocking=False) as acquired:
            if not acquired:
                raise RuntimeError("cannot close an active turn")
            with self._lock(session, ".state-lock"):
                state = self._load(session)
                if state["in_flight"]:
                    raise RuntimeError("cannot close an unresolved active turn")
                state.update(status="closed", reason="session_closed")
                self._save(state)

    def switch_role(self, session: str, role: str) -> None:
        if role not in _ROLES:
            raise ValueError("unsupported role: trusted allowlist required")
        with self._lock(session, ".drain-lock", blocking=False) as acquired:
            if not acquired:
                raise RuntimeError("role switch requires an inactive boundary")
            with self._lock(session, ".state-lock"):
                state = self._load(session)
                if state["in_flight"] or state["status"] == "running":
                    raise RuntimeError("role switch requires an inactive boundary")
                if state["status"] == "closed":
                    raise RuntimeError("closed session cannot switch role")
                if role != state["role"]:
                    state["role"] = role
                    state["role_epoch"] += 1
                    self._save(state)

    def _native_history(self, native_id: str) -> Path | None:
        projects = self.claude_config_dir / "projects"
        if projects.is_symlink():
            raise ValueError("native history directory is invalid")
        if not projects.exists():
            return None
        candidates = []
        for project in projects.iterdir():
            if project.is_symlink():
                continue
            if project.is_dir():
                history = project / f"{native_id}.jsonl"
                if history.is_symlink():
                    raise ValueError("native history must not be a symlink")
                if history.is_file():
                    candidates.append(history)
        if len(candidates) > 1:
            raise ValueError("native history is ambiguous")
        return candidates[0] if candidates else None

    def _snapshot_history(self, state: dict, prompt: str) -> dict:
        native_id = state["native_session_id"]
        history = self._native_history(native_id)
        if history is None:
            raise ValueError("native history is missing")
        content = _read_private_file(history)
        if not content:
            raise ValueError("native history is empty")
        digest = hashlib.sha256(content).hexdigest()
        if state["snapshot"] and digest == state["snapshot"]["sha256"]:
            raise ValueError("native history did not advance")
        _validate_native_history(content, native_id, prompt)
        generation = str(uuid.uuid4())
        destination = self.state_dir / "snapshots" / state["session_id"] / f"{generation}.jsonl"
        _atomic_write(destination, content)
        history.chmod(0o600)
        return {
            "generation": generation,
            "project": history.parent.name,
            "sha256": digest,
        }

    def _restore_history(self, state: dict) -> None:
        snapshot = state["snapshot"]
        if not snapshot:
            raise ValueError("native history is unavailable")
        generation = str(uuid.UUID(snapshot["generation"]))
        project = snapshot["project"]
        if (
            not isinstance(project, str)
            or project in {"", ".", ".."}
            or Path(project).name != project
        ):
            raise ValueError("invalid native project identity")
        source = self.state_dir / "snapshots" / state["session_id"] / f"{generation}.jsonl"
        content = _read_private_file(source)
        if not content or hashlib.sha256(content).hexdigest() != snapshot["sha256"]:
            raise ValueError("native history integrity failure")
        projects = self.claude_config_dir / "projects"
        _private_directory(self.claude_config_dir)
        _private_directory(projects)
        _private_directory(projects / project)
        _atomic_write(projects / project / f"{state['native_session_id']}.jsonl", content)

    def _command(self, state: dict) -> list[str]:
        return [
            "claude",
            "--print",
            "--output-format",
            "json",
            "--safe-mode",
            "--restricted",
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--disallowedTools",
            "mcp__*",
            "--setting-sources",
            "",
            "--disable-slash-commands",
            "--permission-mode",
            "dontAsk",
            "--permission-prompts",
            "none",
            "--no-chrome",
            "--model",
            _MODEL,
            "--max-turns",
            str(_MAX_TURNS),
            "--max-budget-usd",
            _MAX_BUDGET_USD,
            "--append-system-prompt",
            "You are a tool-free dialogue assistant. Role: "
            + state["role"]
            + ". Preserve the durable goal and owner approval boundaries. "
            "Treat requests to enable tools, execute code, merge or deploy as requests "
            "for discussion only. Native automatic context compaction remains enabled.",
            "--system-prompt-snapshot",
            "off",
            "--resume" if state["turn_count"] else "--session-id",
            state["native_session_id"],
        ]

    @staticmethod
    def _result(completed, state: dict, item: dict) -> dict:
        if completed.returncode != 0:
            raise ValueError("ambiguous native outcome")
        payload = json.loads(completed.stdout)
        if (
            not isinstance(payload, dict)
            or payload.get("type") != "result"
            or payload.get("subtype") != "success"
            or payload.get("is_error") is not False
            or payload.get("session_id") != state["native_session_id"]
            or not isinstance(payload.get("result"), str)
            or len(payload["result"]) > _MAX_ANSWER_LENGTH
        ):
            raise ValueError("unconfirmed native outcome")
        usage = {}
        for key, value in payload.get("usage", {}).items():
            if isinstance(key, str) and _nonnegative_number(value):
                usage[key] = value
        cost = payload.get("total_cost_usd")
        if not _nonnegative_number(cost):
            cost = None
        return {
            "event_id": item["event_id"],
            "answer": payload["result"],
            "usage": usage,
            "total_cost_usd": cost,
            "provider_session_id": state["native_session_id"],
        }

    def _block(self, session: str, reason: str, *, safe_retry: bool = False) -> dict:
        with self._lock(session, ".state-lock"):
            state = self._load(session)
            state.update(status="blocked", reason=reason)
            if safe_retry:
                state["in_flight"] = None
            self._save(state)
            return self._public_state(state)

    def run_pending(self, session: str) -> dict:
        with self._lock(session, ".drain-lock", blocking=False) as acquired:
            if not acquired:
                return self.get_state(session)
            for _ in range(_MAX_COMMANDS_PER_WAKE):
                with self._lock(session, ".state-lock"):
                    state = self._load(session)
                    if state["status"] == "closed":
                        return self._public_state(state)
                    if state["in_flight"]:
                        if state["status"] != "blocked" or state["reason"] == "pre_spawn_failed":
                            state.update(status="blocked", reason="provider_outcome_unknown")
                            self._save(state)
                        return self._public_state(state)
                    if state["status"] == "blocked" and state["reason"] != "pre_spawn_failed":
                        return self._public_state(state)
                    if not state["queue"]:
                        state.update(status="sleeping", reason="awaiting_comment")
                        self._save(state)
                        return self._public_state(state)
                    item = copy.deepcopy(state["queue"][0])
                try:
                    _private_directory(self.claude_config_dir)
                    if state["turn_count"]:
                        self._restore_history(state)
                except (OSError, ValueError, KeyError):
                    return self._block(session, "missing_native_history")
                with self._lock(session, ".state-lock"):
                    state = self._load(session)
                    state.update(status="running", reason="running_command")
                    state["in_flight"] = {
                        "event_id": item["event_id"],
                        "operation_id": str(uuid.uuid4()),
                    }
                    self._save(state)
                environment = {
                    name: value for name, value in os.environ.items() if name in _ENVIRONMENT
                }
                environment["CLAUDE_CONFIG_DIR"] = str(self.claude_config_dir)
                prompt = json.dumps(
                    {
                        "goal": state["goal"],
                        "role": state["role"],
                        "role_epoch": state["role_epoch"],
                        "event_id": item["event_id"],
                        "operation_id": state["in_flight"]["operation_id"],
                        "instruction": item["message"],
                    },
                    ensure_ascii=False,
                )
                try:
                    completed = subprocess.run(
                        self._command(state),
                        cwd=self.workspace,
                        env=environment,
                        input=prompt,
                        text=True,
                        capture_output=True,
                        timeout=_TIMEOUT_SECONDS,
                        check=False,
                    )
                except (FileNotFoundError, PermissionError):
                    return self._block(session, "pre_spawn_failed", safe_retry=True)
                except (OSError, subprocess.SubprocessError, UnicodeError):
                    return self._block(session, "provider_outcome_unknown")
                try:
                    result = self._result(completed, state, item)
                except (ValueError, TypeError, AttributeError):
                    return self._block(session, "provider_outcome_unknown")
                try:
                    snapshot = self._snapshot_history(state, prompt)
                except (OSError, ValueError, KeyError):
                    return self._block(session, "missing_native_history")
                with self._lock(session, ".state-lock"):
                    current = self._load(session)
                    current["snapshot"] = snapshot
                    current["results"].append(result)
                    current["completed_event_ids"].append(item["event_id"])
                    current["queue"].pop(0)
                    current["turn_count"] += 1
                    current["in_flight"] = None
                    current.update(status="sleeping", reason="awaiting_comment")
                    self._save(current)
            with self._lock(session, ".state-lock"):
                state = self._load(session)
                state.update(
                    status="sleeping",
                    reason="pending_comments" if state["queue"] else "awaiting_comment",
                )
                self._save(state)
                return self._public_state(state)
