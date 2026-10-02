from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet
import pytest

from scripts import local_launch_smoke as smoke


class _Response:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    def __init__(self, response: _Response):
        self.response = response
        self.calls = []

    def request(self, method, url, json=None, timeout=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "json": json,
                "timeout": timeout,
            }
        )
        return self.response


def test_build_runtime_env_isolated_sqlite_and_valid_credential_key(monkeypatch, tmp_path):
    monkeypatch.setenv("ALICE_DATABASE_URL", "postgresql://should-not-survive")
    monkeypatch.delenv("ALICE_PROVIDER_CREDENTIAL_KEY", raising=False)

    db_path = tmp_path / "launch.db"
    env = smoke._build_runtime_env(db_path=db_path, port=8765)

    assert env["HOST"] == "127.0.0.1"
    assert env["PORT"] == "8765"
    assert env["FLASK_DEBUG"] == "0"
    assert env["ALICE_DB_PATH"] == str(db_path)
    assert env["ALICE_REQUIRE_SHORT_TOKEN"] == "0"
    assert env["ALICE_PROVIDER_CREDENTIALS_TOKEN"] == ""
    assert "ALICE_DATABASE_URL" not in env
    Fernet(env["ALICE_PROVIDER_CREDENTIAL_KEY"].encode("ascii"))


def test_yandex_connected_requires_connected_authorized_status():
    smoke._assert_yandex_connected(
        {
            "providers": [
                {
                    "provider": "yandex",
                    "status": "connected",
                    "authorization_ok": True,
                }
            ]
        }
    )

    with pytest.raises(smoke.SmokeFailure, match="not connected"):
        smoke._assert_yandex_connected(
            {
                "providers": [
                    {
                        "provider": "yandex",
                        "status": "configured",
                        "authorization_ok": False,
                    }
                ]
            }
        )


def test_git_state_fails_closed_on_tracked_changes(monkeypatch):
    outputs = {
        ("rev-parse", "HEAD"): "abc123",
        ("status", "--porcelain", "--untracked-files=no"): " M app.py",
    }
    monkeypatch.setattr(smoke, "_git_output", lambda *args: outputs[args])

    with pytest.raises(smoke.SmokeFailure, match="not clean"):
        smoke._git_state(require_master=False)


def test_git_state_requires_exact_master_head(monkeypatch):
    outputs = {
        ("rev-parse", "HEAD"): "feature-head",
        ("status", "--porcelain", "--untracked-files=no"): "",
        ("rev-parse", "refs/remotes/origin/master"): "master-head",
    }
    monkeypatch.setattr(smoke, "_git_output", lambda *args: outputs[args])

    with pytest.raises(smoke.SmokeFailure, match="not the current master"):
        smoke._git_state(require_master=True)


def test_trace_persistence_checks_invocation_and_trace_correlation():
    session = _Session(
        _Response(
            200,
            {
                "schema_version": 1,
                "trace_id": "trace-1",
                "context": {
                    "invocation_id": "inv-1",
                    "trace_id": "trace-1",
                },
            },
        )
    )

    smoke._trace_persisted(
        session,
        "http://127.0.0.1:8765",
        invocation_id="inv-1",
        trace_id="trace-1",
    )

    assert session.calls == [
        {
            "method": "GET",
            "url": "http://127.0.0.1:8765/api/invocations/inv-1/trace",
            "json": None,
            "timeout": smoke.REQUEST_TIMEOUT,
        }
    ]


def test_trace_persistence_rejects_wrong_invocation():
    session = _Session(
        _Response(
            200,
            {
                "schema_version": 1,
                "trace_id": "trace-1",
                "context": {
                    "invocation_id": "other-invocation",
                    "trace_id": "trace-1",
                },
            },
        )
    )

    with pytest.raises(smoke.SmokeFailure, match="invocation correlation"):
        smoke._trace_persisted(
            session,
            "http://127.0.0.1:8765",
            invocation_id="inv-1",
            trace_id="trace-1",
        )


def test_partial_provider_input_fails_without_printing_secret(monkeypatch, capsys):
    secret = "launch-smoke-secret-marker"
    monkeypatch.setenv("ALICE_LAUNCH_SMOKE_YANDEX_API_KEY", secret)
    monkeypatch.delenv("ALICE_LAUNCH_SMOKE_YANDEX_PROJECT_ID", raising=False)
    monkeypatch.setattr(
        smoke,
        "_git_state",
        lambda *, require_master: {"head_sha": "abc123"},
    )

    assert smoke.main([]) == 1

    output = capsys.readouterr().out
    assert '"status": "FAIL"' in output
    assert "both API key and project id" in output
    assert secret not in output


def test_offline_mode_does_not_require_complete_provider_pair(monkeypatch):
    monkeypatch.setenv("ALICE_LAUNCH_SMOKE_YANDEX_API_KEY", "partial-secret")
    monkeypatch.delenv("ALICE_LAUNCH_SMOKE_YANDEX_PROJECT_ID", raising=False)

    args = smoke._parse_args(["--offline"])

    assert args.offline is True
    assert smoke._provider_inputs() == ("partial-secret", "")


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (
            {"trace_id": "trace-1", "context": {"invocation_id": "inv-1", "trace_id": "trace-1"}},
            "ExecutionTrace is missing",
        ),
        (
            {
                "schema_version": 1,
                "trace_id": "wrong",
                "context": {"invocation_id": "inv-1", "trace_id": "trace-1"},
            },
            "trace id does not match",
        ),
        (
            {
                "schema_version": 1,
                "trace_id": "trace-1",
                "context": {"invocation_id": "inv-1", "trace_id": "wrong"},
            },
            "trace id does not match",
        ),
    ],
)
def test_trace_persistence_rejects_synthetic_and_one_sided_mismatches(payload, reason):
    session = _Session(_Response(200, payload))

    with pytest.raises(smoke.SmokeFailure, match=reason):
        smoke._trace_persisted(
            session,
            "http://127.0.0.1:8765",
            invocation_id="inv-1",
            trace_id="trace-1",
        )
