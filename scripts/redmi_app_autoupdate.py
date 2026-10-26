#!/usr/bin/env python3
"""Keep the existing Redmi Logos installation current without waiting for Actions."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import http.client
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import traceback
from typing import Iterator

REMOTE = "https://github.com/maksimp6/Chat.git"
REVISION = re.compile(r"^[0-9a-f]{40}$")


class UpdateFailure(RuntimeError):
    """A fixed diagnostic code, never arbitrary subprocess or provider output."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_bytes(path: Path, content: bytes, mode: int = 0o600) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_json(path: Path, value: dict) -> None:
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False) + "\n").encode())


def environment(app: Path, state: Path, port: int) -> dict[str, str]:
    # The public Git read and deployment need no Actions token or provider credential.
    allowed = {"HOME", "PATH", "PREFIX", "TMPDIR", "LD_PRELOAD", "LANG", "ANDROID_ROOT", "ANDROID_DATA"}
    result = {key: value for key, value in os.environ.items() if key in allowed}
    result.update(
        GIT_TERMINAL_PROMPT="0",
        GIT_CONFIG_GLOBAL=os.devnull,
        LOGOS_APP_DIR=str(app),
        LOGOS_STATE_DIR=str(state),
        LOGOS_PORT=str(port),
        LOGOS_DEPLOY_TRIGGER="local_autoupdate",
    )
    for key in ("LOGOS_REQUIRE_WIFI", "LOGOS_START_TIMEOUT"):
        if key in os.environ:
            result[key] = os.environ[key]
    return result


def run_git(arguments: list[str], env: dict[str, str], timeout: int, error: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-c", "credential.helper=", "-c", "credential.interactive=false", "-c", "http.extraHeader=", *arguments],
            env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateFailure(error) from exc
    if result.returncode:
        raise UpdateFailure(error)
    return result.stdout.strip()


def remote_revision(env: dict[str, str]) -> str:
    output = run_git(["ls-remote", "--exit-code", REMOTE, "refs/heads/develop"], env, 20, "remote_unavailable")
    fields = output.split()
    if len(fields) != 2 or fields[1] != "refs/heads/develop" or not REVISION.fullmatch(fields[0]):
        raise UpdateFailure("invalid_remote_revision")
    return fields[0]


def app_revision(app: Path, env: dict[str, str]) -> str:
    value = run_git(["-C", str(app), "rev-parse", "--verify", "HEAD"], env, 5, "app_revision_unavailable")
    if not REVISION.fullmatch(value):
        raise UpdateFailure("invalid_app_revision")
    return value


def backend_pid(state: Path) -> int | None:
    try:
        value = (state / "backend.pid").read_text().strip()
        return int(value) if value.isdecimal() and int(value) > 0 else None
    except OSError:
        return None


def process_owned(app: Path, pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        root = Path("/proc") / str(pid)
        if root.joinpath("cwd").resolve(strict=True) != app:
            return False
        arguments = root.joinpath("cmdline").read_bytes().split(b"\0")
        arguments = [value for value in arguments if value]
        if arguments != [str(app / ".venv/bin/python").encode(), b"app.py"]:
            return False
        if root.joinpath("stat").read_text().rsplit(") ", 1)[1].split()[0] == "Z":
            return False
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, IndexError):
        return False


def http_healthy(port: int) -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        body = response.read(4097)
        data = json.loads(body)
        return response.status == 200 and len(body) <= 4096 and isinstance(data, dict) and data.get("status") == "ok"
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def ready(app: Path, state: Path, revision: str, port: int) -> bool:
    pid = backend_pid(state)
    try:
        receipt = json.loads((state / "runner-start.json").read_text())
        matches = (
            receipt.get("status") == "ready"
            and receipt.get("app_revision") == revision
            and receipt.get("pid") == pid
        )
    except (OSError, ValueError, AttributeError):
        return False
    return matches and process_owned(app, pid) and http_healthy(port)


def refresh_worker(app: Path, state: Path) -> None:
    source = app / "scripts/redmi_app_autoupdate.py"
    target = state / "app-autoupdate.py"
    if source.is_file():
        try:
            content = source.read_bytes()
            compile(content, str(source), "exec")
            if not target.exists() or target.read_bytes() != content:
                atomic_bytes(target, content, 0o700)
        except (OSError, SyntaxError) as exc:
            raise UpdateFailure("worker_refresh_failed") from exc


def run_controller(app: Path, env: dict[str, str], action: str, timeout: int) -> int:
    script = app / "scripts/redmi_app_server.sh"
    if not script.is_file():
        raise UpdateFailure("deploy_script_missing")
    try:
        process = subprocess.Popen(
            ["bash", str(script), action], env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True, close_fds=True,
        )
    except OSError as exc:
        raise UpdateFailure("deploy_unavailable") from exc
    try:
        output, error = process.communicate(timeout=timeout)
        state = env.get("LOGOS_STATE_DIR")
        if state:
            atomic_bytes(Path(state) / "autoupdate-controller.log", (output + error)[-8192:].encode(), 0o600)
        return process.returncode
    except subprocess.TimeoutExpired as exc:
        # Only this controller's new process group is signalled. The backend launcher
        # deliberately detaches the actual application into its own session.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=5)
        raise UpdateFailure("deploy_timeout") from exc


@contextmanager
def iteration_lock(state: Path) -> Iterator[bool]:
    with (state / "autoupdate-worker.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def check_once(app: Path, state: Path, port: int = 8080, deploy_timeout: int = 600) -> dict:
    app, state = app.resolve(), state.resolve()
    if not app.is_dir() or not state.is_dir() or not (app / "app.py").is_file():
        return {"result": "error", "error": "existing_installation_missing", "checked_at": now()}
    with iteration_lock(state) as acquired:
        if not acquired:
            return {"result": "busy", "checked_at": now()}
        record = {"checked_at": now(), "trigger": "local_autoupdate", "pid_before": backend_pid(state)}
        attempted = False
        try:
            if (state / "autoupdate.disabled").exists():
                record["result"] = "disabled"
            else:
                env = environment(app, state, port)
                installed = app_revision(app, env)
                healthy = ready(app, state, installed, port)
                record.update(app_revision_before=installed, healthy_before=healthy, latest_revision_verified=False)
                try:
                    observed = remote_revision(env)
                except UpdateFailure as exc:
                    # Offline GitHub must not prevent recovery of the installed application.
                    owned_before = process_owned(app, record["pid_before"])
                    if not (owned_before and http_healthy(port)):
                        action = "restart" if owned_before else "start"
                        attempted = True
                        record["action"] = action
                        code = run_controller(app, env, action, deploy_timeout)
                        record.update(deploy_exit_code=code, pid_after=backend_pid(state))
                        if code or not ready(app, state, installed, port):
                            raise UpdateFailure("local_recovery_failed") from exc
                        record.update(result="recovered_local", app_revision=installed,
                                      healthy_after=True, error=str(exc))
                        refresh_worker(app, state)
                    else:
                        raise
                else:
                    record["observed_revision"] = observed
                    if observed == installed and healthy:
                        record.update(result="unchanged", app_revision=installed,
                                      pid_after=record["pid_before"], latest_revision_verified=True)
                        refresh_worker(app, state)
                    else:
                        attempted = True
                        record["action"] = "deploy"
                        code = run_controller(app, env, "deploy", deploy_timeout)
                        record["deploy_exit_code"] = code
                        if code:
                            raise UpdateFailure("deploy_failed")
                        final_revision = app_revision(app, env)
                        final_remote = remote_revision(env)
                        record.update(app_revision=final_revision, verified_remote_revision=final_remote, pid_after=backend_pid(state))
                        if final_revision != final_remote or not ready(app, state, final_revision, port):
                            raise UpdateFailure("deploy_verification_failed")
                        record["result"] = "deployed" if installed != final_revision else "recovered"
                        record.update(healthy_after=True, latest_revision_verified=True)
                        refresh_worker(app, state)
        except UpdateFailure as exc:
            record.update(result="error", error=str(exc), pid_after=backend_pid(state))
        except Exception:
            # Private subprocess output and exception details never enter the public receipt.
            traceback.print_exc(file=sys.stderr)
            record.update(result="error", error="updater_error", pid_after=backend_pid(state))
        record["completed_at"] = now()
        save_json(state / "autoupdate-status.json", record)
        if attempted:
            save_json(state / "autoupdate-last-deploy.json", record)
        return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Perform one check; the boot supervisor schedules retries.")
    parser.add_argument("--app", type=Path, default=Path(os.environ.get("LOGOS_APP_DIR", Path.home() / "logos-local")))
    parser.add_argument("--state", type=Path, default=Path(os.environ.get("LOGOS_STATE_DIR", Path.home() / "logos-local-state")))
    parser.add_argument("--port", type=int, default=os.environ.get("LOGOS_PORT", "8080"))
    parser.add_argument("--deploy-timeout", type=int, default=600)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or not 1 <= args.deploy_timeout <= 900:
        parser.error("Invalid port or deployment timeout")
    try:
        result = check_once(args.app, args.state, args.port, args.deploy_timeout)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        result = {"result": "error", "error": "updater_state_unavailable", "checked_at": now()}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return int(result["result"] == "error")


if __name__ == "__main__":
    raise SystemExit(main())
