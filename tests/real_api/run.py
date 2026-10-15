#!/usr/bin/env python3
"""Opt-in real-key acceptance; never collected or invoked by ordinary pytest/CI."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import local_launch_smoke as smoke


class AcceptanceFailure(RuntimeError):
    """Only fixed, non-secret failure codes cross the CLI boundary."""


def private_directory(path: Path) -> Path:
    """Reject symlinks and other-owner/writable parents before creating state."""
    path = path.expanduser().absolute()
    for parent in reversed((path, *path.parents)):
        if parent.is_symlink():
            raise AcceptanceFailure("unsafe_directory")
        if parent.exists():
            mode = parent.stat()
            unsafe_write = stat.S_IMODE(mode.st_mode) & 0o022 and not mode.st_mode & stat.S_ISVTX
            if mode.st_uid not in {0, os.getuid()} or unsafe_write:
                raise AcceptanceFailure("unsafe_directory")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    mode = path.stat()
    if mode.st_uid != os.getuid() or stat.S_IMODE(mode.st_mode) != 0o700:
        raise AcceptanceFailure("directory_requires_owner_0700")
    return path


def read_key(path: Path) -> str:
    """Read the existing phone JSON secret via an owner-only, no-follow fd."""
    path = path.expanduser().absolute()
    # The containing directory must itself be private. Never chmod a secret source.
    private_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_size > 8192
        ):
            raise AcceptanceFailure("secret_requires_owner_0600_regular_file")
        value = json.loads(stream.read(8193)).get("api_key")
    if not isinstance(value, str) or not 20 <= len(value.strip()) <= 4096:
        raise AcceptanceFailure("invalid_secret_input")
    return value.strip()


def write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def copy_committed_source(destination: Path) -> str:
    """No untracked files, local credentials or previous runtime state are copied."""
    head = smoke._git_state(require_master=False)["head_sha"]
    snapshot = subprocess.check_output(["git", "archive", "--format=tar", head], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(snapshot)) as archive:
        for member in archive:
            relative = Path(member.name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not (member.isfile() or member.isdir())
            ):
                raise AcceptanceFailure("source_requires_regular_tracked_files")
            target = destination / relative
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with archive.extractfile(member) as stream:
                    write_private(target, stream.read())
    return head


def runtime_environment(
    work: Path, port: int, encryption_key: str, admin_token: str
) -> dict[str, str]:
    """Real provider key is NOT passed in environment or process arguments."""
    env = {
        name: os.environ[name]
        for name in ("PATH", "LANG", "SSL_CERT_FILE", "SSL_CERT_DIR")
        if name in os.environ
    }
    env.update(
        HOME=str(work),
        TMPDIR=str(work),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONUNBUFFERED="1",
        HOST="127.0.0.1",
        PORT=str(port),
        FLASK_DEBUG="0",
        ALICE_MEMORY_PATH=str(work / "alice.memory"),
        ALICE_REQUIRE_SHORT_TOKEN="0",
        ALICE_PROVIDER_CREDENTIAL_KEY=encryption_key,
        ALICE_PROVIDER_CREDENTIALS_TOKEN=admin_token,
        YANDEX_BASE_URL="https://ai.api.cloud.yandex.net/v1",
    )
    return env


class LocalSession(smoke.requests.Session):
    """A local import must never forward the real key to a redirect or proxy."""

    def __init__(self) -> None:
        super().__init__()
        self.trust_env = False

    def request(self, method, url, **kwargs):
        kwargs["allow_redirects"] = False
        return super().request(method, url, **kwargs)


def assert_no_plaintext(root: Path, key: str) -> None:
    needle = key.encode()
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as stream:
            tail = b""
            while chunk := stream.read(65536):
                block = tail + chunk
                if needle in block:
                    raise AcceptanceFailure("plaintext_key_in_runtime_or_logs")
                tail = block[-len(needle) :]


def verify_history(http, url: str, conversation: str, reply: str) -> None:
    _, data = smoke._request_json(http, "GET", url, f"/api/conversations/{conversation}/messages")
    messages = data.get("messages", []) if isinstance(data, dict) else []
    if not any(m.get("role") == "user" and m.get("content") == "Привет" for m in messages):
        raise AcceptanceFailure("user_message_missing")
    if not any(m.get("role") == "assistant" and m.get("content") == reply for m in messages):
        raise AcceptanceFailure("assistant_message_missing")


def verify_provider_response(trace: dict) -> dict:
    responses = trace.get("responses") or []
    if len(trace.get("api_requests") or []) != 1 or len(responses) != 1:
        raise AcceptanceFailure("expected_one_model_request")
    raw = responses[0].get("raw") or {}
    if (
        not isinstance(raw, dict)
        or not raw.get("id")
        or raw.get("status") != "completed"
        or not raw.get("output")
    ):
        raise AcceptanceFailure("completed_provider_response_missing")
    if trace.get("tool_calls"):
        raise AcceptanceFailure("unexpected_tool_call")
    return raw


def exchange(http, url: str, args, evidence: dict):
    session = smoke._create_session(http, url)
    evidence["stage"] = "conversation_create"
    _, created = smoke._request_json(
        http,
        "POST",
        url,
        "/api/conversations",
        payload={"model": args.model, "title": "Real API acceptance"},
        expected=(200, 201),
    )
    conversation = created.get("id")
    if not isinstance(conversation, str) or not conversation:
        raise AcceptanceFailure("conversation_id_missing")
    evidence["checks"]["conversation_create"] = True
    evidence["stage"] = "real_response"
    _, chat = smoke._request_json(
        http,
        "POST",
        url,
        "/api/chat",
        payload={
            "conversation_id": conversation,
            "session_id": session,
            "message": "Привет",
            "model": args.model,
            "params": {
                "background": False,
                "max_output_tokens": args.max_output_tokens,
                "tool_choice": "none",
                "active_tool_categories": [],
                "instructions": "Ответь одним коротким приветствием. Инструменты не вызывай.",
                "tools_config": {
                    name: {"enabled": False}
                    for name in (
                        "web_search",
                        "file_search",
                        "code_interpreter",
                        "image_generation",
                    )
                },
            },
        },
    )
    reply = chat.get("reply")
    invocation, trace_id = chat.get("invocation_id"), chat.get("trace_id")
    if (
        not isinstance(reply, str)
        or not reply.strip()
        or not invocation
        or not trace_id
        or chat.get("error")
    ):
        raise AcceptanceFailure("real_reply_or_correlation_missing")
    smoke._trace_persisted(http, url, invocation_id=invocation, trace_id=trace_id)
    _, trace = smoke._request_json(http, "GET", url, f"/api/invocations/{invocation}/trace")
    raw = verify_provider_response(trace)
    evidence["checks"]["real_response"] = True
    verify_history(http, url, conversation, reply)
    evidence["checks"]["history"] = True
    return conversation, reply, invocation, trace_id, raw


def real_acceptance(args, work: Path, evidence: dict) -> None:
    evidence["stage"] = "secret_input"
    key = read_key(Path(args.secret_file))
    evidence["stage"] = "source"
    source = work / "source"
    source.mkdir(mode=0o700)
    evidence["head_sha"] = copy_committed_source(source)
    from cryptography.fernet import Fernet

    encryption_key = Fernet.generate_key().decode("ascii")
    admin_token = secrets.token_urlsafe(32)
    write_private(work / "credential-encryption.key", encryption_key.encode())
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = runtime_environment(work, port, encryption_key, admin_token)
    http = LocalSession()
    http.headers["Authorization"] = "Bearer " + admin_token
    server = None
    try:
        evidence["stage"] = "startup"
        server = smoke._start_server(
            env=env, work_dir=work, base_url=url, attempt=1, source_root=source
        )
        evidence["stage"] = "real_key_import"
        smoke._configure_yandex(http, url, key, args.project_id)
        evidence["checks"]["key_import"] = True
        # Reject plaintext persistence/logging before spending on a generation.
        assert_no_plaintext(work, key)
        conversation, reply, invocation, trace_id, raw = exchange(http, url, args, evidence)
        evidence["stage"] = "restart"
        server.stop()
        server = None
        assert_no_plaintext(work, key)
        server = smoke._start_server(
            env=env, work_dir=work, base_url=url, attempt=2, source_root=source
        )
        verify_history(http, url, conversation, reply)
        smoke._trace_persisted(http, url, invocation_id=invocation, trace_id=trace_id)
        # Restart must resolve existing credentials, NOT reimport them.
        _, status = smoke._request_json(
            http,
            "POST",
            url,
            "/api/provider-credentials/status/check",
            payload={"provider": "yandex"},
        )
        smoke._assert_yandex_connected(status)
        evidence["checks"]["restart"] = True
        server.stop()
        server = None
        assert_no_plaintext(work, key)
        evidence["checks"]["no_plaintext_key"] = True
        evidence.update(
            status="PASS",
            stage="complete",
            conversation_id=conversation,
            invocation_id=invocation,
            trace_id=trace_id,
            provider_response_id=raw["id"],
        )
        if any(value in json.dumps(evidence) for value in (key, encryption_key, admin_token)):
            raise AcceptanceFailure("secret_in_evidence")
    finally:
        if server is not None:
            server.stop()
        http.close()


def probe_installed(args, work: Path, evidence: dict) -> None:
    evidence["test_type"] = "real_api_installed_backend"
    evidence["stage"] = "installed_provider_status"
    evidence["runner_head_sha"] = smoke._git_output("rev-parse", "HEAD")
    evidence["target_build"] = args.installed_build
    with LocalSession() as http:
        # A normal anonymous identity keeps this test conversation separate.
        smoke._request_json(
            http,
            "POST",
            "http://127.0.0.1:5000",
            "/api/users/bootstrap",
            payload={
                "installation_id": evidence["run_id"],
                "metadata": {"source": "real-api-acceptance"},
            },
        )
        _, status = smoke._request_json(
            http, "GET", "http://127.0.0.1:5000", "/api/provider-credentials/status"
        )
        smoke._assert_yandex_connected(status)
        evidence["checks"]["provider_configured"] = True
        exchange(http, "http://127.0.0.1:5000", args, evidence)
    # No restart or secret-import claim is possible against an existing instance.
    evidence.update(status="PASS", stage="complete")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-paid-api",
        action="store_true",
        help="Authorize one bounded real chat; no currency cap is guaranteed.",
    )
    parser.add_argument(
        "--secret-file", help="Existing owner-only JSON file containing api_key, outside Git."
    )
    parser.add_argument("--project-id")
    parser.add_argument(
        "--probe-installed",
        action="store_true",
        help="Check existing loopback:5000 app; never import/replace its key or restart it.",
    )
    parser.add_argument(
        "--installed-build", help="Operator-observed installed build; not a verified source SHA."
    )
    parser.add_argument(
        "--model", required=True, help="Explicit Yandex model slug, e.g. aliceai-llm-flash."
    )
    parser.add_argument(
        "--state-root",
        required=True,
        help="Owner-only 0700 directory outside Git for private runtime/evidence.",
    )
    parser.add_argument("--max-output-tokens", type=int, default=32)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.allow_paid_api:
        print('{"status":"BLOCKED","stage":"explicit_opt_in_required"}')
        return 2
    if (
        not 1 <= args.max_output_tokens <= 64
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", args.model)
        or (
            not args.probe_installed
            and (
                not args.secret_file
                or not args.project_id
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", args.project_id)
            )
        )
    ):
        print('{"status":"BLOCKED","stage":"invalid_bounds"}')
        return 2
    started = time.monotonic()
    evidence = {
        "test_type": "real_api",
        "run_id": str(uuid.uuid4()),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "FAIL",
        "stage": "protected_state",
        "checks": {},
        "provider_confirmed_cost": None,
        "android_ui_verified": False,
    }
    work = None
    old_umask = os.umask(0o077)
    old_sigterm = signal.getsignal(signal.SIGTERM)

    def interrupted(signum, frame):
        raise AcceptanceFailure("interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        state = private_directory(Path(args.state_root))
        secret = (
            Path(args.secret_file).expanduser().absolute() if args.secret_file else state / "unused"
        )
        if any(
            (ancestor / ".git").exists()
            for path in (state, secret.parent)
            for ancestor in (path, *path.parents)
        ):
            raise AcceptanceFailure("secrets_and_state_must_be_outside_checkout")
        work = state / evidence["run_id"]
        work.mkdir(mode=0o700)
        if args.probe_installed:
            probe_installed(args, work, evidence)
        else:
            real_acceptance(args, work, evidence)
    except (Exception, KeyboardInterrupt):
        # Never stringify exceptions/HTTP payloads or print traceback/locals with a real key.
        evidence["status"] = "FAIL"
        for name in ("conversation_id", "invocation_id", "trace_id", "provider_response_id"):
            evidence.pop(name, None)
    finally:
        evidence["duration_seconds"] = round(time.monotonic() - started, 3)
        try:
            if work is not None:
                write_private(work / "result.json", json.dumps(evidence, sort_keys=True).encode())
        finally:
            os.umask(old_umask)
            signal.signal(signal.SIGTERM, old_sigterm)
    print(json.dumps(evidence, sort_keys=True))
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
