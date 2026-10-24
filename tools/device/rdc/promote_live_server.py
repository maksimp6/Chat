#!/usr/bin/env python3
"""Promote patched localhost Live Server, retaining independent rollback.

This does NOT restart or stop RDC. It does not touch root services or send
ADB device actions outside the Live Server HTTP interface.
"""

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = Path.home() / ".alice-rdc"
SERVER = BASE / "android_live_server.py"
ACTIONS = BASE / "android_actions.py"
TOKEN = (BASE / "live-token").read_text().strip()
PORT = 8765
LOG = BASE / "live-server.log"
BACKUPS = sorted((BASE / "repairs").glob("live-*/android_live_server.py"))
if not BACKUPS:
    raise RuntimeError("No tested live-server backup; aborting")
BACKUP_DIR = BACKUPS[-1].parent


def live_pids():
    matching = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = (entry / "cmdline").read_bytes().split(bytes([0]))
            if args[-1] == b"":
                args.pop()
            if args and args[-1].decode(errors="ignore") == str(SERVER):
                matching.append(int(entry.name))
        except (OSError, ValueError):
            continue
    return matching


def reachable():
    with socket.socket() as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", PORT)) == 0


def request(path, authorized=True):
    headers = {}
    if authorized:
        headers["Authorization"] = "Bearer " + TOKEN
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            status = response.status
            raw = response.read(4096)
    except urllib.error.HTTPError as error:
        status = error.code
        raw = error.read(4096)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        payload = {}
    return status, payload


def start_service(path):
    with LOG.open("ab") as logfile:
        child = subprocess.Popen(
            [sys.executable, "-u", str(path)],
            cwd=BASE,
            stdin=subprocess.DEVNULL,
            stdout=logfile,
            stderr=logfile,
            start_new_session=True,
            close_fds=True,
        )
    return child


def stop_service(pid):
    os.kill(pid, signal.SIGTERM)
    for _ in range(35):
        if not reachable():
            return
        time.sleep(0.12)
    raise RuntimeError("old Live Server did not release port")


def check_ready(child, strict=True):
    for _ in range(45):
        if child.poll() is not None:
            raise RuntimeError("new Live Server exited unexpectedly")
        try:
            health, info = request("/health")
            if health == 200 and (not strict or info.get("device_policy") == "redmi9_bound"):
                status, details = request("/status")
                if status == 200 and (not strict or details.get("target") == "Redmi_9"):
                    if request("/health", authorized=False)[0] != 401:
                        raise RuntimeError("Bearer authentication regressed")
                    return True
        except (OSError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.15)
    raise RuntimeError("Live Server readiness or device binding failed")


def restore_backup():
    # Roll back only these two source files; keep existing token/phone state.
    for filename, destination in (
        ("android_live_server.py", SERVER),
        ("android_actions.py", ACTIONS),
    ):
        source = BACKUP_DIR / filename
        if not source.exists():
            raise RuntimeError(f"Backup incomplete: {filename}")
        temp = destination.with_name(destination.name + ".restoring")
        temp.write_bytes(source.read_bytes())
        temp.chmod(0o600)
        os.replace(temp, destination)


def main():
    old = live_pids()
    if len(old) != 1 or not reachable():
        raise RuntimeError("Live Server identity/port ambiguous; refused restart")
    old_pid = old[0]
    stop_service(old_pid)
    new = None
    try:
        new = start_service(SERVER)
        check_ready(new, strict=True)
        # A return from /screen/semantics validates the previously broken
        # accessibility helper with the pinned ANDROID_SERIAL, no UI text logged.
        sem_status, sem = request("/screen/semantics")
        if sem_status != 200:
            raise RuntimeError("Screen semantics unavailable on updated Live Server")
        print(
            json.dumps(
                {
                    "result": "promoted",
                    "primary_health": 200,
                    "primary_status": 200,
                    "authorized_device": "Redmi_9",
                    "bearer_auth": "enforced",
                    "screen_semantics": 200,
                    "semantic_nodes": sem.get("count"),
                    "new_pid": new.pid,
                    "rdc": "untouched",
                }
            )
        )
    except BaseException as error:
        if new is not None and new.poll() is None:
            new.terminate()
            try:
                new.wait(timeout=3)
            except subprocess.TimeoutExpired:
                new.kill()
                new.wait(timeout=2)
        for _ in range(15):
            if not reachable():
                break
            time.sleep(0.1)
        restore_backup()
        original = start_service(SERVER)
        check_ready(original, strict=False)
        print(json.dumps({"result": "rolled_back", "reason": str(error), "rdc": "untouched"}))
        raise


if __name__ == "__main__":
    main()
