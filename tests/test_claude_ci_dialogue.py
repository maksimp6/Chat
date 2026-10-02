"""Issue #703 CI transport exercised through native CLI and GitHub adapter seams.

Synthetic transcript files and GitHub records make no paid/network calls. This
suite verifies orchestration ordering, private transport and exact artifact
binding; genuine pinned Claude CLI/cross-host smoke remains separate.
"""

from __future__ import annotations

import copy
import hashlib
import subprocess
from pathlib import Path

import pytest

from tests.test_development_session_contract import NativeClaudeSpy


REPO_ID = 12345
HEAD = "a" * 40
SECRET = "synthetic-provider-secret-only"


class FakeGithub:
    def __init__(self, encode_marker):
        self.encode_marker = encode_marker
        self.comments = []
        self.markers = []
        self.answers = []
        self.labels = []
        self.permission_calls = []
        self.authorized = True
        self.runs = {}
        self.artifacts = {}
        self.files = {}
        self.add_run(100)

    def add_run(self, run_id):
        self.runs[run_id] = {
            "id": run_id,
            "path": ".github/workflows/claude-lite.yml",
            "event": "issue_comment",
            "head_sha": HEAD,
            "head_branch": "master",
            "repository": {"id": REPO_ID},
            "status": "in_progress",
            "conclusion": None,
        }

    def add_comment(self, body, *, user_type="User", association="OWNER"):
        comment_id = len(self.comments) + 1
        self.comments.append(
            {
                "id": comment_id,
                "body": body,
                "author_association": association,
                "user": {
                    "login": "owner" if user_type == "User" else "github-actions[bot]",
                    "type": user_type,
                },
            }
        )
        return comment_id

    def list_comments(self, _issue):
        return copy.deepcopy(self.comments)

    def get_issue(self, _issue):
        return {
            "number": 703,
            "title": "Persistent private Claude dialogue",
            "labels": [{"name": label} for label in self.labels],
        }

    def write_permission(self, login):
        self.permission_calls.append(login)
        return self.authorized

    def get_run(self, run_id):
        return copy.deepcopy(self.runs[run_id])

    def get_artifact(self, artifact_id):
        return copy.deepcopy(self.artifacts[artifact_id])

    def download_checkpoint(self, artifact_id):
        return self.files[artifact_id]

    def post_marker(self, _issue, marker):
        self.markers.append(copy.deepcopy(marker))
        return self.add_comment(self.encode_marker(marker), user_type="Bot", association="MEMBER")

    def post_answer(self, _issue, text):
        self.answers.append(text)

    def add_session_label(self, _issue):
        if "claude-session" not in self.labels:
            self.labels.append("claude-session")

    def upload(self, metadata, artifact_id=201):
        content = Path(metadata["checkpoint_path"]).read_bytes()
        # The marker is authoritative for the originating workflow run, not the
        # local generation counter used in authenticated encryption.
        run_id = metadata["marker"]["run_id"]
        self.artifacts[artifact_id] = {
            "id": artifact_id,
            "name": metadata["artifact_name"],
            "expired": False,
            "size_in_bytes": len(content),
            "workflow_run": {"id": run_id, "head_sha": HEAD, "repository_id": REPO_ID},
        }
        self.files[artifact_id] = content
        return artifact_id


@pytest.fixture
def transport(tmp_path, monkeypatch):
    from agent_office import claude_ci
    from agent_office.claude_runner import PersistentClaudeRunner

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    spy = NativeClaudeSpy()
    monkeypatch.setattr(subprocess, "run", spy)
    github = FakeGithub(claude_ci.encode_marker)

    def wake(run_id=100):
        github.add_run(run_id)
        runner = PersistentClaudeRunner(
            state_dir=tmp_path / f"state-{run_id}",
            workspace=workspace,
            claude_config_dir=tmp_path / f"claude-{run_id}",
        )
        return claude_ci.prepare_dialogue(
            github,
            runner,
            repo_id=REPO_ID,
            repository="owner/Chat",
            issue_number=703,
            run_id=run_id,
            head_sha=HEAD,
            secret=SECRET,
            output_dir=tmp_path / f"output-{run_id}",
        )

    def commit(metadata, artifact_id=201):
        github.upload(metadata, artifact_id)
        result = claude_ci.finalize_dialogue(github, metadata, artifact_id=artifact_id)
        github.runs[metadata["marker"]["run_id"]].update(status="completed", conclusion="success")
        return result

    return claude_ci, github, spy, wake, commit


def test_explicit_command_records_start_before_paid_turn_and_commit_after_upload(transport):
    _, github, spy, wake, commit = transport
    github.add_comment("@claude-lite session Explain persistent private context")

    def confirm_durable_started_before_dispatch(_call):
        assert github.markers[-1]["kind"] == "started"
        assert not any(marker["kind"] == "committed" for marker in github.markers)

    spy.on_turn = confirm_durable_started_before_dispatch
    metadata = wake()
    assert len(spy.calls) == 1
    assert github.permission_calls == ["owner"]
    ciphertext = Path(metadata["checkpoint_path"]).read_bytes()
    assert b"Explain persistent private context" not in ciphertext
    assert SECRET.encode() not in ciphertext
    assert github.answers == []
    commit(metadata)
    assert github.markers[-1]["kind"] == "committed"
    assert github.markers[-1]["artifact_id"] == 201
    assert github.answers and "claude-session" in github.labels


def test_association_or_routing_label_cannot_replace_live_write_authorization(transport):
    _, github, spy, wake, _ = transport
    github.authorized = False
    github.add_comment("@claude-lite session This association alone is insufficient")
    assert wake() is None
    assert github.permission_calls == ["owner"]
    assert spy.calls == [] and github.markers == []


def test_label_with_no_trusted_checkpoint_cannot_create_session_from_plain_followup(transport):
    _, github, spy, wake, _ = transport
    github.labels.append("claude-session")
    github.add_comment("Continue the previous session")
    with pytest.raises(ValueError, match="^checkpoint_missing$"):
        wake()
    assert spy.calls == []


def test_next_worker_restores_encrypted_history_and_plain_followup_same_native_session(transport):
    _, github, spy, wake, commit = transport
    github.add_comment("@claude-lite session Remember explicit owner approval")
    first = wake(100)
    commit(first, 201)
    github.add_comment("What did we agree about approval?")
    second = wake(101)
    assert len(spy.calls) == 2
    assert spy.calls[1]["native_id"] == spy.calls[0]["native_id"]
    assert "--resume" in spy.calls[1]["argv"]
    assert "Remember explicit owner approval" in spy.calls[1]["history_before"]
    assert "What did we agree about approval?" in spy.calls[1]["prompt"]
    assert second["binding"]["generation"] > first["binding"]["generation"]


def test_duplicate_wake_restores_cursor_without_another_native_call(transport):
    _, github, spy, wake, commit = transport
    github.add_comment("@claude-lite session One command only")
    commit(wake(), 201)
    assert wake(101) is None
    assert len(spy.calls) == 1
    assert len([marker for marker in github.markers if marker["kind"] == "started"]) == 1


def test_started_but_uncommitted_worker_cannot_silently_replay_paid_turn(transport):
    _, github, spy, wake, _ = transport
    github.add_comment("@claude-lite session A possibly completed paid turn")
    wake(100)
    with pytest.raises(ValueError, match="^checkpoint_incomplete$"):
        wake(101)
    assert len(spy.calls) == 1


def test_latest_corrupt_artifact_never_falls_back_to_paid_new_session(transport):
    _, github, spy, wake, commit = transport
    github.add_comment("@claude-lite session Preserve this private history")
    commit(wake(), 201)
    github.files[201] = b"corrupt encrypted checkpoint"
    github.add_comment("Continue")
    with pytest.raises(ValueError):
        wake(101)
    assert len(spy.calls) == 1


def test_finalize_refuses_artifact_from_different_run_before_commit_or_answer(transport):
    claude_ci, github, spy, wake, _ = transport
    github.add_comment("@claude-lite session Bind checkpoint to the exact run")
    metadata = wake()
    github.upload(metadata, 201)
    github.artifacts[201]["workflow_run"]["id"] = 999
    with pytest.raises(ValueError):
        claude_ci.finalize_dialogue(github, metadata, artifact_id=201)
    assert all(marker["kind"] == "started" for marker in github.markers)
    assert github.answers == []
    assert len(spy.calls) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("path", ".github/workflows/untrusted.yml"),
        ("head_branch", "untrusted"),
        ("head_sha", "c" * 40),
        ("event", "pull_request_target"),
        ("repository", {"id": 999}),
        ("status", "in_progress"),
        ("conclusion", "failure"),
    ],
)
def test_restoration_requires_exact_successful_trusted_workflow_run(transport, field, value):
    _, github, spy, wake, commit = transport
    github.add_comment("@claude-lite session One confirmed trusted turn")
    commit(wake(), 201)
    github.runs[100][field] = value
    github.add_comment("Continue")
    with pytest.raises(ValueError):
        wake(101)
    assert len(spy.calls) == 1


def test_expired_checkpoint_artifact_never_starts_paid_replacement_session(transport):
    _, github, spy, wake, commit = transport
    github.add_comment("@claude-lite session Preserve the native history")
    commit(wake(), 201)
    github.artifacts[201]["expired"] = True
    github.add_comment("Continue")
    with pytest.raises(ValueError):
        wake(101)
    assert len(spy.calls) == 1


def test_authenticated_snapshot_work_item_cannot_be_transplanted_across_issues(transport):
    from agent_office.claude_checkpoint import (
        open_checkpoint,
        pack_checkpoint,
        seal_checkpoint,
        unpack_checkpoint,
    )

    _, github, spy, wake, commit = transport
    github.add_comment("@claude-lite session Keep the canonical issue scope")
    metadata = wake()
    commit(metadata, 201)
    record = unpack_checkpoint(open_checkpoint(github.files[201], SECRET, metadata["binding"]))
    record["state"]["work_items"] = [{"type": "issue", "id": "716"}]
    ciphertext = seal_checkpoint(pack_checkpoint(**record), SECRET, metadata["binding"])
    github.files[201] = ciphertext
    github.artifacts[201]["size_in_bytes"] = len(ciphertext)
    marker = copy.deepcopy(github.markers[-1])
    marker["ciphertext_sha256"] = hashlib.sha256(ciphertext).hexdigest()
    github.comments[-1]["body"] = github.encode_marker(marker)
    github.add_comment("Continue this issue")
    with pytest.raises(ValueError):
        wake(101)
    assert len(spy.calls) == 1


def test_queued_ci_wake_drains_remaining_commands_without_new_owner_comment(transport):
    from agent_office.claude_checkpoint import open_checkpoint, unpack_checkpoint

    _, github, spy, wake, commit = transport
    for index in range(11):
        github.add_comment(f"@claude-lite session Queued instruction {index}")
    first = wake(100)
    assert len(spy.calls) == 8
    record = unpack_checkpoint(
        open_checkpoint(Path(first["checkpoint_path"]).read_bytes(), SECRET, first["binding"])
    )
    assert len(record["state"]["queue"]) == 3
    assert len(record["state"]["received_event_ids"]) == 11
    assert len(record["state"]["completed_event_ids"]) == 8
    commit(first, 201)
    # This is the queued CI invocation for an already-present source event.
    # No new owner command is supplied to continue the remaining work.
    second = wake(101)
    assert len(spy.calls) == 11
    assert len({call["native_id"] for call in spy.calls}) == 1
    assert second["marker"]["watermark"] == 11
    commit(second, 202)
    assert wake(102) is None
    assert len(spy.calls) == 11
