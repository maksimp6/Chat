"""Alice Mouse minimal trusted root bridge.

One root process owns a unix socket in Termux private directory. It validates
SO_PEERCRED against the exact Termux app UID and forwards only bounded move/click.
No shell execution of user input, no HTTP listener and no text injection.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import socket
import stat
import struct
import subprocess
from pathlib import Path

TERMUX_DIR = Path("/data/data/com.termux/files/home/.alice-rdc")
SOCKET_PATH = TERMUX_DIR / "mouse-trusted.sock"
DAEMON_SOCKET = Path("/data/adb/alice-mouse/control.sock")
ALLOWED = re.compile(rb"(move|click) (-?[0-9]{1,4}) (-?[0-9]{1,4})\n?\Z")
EXPECTED_BROWSER = "com.yandex.browser"
MAX_REQUEST = 64


def decode_command(payload: bytes) -> tuple[str, int, int] | None:
    match = ALLOWED.fullmatch(payload)
    if match is None:
        return None
    op = match.group(1).decode("ascii")
    a, b = int(match.group(2)), int(match.group(3))
    if abs(a) > 500 or abs(b) > 500:
        return None
    return op, a, b


def focus_is_safe() -> bool:
    try:
        result = subprocess.run(
            ["/system/bin/dumpsys", "window"],
            capture_output=True,
            timeout=2,
            text=True,
            check=True,
        )
        line = next((line for line in result.stdout.splitlines() if "mCurrentFocus=" in line), "")
        return EXPECTED_BROWSER + "/" in line
    except (OSError, subprocess.SubprocessError):
        return False


def identity(peer: socket.socket) -> tuple[int, int, int]:
    raw = peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", raw)


def ensure_private_dir(termux_uid: int) -> None:
    parent = TERMUX_DIR.stat()
    if parent.st_uid != termux_uid or parent.st_mode & 0o077:
        raise PermissionError("bridge parent must be owned by Termux and private")


def dispatch(payload: bytes, *, forwarded: bool = True) -> bytes:
    command = decode_command(payload)
    if command is None:
        return b"DENIED invalid_command\n"
    if not focus_is_safe():
        return b"DENIED unsafe_foreground\n"
    op, a, b = command
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as daemon:
            daemon.settimeout(2)
            daemon.connect(str(DAEMON_SOCKET))
            daemon.sendall(f"{op} {a} {b}\n".encode("ascii"))
            response = daemon.recv(16)
    except (OSError, TimeoutError):
        return b"DENIED device_unavailable\n"
    return b"OK\n" if response == b"OK\n" else b"DENIED device_error\n"


def run(uid: int, *, path: Path = SOCKET_PATH, one_shot: bool = False) -> None:
    if os.geteuid() != 0:
        raise PermissionError("root-only broker")
    ensure_private_dir(uid)
    if path.exists() or path.is_symlink():
        raise FileExistsError("socket already exists; refuse to unlink a live socket")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.settimeout(0.4)
    server.bind(str(path))
    os.chown(path, uid, uid)
    os.chmod(path, 0o600)
    server.listen(4)
    stop = False

    def stop_signal(_signal, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)
    print("ROOT_BRIDGE_READY uid=" + str(uid), flush=True)
    try:
        requests = 0
        while not stop:
            try:
                peer, _ = server.accept()
            except TimeoutError:
                continue
            except InterruptedError:
                continue
            with peer:
                peer.settimeout(2)
                try:
                    _pid, peer_uid, _gid = identity(peer)
                    if peer_uid != uid:
                        peer.sendall(b"DENIED wrong_uid\n")
                        continue
                    payload = peer.recv(MAX_REQUEST + 1)
                    answer = (
                        dispatch(payload) if len(payload) <= MAX_REQUEST else b"DENIED too_long\n"
                    )
                    peer.sendall(answer)
                except (OSError, TimeoutError):
                    pass
            requests += 1
            if one_shot and requests:
                break
    finally:
        server.close()
        try:
            if stat.S_ISSOCK(path.lstat().st_mode):
                path.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uid", type=int, required=True)
    parser.add_argument("--one-shot", action="store_true")
    args = parser.parse_args()
    if args.uid < 10000 or args.uid > 99999:
        parser.error("must be Android app UID")
    run(args.uid, one_shot=args.one_shot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
