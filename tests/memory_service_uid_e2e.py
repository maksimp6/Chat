"""Linux separate-service-UID IPC E2E. Requires privileged isolated CI runner."""

import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import subprocess
import sys
import tempfile
import time


def request(path: Path, payload: dict[str, object]) -> dict[str, object] | None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(2)
        conn.connect(str(path))
        conn.sendall((json.dumps(payload) + "\n").encode())
        try:
            data = conn.recv(4096)
        except ConnectionResetError:
            return None
    return json.loads(data) if data else None


def main() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("requires isolated privileged Linux runner")
    owner = pwd.getpwnam("memorydb_owner")
    client = pwd.getpwnam("memorydb_attacker")
    assert owner.pw_uid != client.pw_uid
    with tempfile.TemporaryDirectory(prefix="memory-ipc-") as tmp:
        root = Path(tmp)
        root.chmod(0o755)
        shutil.copytree(
            Path(__file__).resolve().parents[1] / "memory_engine", root / "memory_engine"
        )
        for entry in (root / "memory_engine").rglob("*"):
            entry.chmod(0o755 if entry.is_dir() else 0o644)
        private = root / "private"
        private.mkdir(mode=0o700)
        os.chown(private, owner.pw_uid, owner.pw_gid)
        private.chmod(0o700)
        socket_dir = root / "socket-dir"
        socket_dir.mkdir(mode=0o755)
        os.chown(socket_dir, owner.pw_uid, owner.pw_gid)
        socket_dir.chmod(0o755)
        sock = socket_dir / "service.sock"
        env = os.environ.copy()
        env["ALICE_MEMORY_TRUSTED_ROOT"] = "1"
        env["PYTHONPATH"] = str(root)
        proc = subprocess.Popen(
            [
                "runuser",
                "-u",
                owner.pw_name,
                "--",
                sys.executable,
                "-m",
                "memory_engine.service",
                "--database",
                str(private / "db.memory"),
                "--socket",
                str(sock),
                "--allowed-uid",
                str(client.pw_uid),
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            for _ in range(100):
                if sock.exists():
                    break
                if proc.poll() is not None:
                    raise AssertionError(proc.stderr.read())
                time.sleep(0.05)
            assert sock.exists(), "service did not start"
            print(\n                "IPC_PERMISSIONS",\n                oct(sock.stat().st_mode & 0o777),\n                oct(socket_dir.stat().st_mode & 0o777),\n                oct(root.stat().st_mode & 0o777),\n                flush=True,\n            )
            # A root client is NOT the allowed UID.
            assert request(sock, {"op": "info"}) is None
            code = (
                "import json,socket,sys; s=socket.socket(socket.AF_UNIX); "
                "s.connect(sys.argv[1]); "
                "s.sendall((json.dumps({'op':'set','key':'safe','value':7})+'\\n').encode()); "
                "print(s.recv(4096).decode()); s.close()"
            )
            result = subprocess.run(
                ["runuser", "-u", client.pw_name, "--", sys.executable, "-c", code, str(sock)],
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            assert '"ok":true' in result.stdout, result.stdout
            denied = subprocess.run(
                [
                    "runuser",
                    "-u",
                    client.pw_name,
                    "--",
                    sys.executable,
                    "-c",
                    "import pathlib,sys; pathlib.Path(sys.argv[1]).rename(sys.argv[2])",
                    str(private / "db.memory"),
                    str(private / "stolen.memory"),
                ],
                capture_output=True,
                text=True,
            )
            assert denied.returncode != 0 and "PermissionError" in denied.stderr
            assert (private / "db.memory").exists()
            print("PASS: isolated service UID, peer-credential IPC, direct rename denied")
        finally:
            proc.terminate()
            try:
                proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()


if __name__ == "__main__":
    main()
