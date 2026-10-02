"""Authorized GitHub dialogue ingress and opaque checkpoint commit protocol.

GitHub comments are the source queue. A public started marker precedes any paid
turn; only an uploaded, scoped encrypted artifact can commit its cursor. This
transport never silently falls back to an older or fresh native conversation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from pathlib import Path

from agent_office.claude_checkpoint import (
    open_checkpoint,
    pack_checkpoint,
    seal_checkpoint,
    unpack_checkpoint,
)


MARKER_PREFIX = "<!-- claude-session-checkpoint:"
SESSION_LABEL = "claude-session"
WORKFLOW_PATH = ".github/workflows/claude-lite.yml"
_TRIGGER = re.compile(r"^@claude-lite session\s+(.*)$", re.DOTALL)
_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


def select_commands(comments, after_id, routed=False):
    if (
        type(after_id) is not int
        or after_id < 0
        or type(routed) is not bool
        or not isinstance(comments, list)
    ):
        raise ValueError("comment_invalid")
    seen = {}
    commands = []
    for comment in comments:
        if (
            not isinstance(comment, dict)
            or type(comment.get("id")) is not int
            or comment["id"] <= 0
        ):
            raise ValueError("comment_invalid")
        identity = comment["id"]
        if identity in seen and seen[identity] != comment:
            raise ValueError("comment_invalid")
        if identity in seen or identity <= after_id:
            continue
        seen[identity] = copy.deepcopy(comment)
        user = comment.get("user", {})
        if user.get("type") != "User" or comment.get("author_association") not in _ASSOCIATIONS:
            continue
        body = comment.get("body", "")
        if not isinstance(body, str) or not isinstance(user.get("login"), str):
            raise ValueError("comment_invalid")
        body = body.strip()
        if body.startswith(MARKER_PREFIX):
            continue
        match = _TRIGGER.match(body)
        message = match.group(1).strip() if match else body if routed else ""
        if message:
            commands.append(
                {
                    "event_id": f"issue-comment:{identity}",
                    "comment_id": identity,
                    "message": message,
                    "author": user["login"],
                }
            )
    return sorted(commands, key=lambda item: item["comment_id"])


def _positive(value):
    return type(value) is int and value > 0


def _uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


def choose_checkpoint(markers):
    if not isinstance(markers, list):
        raise ValueError("checkpoint_anchor_invalid")
    ordered = []
    for marker in markers:
        if not isinstance(marker, dict) or not all(
            _positive(marker.get(key))
            for key in ("comment_id", "repo_id", "issue_number", "generation", "run_id")
        ):
            raise ValueError("checkpoint_anchor_invalid")
        if (
            marker.get("schema") != 1
            or type(marker.get("schema")) is not int
            or marker.get("kind") not in {"started", "committed"}
        ):
            raise ValueError("checkpoint_anchor_invalid")
        if (
            not _uuid(marker.get("session_id"))
            or type(marker.get("watermark")) is not int
            or marker["watermark"] < 0
        ):
            raise ValueError("checkpoint_anchor_invalid")
        if marker["kind"] == "committed":
            if not _uuid(marker.get("native_session_id")) or not _positive(
                marker.get("artifact_id")
            ):
                raise ValueError("checkpoint_anchor_invalid")
            if not isinstance(marker.get("artifact_name"), str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,128}", marker["artifact_name"]
            ):
                raise ValueError("checkpoint_anchor_invalid")
            if not re.fullmatch(r"[0-9a-f]{40}", str(marker.get("head_sha"))) or not re.fullmatch(
                r"[0-9a-f]{64}", str(marker.get("ciphertext_sha256"))
            ):
                raise ValueError("checkpoint_anchor_invalid")
        ordered.append(marker)
    ordered.sort(key=lambda item: item["comment_id"])
    scope = None
    previous_generation = 0
    committed_watermark = 0
    latest = None
    pending = None
    for marker in ordered:
        identity = (marker["repo_id"], marker["issue_number"], marker["session_id"])
        if (scope is not None and identity != scope) or marker["generation"] < previous_generation:
            raise ValueError("checkpoint_anchor_invalid")
        scope = identity
        previous_generation = marker["generation"]
        if marker["kind"] == "started":
            pending = marker
        else:
            if marker["watermark"] < committed_watermark:
                raise ValueError("checkpoint_anchor_invalid")
            if pending and (
                pending["generation"] != marker["generation"]
                or pending["run_id"] != marker["run_id"]
            ):
                raise ValueError("checkpoint_anchor_invalid")
            committed_watermark = marker["watermark"]
            latest = marker
            pending = None
    if pending:
        raise ValueError("checkpoint_incomplete")
    return copy.deepcopy(latest)


def encode_marker(marker):
    return MARKER_PREFIX + json.dumps(marker, sort_keys=True, separators=(",", ":")) + " -->"


def _markers(comments):
    markers = []
    for comment in comments:
        user = comment.get("user", {})
        body = comment.get("body", "")
        if (
            user.get("type") != "Bot"
            or user.get("login") != "github-actions[bot]"
            or not isinstance(body, str)
            or not body.startswith(MARKER_PREFIX)
        ):
            continue
        try:
            if not body.endswith(" -->"):
                raise ValueError("checkpoint_anchor_invalid")
            marker = json.loads(body[len(MARKER_PREFIX) : -4])
            marker["comment_id"] = comment["id"]
            markers.append(marker)
        except (ValueError, TypeError, KeyError):
            raise ValueError("checkpoint_anchor_invalid") from None
    return markers


def _binding(marker):
    return {
        key: marker[key]
        for key in ("repo_id", "session_id", "native_session_id", "schema", "generation")
    }


def eligible_dialogue(github, issue_number):
    """Avoid installing the native CLI for an ordinary unbound issue comment.

    Existing checkpoints must be fully verified by prepare even when their public
    cursor claims there is no new command. A public cursor is not private proof.
    """
    comments = github.list_comments(issue_number)
    anchor = choose_checkpoint(_markers(comments))
    if anchor:
        return True
    issue = github.get_issue(issue_number)
    if SESSION_LABEL in {label["name"] for label in issue.get("labels", [])}:
        raise ValueError("checkpoint_missing")
    return any(github.write_permission(item["author"]) for item in select_commands(comments, 0))


def _validate_artifact(github, marker, *, restoring=False):
    run = github.get_run(marker["run_id"])
    artifact = github.get_artifact(marker["artifact_id"])
    workflow_run = artifact.get("workflow_run", {})
    if (
        run.get("id") != marker["run_id"]
        or run.get("path") != WORKFLOW_PATH
        or run.get("event") != "issue_comment"
        or run.get("head_branch") != "master"
        or run.get("repository", {}).get("id") != marker["repo_id"]
        or run.get("head_sha") != marker["head_sha"]
        or artifact.get("id") != marker["artifact_id"]
        or artifact.get("name") != marker["artifact_name"]
        or artifact.get("expired") is not False
        or not _positive(artifact.get("size_in_bytes"))
        or artifact["size_in_bytes"] > 17 * 1024 * 1024
        or workflow_run.get("id") != marker["run_id"]
        or workflow_run.get("head_sha") != marker["head_sha"]
        or workflow_run.get("repository_id") != marker["repo_id"]
        or (restoring and (run.get("status") != "completed" or run.get("conclusion") != "success"))
    ):
        raise ValueError("checkpoint_artifact_invalid")
    return artifact


def prepare_dialogue(
    github,
    runner,
    *,
    repo_id,
    repository,
    issue_number,
    run_id,
    head_sha,
    secret,
    output_dir,
    billing_source="api_usage_estimate",
):
    # The initial hosted dialogue slice has one canonical issue workplace.
    # Independent scope prevents a same-title issue from adopting its history.
    goal = f"GitHub dialogue repository {repo_id}, issue {issue_number}"
    expected_session = str(uuid.uuid5(uuid.NAMESPACE_URL, "alice-pro:claude-dialogue:" + goal))
    comments = github.list_comments(issue_number)
    anchor = choose_checkpoint(_markers(comments))
    issue = github.get_issue(issue_number)
    labelled = SESSION_LABEL in {label["name"] for label in issue.get("labels", [])}
    if anchor and (
        anchor["repo_id"] != repo_id
        or anchor["issue_number"] != issue_number
        or anchor["session_id"] != expected_session
    ):
        raise ValueError("checkpoint_anchor_invalid")
    if labelled and not anchor:
        raise ValueError("checkpoint_missing")
    cursor = anchor["watermark"] if anchor else 0
    record = None
    if anchor:
        _validate_artifact(github, anchor, restoring=True)
        ciphertext = github.download_checkpoint(anchor["artifact_id"])
        if hashlib.sha256(ciphertext).hexdigest() != anchor["ciphertext_sha256"]:
            raise ValueError("checkpoint_invalid")
        record = unpack_checkpoint(open_checkpoint(ciphertext, secret, _binding(anchor)))
        if (
            record["state"].get("session_id") != expected_session
            or record["native_session_id"] != anchor["native_session_id"]
            or record["state"].get("work_items") != [{"type": "issue", "id": str(issue_number)}]
        ):
            raise ValueError("checkpoint_invalid")
        completed_ids = record["state"].get("completed_event_ids", [])
        if (
            not completed_ids
            or not all(re.fullmatch(r"issue-comment:[1-9][0-9]*", event) for event in completed_ids)
            or max(int(event.split(":")[1]) for event in completed_ids) != cursor
        ):
            raise ValueError("checkpoint_invalid")
    candidates = select_commands(comments, cursor, routed=bool(anchor))
    commands = [item for item in candidates if github.write_permission(item["author"])]
    if not commands:
        return None
    if not anchor:
        # The first explicit command admits this dialogue. Later authorized
        # plain comments already queued in GitHub belong to that same session.
        first = commands[0]
        followups = select_commands(comments, first["comment_id"], routed=True)
        commands = [first] + [item for item in followups if github.write_permission(item["author"])]
    if anchor:
        session = runner.restore_checkpoint(**record)
    else:
        session = runner.open_session(
            goal, work_items=({"type": "issue", "id": str(issue_number)},)
        )
    before = runner.get_state(session)
    if before["status"] in {"closed", "blocked"}:
        raise ValueError("checkpoint_session_blocked")
    allowed_ids = {item["event_id"] for item in commands}
    if any(item["event_id"] not in allowed_ids for item in before["queue"]):
        raise ValueError("checkpoint_queue_unauthorized")
    for command in commands:
        runner.enqueue(session, command["event_id"], command["message"])
    generation = anchor["generation"] + 1 if anchor else 1
    marker = {
        "schema": 1,
        "kind": "started",
        "repo_id": repo_id,
        "issue_number": issue_number,
        "session_id": session,
        "generation": generation,
        "run_id": run_id,
        "watermark": commands[0]["comment_id"],
        "event_ids": [item["event_id"] for item in commands],
    }
    github.post_marker(issue_number, marker)
    # Establish wake routing before the first active turn, closing the initial
    # plain-comment/sleep race. A label never authorizes a fresh or paid session.
    github.add_session_label(issue_number)
    state = runner.run_pending(session)
    if state["status"] != "sleeping":
        raise ValueError("checkpoint_provider_blocked")
    record = runner.export_checkpoint(session)
    marker.update(
        kind="committed", native_session_id=record["native_session_id"], head_sha=head_sha
    )
    binding = _binding(marker)
    ciphertext = seal_checkpoint(pack_checkpoint(**record), secret, binding)
    directory = Path(output_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "checkpoint.bin"
    path.write_bytes(ciphertext)
    path.chmod(0o600)
    artifact_name = f"claude-session-{repo_id}-{issue_number}-{run_id}-{generation}"
    completed = [
        int(event.removeprefix("issue-comment:")) for event in state["completed_event_ids"]
    ]
    marker.update(
        watermark=max(completed),
        artifact_name=artifact_name,
        ciphertext_sha256=hashlib.sha256(ciphertext).hexdigest(),
    )
    answers = []
    for result in runner.get_results(session):
        if int(result["event_id"].removeprefix("issue-comment:")) > cursor:
            safe = copy.deepcopy(result)
            safe["answer"] = safe["answer"].replace(secret, "[redacted]")
            safe["billing_source"] = billing_source
            answers.append(safe)
    return {
        "checkpoint_path": str(path),
        "artifact_name": artifact_name,
        "binding": binding,
        "marker": marker,
        "answers": answers,
    }


def finalize_dialogue(github, metadata, *, artifact_id):
    marker = dict(metadata["marker"], artifact_id=artifact_id)
    _validate_artifact(github, marker)
    github.post_marker(marker["issue_number"], marker)
    for result in metadata["answers"]:
        report = json.dumps(
            {
                key: result[key]
                for key in (
                    "event_id",
                    "provider_session_id",
                    "usage",
                    "total_cost_usd",
                    "billing_source",
                )
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        github.post_answer(
            marker["issue_number"],
            result["answer"]
            + "\n\n<details><summary>Сессия и расход</summary>\n\n```json\n"
            + report
            + "\n```\n</details>",
        )
    return marker
