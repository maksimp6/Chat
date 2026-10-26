#!/usr/bin/env python3
"""Retire the old Termux updater once, from the existing runner's deploy job."""

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def acquire(stream, seconds):
    deadline = time.monotonic() + seconds
    while True:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise RuntimeError("autoupdate_still_busy") from None
            time.sleep(0.1)


def supervisor_arguments(pid):
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[:-1]
    except PermissionError:
        # Android hides other /proc entries even from the same Termux account.
        # Read only this PID's command; never process environments or credentials.
        result = subprocess.run(
            ["/system/bin/su", "-c", f"cat /proc/{pid}/cmdline"],
            capture_output=True, timeout=5, check=False,
        )
        if result.returncode:
            raise RuntimeError("autoupdate_identity_unavailable") from None
        return result.stdout.rstrip(b"\0").split(b"\0")


def main():
    model = subprocess.run(
        ["/system/bin/getprop", "ro.product.model"], capture_output=True,
        text=True, timeout=3, check=True,
    ).stdout.strip()
    if model != "Redmi 9" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("redmi9_runner_required")
    home = Path.home()
    state = Path(os.environ.get("LOGOS_STATE_DIR", home / "logos-local-state"))
    app = Path(os.environ.get("LOGOS_APP_DIR", home / "logos-local"))
    if not state.is_dir() or not (app / "app.py").is_file():
        raise RuntimeError("existing_installation_required")
    boot = home / ".termux/boot/60-logos-app-autoupdate"
    sources = [boot, state / "app-autoupdate-boot.sh", state / "app-autoupdate.py"]
    present = [source for source in sources if source.exists()]
    archive = None
    if present:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        archive = state / "retired-autoupdate" / stamp
        archive.mkdir(parents=True, mode=0o700)
        # Preserve the status before the disabled marker can change it.
        for name in ["autoupdate-status.json", "autoupdate-last-deploy.json"]:
            source = state / name
            if source.is_file():
                target = archive / name
                target.write_bytes(source.read_bytes())
                target.chmod(0o600)
    (state / "autoupdate.disabled").touch(mode=0o600, exist_ok=True)
    # An in-flight worker owns this lock until its controller finishes. Do not
    # terminate a controller while it may be between stopping and starting the app.
    with (state / "autoupdate-worker.lock").open("a+") as worker_lock:
        acquire(worker_lock, 60)
        with (state / "autoupdate-supervisor.lock").open("a+") as supervisor_lock:
            stopped_pid = None
            try:
                acquire(supervisor_lock, 0)
            except RuntimeError:
                supervisor_lock.seek(0)
                value = supervisor_lock.read().strip()
                if not value.isdecimal() or int(value) <= 1:
                    raise RuntimeError("invalid_autoupdate_supervisor_pid") from None
                pid = int(value)
                arguments = supervisor_arguments(pid)
                expected = {
                    str(state / "app-autoupdate-boot.sh").encode(),
                    str(app / "scripts/redmi_app_autoupdate_boot.sh").encode(),
                }
                if (len(arguments) != 3 or Path(os.fsdecode(arguments[0])).name != "bash"
                        or arguments[1] not in expected or arguments[2] != b"run"):
                    raise RuntimeError("autoupdate_supervisor_identity_mismatch")
                # Signal this validated supervisor only, never its process group,
                # the backend, runner, or the independent RDC connection.
                os.kill(pid, signal.SIGTERM)
                # The supervisor's current 30-second sleep may inherit its lock.
                acquire(supervisor_lock, 35)
                stopped_pid = pid
            pid_path = state / "backend.pid"
            before = pid_path.read_text().strip() if pid_path.exists() else None
            retired = []
            for source in present:
                content = source.read_bytes()
                target = archive / (source.name + ".txt")
                os.replace(source, target)
                target.chmod(0o600)
                retired.append({"path": str(source), "sha256": hashlib.sha256(content).hexdigest()})
            after = pid_path.read_text().strip() if pid_path.exists() else None
            result = {
                "result": "removed" if present else "already_removed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "supervisor_stopped_pid": stopped_pid,
                "supervisor_lock_free_before_removal": True,
                "retired": retired,
                "archive": str(archive) if archive else None,
                "backend_pid_before": before, "backend_pid_after": after,
                "boot_entry_absent": not boot.exists(),
            }
            if present or stopped_pid:
                receipt = state / "autoupdate-removal.json"
                temporary = receipt.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(result, ensure_ascii=False) + "\n")
                temporary.chmod(0o600)
                os.replace(temporary, receipt)
            print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
