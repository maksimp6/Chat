"""Public network/CLI adapter boundaries; all HTTP and Claude calls are mocked."""

import io
import json
import stat
import subprocess
import zipfile
from types import SimpleNamespace

import pytest

from scripts import claude_session_ci as entry


def _response(status=200, content=b"", headers=None, payload=None):
    return SimpleNamespace(
        status_code=status, content=content, headers=headers or {}, json=lambda: payload
    )


def _archive(name="checkpoint.bin", content=b"opaque-encrypted-checkpoint", symlink=False):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        info = zipfile.ZipInfo(name)
        if symlink:
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, content)
    return output.getvalue()


def test_signed_artifact_storage_never_receives_repository_authorization(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            return _response(
                302, headers={"Location": "https://files.blob.core.windows.net/signed-checkpoint"}
            )
        return _response(content=_archive())

    monkeypatch.setattr(entry.requests, "get", get)
    github = entry.GitHub("owner/Chat", "synthetic-github-token")
    assert github.download_checkpoint(12) == b"opaque-encrypted-checkpoint"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer synthetic-github-token"
    assert "headers" not in calls[1][1]
    assert all(
        kwargs["timeout"] == 30 and kwargs["allow_redirects"] is False for _, kwargs in calls
    )


@pytest.mark.parametrize(
    "location",
    [
        "http://files.blob.core.windows.net/a",
        "https://api.github.com.evil.test/a",
        "https://example.test/a",
        "file:///tmp/checkpoint",
    ],
)
def test_untrusted_artifact_redirect_stops_before_storage_request(monkeypatch, location):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return _response(302, headers={"Location": location})

    monkeypatch.setattr(entry.requests, "get", get)
    with pytest.raises(ValueError, match="^checkpoint_artifact_invalid$"):
        entry.GitHub("owner/Chat", "synthetic-token").download_checkpoint(12)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "blob",
    [
        _archive("../checkpoint.bin"),
        _archive("credentials.json"),
        _archive(symlink=True),
        b"invalid ZIP archive",
    ],
)
def test_checkpoint_download_does_not_extract_foreign_paths_or_symlinks(monkeypatch, blob):
    replies = iter(
        [
            _response(302, headers={"Location": "https://files.blob.core.windows.net/a"}),
            _response(content=blob),
        ]
    )
    monkeypatch.setattr(entry.requests, "get", lambda *_args, **_kwargs: next(replies))
    with pytest.raises(ValueError, match="^checkpoint_artifact_invalid$"):
        entry.GitHub("owner/Chat", "synthetic-token").download_checkpoint(12)


def test_http_error_is_generic_and_does_not_surface_provider_or_github_text(monkeypatch):
    monkeypatch.setattr(
        entry.requests,
        "request",
        lambda *_args, **_kwargs: _response(403, content=b"synthetic-secret-response"),
    )
    with pytest.raises(ValueError, match="^github_request_failed$"):
        entry.GitHub("owner/Chat", "synthetic-token").get_issue(703)


def test_comment_pagination_preserves_all_ordered_source_events(monkeypatch):
    pages = []

    def request(method, url, **kwargs):
        pages.append(kwargs["params"]["page"])
        payload = [{"id": index} for index in range(1, 101)] if len(pages) == 1 else [{"id": 101}]
        return _response(payload=payload)

    monkeypatch.setattr(entry.requests, "request", request)
    comments = entry.GitHub("owner/Chat", "synthetic-token").list_comments(703)
    assert [comment["id"] for comment in comments] == list(range(1, 102))
    assert pages == [1, 2]


@pytest.mark.parametrize("permission", ["read", "triage", "write", "maintain", "admin"])
def test_current_permission_gate_matches_repository_writer_access(monkeypatch, permission):
    monkeypatch.setattr(
        entry.requests,
        "request",
        lambda *_args, **_kwargs: _response(payload={"permission": permission}),
    )
    assert entry.GitHub("owner/Chat", "synthetic-token").write_permission("owner") is (
        permission in {"write", "maintain", "admin"}
    )


@pytest.fixture
def environment(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"repository": {"id": 12345}, "issue": {"number": 703}}), encoding="utf-8"
    )
    for name, value in {
        "GITHUB_REPOSITORY": "owner/Chat",
        "GITHUB_TOKEN": "synthetic-github-token",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_RUN_ID": "100",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_OUTPUT": str(tmp_path / "output.txt"),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(entry, "GitHub", lambda *_args: object())
    monkeypatch.setattr(entry, "_preflight", lambda: None)
    monkeypatch.setattr(entry, "PersistentClaudeRunner", lambda **_kwargs: object())
    return tmp_path, monkeypatch


def test_prepare_oauth_precedence_keeps_private_content_out_of_logs_and_outputs(
    environment, capsys
):
    root, monkeypatch = environment
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "synthetic-oauth-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-api-secret")
    captured = {}
    metadata = {
        "checkpoint_path": str(root / "checkpoint.bin"),
        "artifact_name": "opaque-artifact",
        "answers": [{"answer": "private-conversation-answer"}],
    }

    def prepare(*_args, **kwargs):
        captured.update(kwargs)
        return metadata

    monkeypatch.setattr(entry, "prepare_dialogue", prepare)
    destination = root / "private" / "metadata.json"
    assert entry.main(["prepare", "--metadata", str(destination)]) == 0
    assert captured["secret"] == "synthetic-oauth-secret"
    assert captured["billing_source"] == "subscription_usage"
    assert json.loads(destination.read_text()) == metadata
    assert destination.stat().st_mode & 0o777 == 0o600
    public = capsys.readouterr().out + (root / "output.txt").read_text()
    assert "private-conversation-answer" not in public
    assert "synthetic-oauth-secret" not in public and "synthetic-api-secret" not in public


def test_finalize_uses_only_github_auth_and_does_not_wake_native_provider(environment, capsys):
    root, monkeypatch = environment
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    metadata = root / "metadata.json"
    metadata.write_text(json.dumps({"private": "conversation-content"}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        entry, "finalize_dialogue", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    assert entry.main(["finalize", "--metadata", str(metadata), "--artifact-id", "201"]) == 0
    assert calls[0][1]["artifact_id"] == 201
    assert "conversation-content" not in capsys.readouterr().out


def test_missing_provider_auth_blocks_with_sanitized_error(environment, capsys):
    root, monkeypatch = environment
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert entry.main(["prepare", "--metadata", str(root / "metadata.json")]) == 1
    assert capsys.readouterr().err == "BLOCKED: claude_auth_missing; no automatic replay.\n"


def test_no_authorized_command_writes_only_not_ready_output(environment, capsys):
    root, monkeypatch = environment
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-api-secret")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(entry, "prepare_dialogue", lambda *_args, **_kwargs: None)
    metadata = root / "private" / "metadata.json"
    assert entry.main(["prepare", "--metadata", str(metadata)]) == 0
    assert (root / "output.txt").read_text() == "ready=false\n"
    assert not metadata.exists()
    assert "synthetic-api-secret" not in capsys.readouterr().out


@pytest.mark.parametrize("eligible", [False, True])
def test_check_phase_routes_before_cli_installation_or_provider_auth(environment, capsys, eligible):
    root, monkeypatch = environment
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(entry, "eligible_dialogue", lambda *_args: eligible)

    def no_preflight():
        raise AssertionError("Routing must not launch or require Claude")

    monkeypatch.setattr(entry, "_preflight", no_preflight)
    assert entry.main(["check", "--metadata", str(root / "unused.json")]) == 0
    assert (root / "output.txt").read_text() == f"eligible={str(eligible).lower()}\n"
    assert not (root / "unused.json").exists()
    assert "no model call" in capsys.readouterr().out


def test_invalid_github_identity_is_rejected_without_request(monkeypatch):
    calls = []
    monkeypatch.setattr(entry.requests, "request", lambda *_args, **_kwargs: calls.append(True))
    with pytest.raises(ValueError, match="^github_configuration_invalid$"):
        entry.GitHub("owner/Chat/../../private", "synthetic-token")
    with pytest.raises(ValueError, match="^github_configuration_invalid$"):
        entry.GitHub("owner/Chat", "")
    assert calls == []


def test_invalid_login_is_not_interpolated_into_collaborator_route(monkeypatch):
    calls = []
    monkeypatch.setattr(entry.requests, "request", lambda *_args, **_kwargs: calls.append(True))
    assert (
        entry.GitHub("owner/Chat", "synthetic-token").write_permission("owner/../secrets") is False
    )
    assert calls == []


def test_full_comment_pages_hit_bounded_queue_limit(monkeypatch):
    calls = []

    def request(*_args, **_kwargs):
        calls.append(True)
        return _response(payload=[{"id": index} for index in range(100)])

    monkeypatch.setattr(entry.requests, "request", request)
    with pytest.raises(ValueError, match="^github_queue_too_large$"):
        entry.GitHub("owner/Chat", "synthetic-token").list_comments(703)
    assert len(calls) == 20


def test_unsupported_cli_is_rejected_before_provider_request(monkeypatch):
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "unknown version", "synthetic-secret")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ValueError, match="^claude_cli_unsupported$"):
        entry._preflight()
    assert calls == [["claude", "--version"], ["claude", "--help"]]


@pytest.mark.parametrize("logged_in", [False, True, "true"])
def test_native_auth_status_is_checked_privately_before_any_model_turn(monkeypatch, logged_in):
    calls = []
    flags = "--safe-mode --restricted --tools --strict-mcp-config --session-id --resume --system-prompt-snapshot --permission-prompts"

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        output = (
            "2.1.283 (Claude Code)"
            if "--version" in argv
            else flags
            if "--help" in argv
            else json.dumps({"loggedIn": logged_in})
        )
        return subprocess.CompletedProcess(argv, 0, output, "synthetic-private-provider-error")

    monkeypatch.setattr(subprocess, "run", run)
    if logged_in is True:
        entry._preflight()
    else:
        with pytest.raises(ValueError, match="^claude_auth_missing$"):
            entry._preflight()
    assert calls[-1][0] == ["claude", "--safe-mode", "--restricted", "auth", "status", "--json"]
    assert all(kwargs["capture_output"] is True and kwargs["timeout"] == 15 for _, kwargs in calls)


def test_private_unrecognized_error_is_replaced_with_safe_status(environment, capsys):
    root, monkeypatch = environment
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-api-secret")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    def private_failure(*_args, **_kwargs):
        raise ValueError("synthetic-api-secret in private provider failure")

    monkeypatch.setattr(entry, "prepare_dialogue", private_failure)
    assert entry.main(["prepare", "--metadata", str(root / "metadata.json")]) == 1
    assert capsys.readouterr().err == "BLOCKED: session_unavailable; no automatic replay.\n"


def test_ci_output_values_cannot_inject_new_output_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output.txt"))
    with pytest.raises(ValueError, match="^ci_output_invalid$"):
        entry._outputs({"ready": "true\nsecret=must-not-be-written"})
    assert "must-not-be-written" not in (tmp_path / "output.txt").read_text()
