#!/usr/bin/env python3
"""Runner-only native RootAgent CLI; reuse the existing root mouse daemon.

Mouse coordinates are relative deltas, not absolute screen coordinates.
An ACK means submitted, never that Android visibly applied the action.
"""

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import select
import shlex
import socket
import stat
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

MOUSE = "/data/adb/alice-mouse/control.sock"
DAEMON_SHA256 = "9d25ff623eb29fc9b9f72e9e2126d52144db2181508229aac91eb4cd0de38516"
PNG_LIMIT = 9 * 1024 * 1024
PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")
RUNNER_ENV_KEYS = (
    "CI",
    "GITHUB_ACTIONS",
    "GITHUB_RUN_ID",
    "GITHUB_SHA",
    "RUNNER_NAME",
    "RUNNER_TEMP",
)


class AgentError(Exception):
    pass


def runner_guard():
    values = {key: os.environ.get(key, "") for key in RUNNER_ENV_KEYS}
    if (
        values["CI"] != "true"
        or values["GITHUB_ACTIONS"] != "true"
        or not all(values.values())
    ):
        raise AgentError("actual_github_runner_required")
    return values


def bounded_command(argv, limit=2 * 1024 * 1024, timeout=8):
    """Read bounded stdout, without exposing command stderr or using a shell."""
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    deadline, output = time.monotonic() + timeout, bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if (
                remaining <= 0
                or not select.select([process.stdout], [], [], remaining)[0]
            ):
                raise AgentError("android_command_timeout")
            chunk = os.read(
                process.stdout.fileno(), min(65536, limit + 1 - len(output))
            )
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > limit:
                raise AgentError("android_command_output_limit")
        if process.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
            raise AgentError("android_command_failed")
        return bytes(output)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        process.stdout.close()


def device_guard():
    if os.geteuid() != 0:
        raise AgentError("root_worker_required")
    values = [
        bounded_command(["/system/bin/getprop", key], 128).decode().strip()
        for key in ("ro.product.model", "ro.product.device")
    ]
    if values != ["Redmi 9", "galahad"]:
        raise AgentError("wrong_device")


def screen_state():
    policy = bounded_command(["/system/bin/dumpsys", "window", "policy"]).decode(
        "utf-8", "replace"
    )
    flags = []
    for key in ("showing", "mInputRestricted", "mKeyguardOccluded"):
        values = re.findall(
            r"(?<![A-Za-z0-9_])" + key + r"\s*=\s*(true|false)\b", policy
        )
        flags.append(bool(values) and all(value == "false" for value in values))
    window = bounded_command(["/system/bin/dumpsys", "window"]).decode(
        "utf-8", "replace"
    )
    display_source = window
    display_ids = set(re.findall(r"\bmDisplayId\s*=\s*(\d+)\b", window))
    if len(display_ids) > 1:
        blocks = re.split(r"(?m)(?=^\s*Display:\s*mDisplayId=)", window)
        primary = [
            block
            for block in blocks
            if re.match(r"\s*Display:\s*mDisplayId=0\b", block)
        ]
        display_source = primary[0] if len(primary) == 1 else ""
    elif display_ids and "0" not in display_ids:
        display_source = ""
    display_flags = []
    for key in ("mScreenOnEarly", "mScreenOnFully"):
        values = re.findall(
            r"(?<![A-Za-z0-9_])" + key + r"\s*=\s*(true|false)\b", display_source
        )
        display_flags.append(bool(values) and all(value == "true" for value in values))
    windows = re.findall(r"(?m)^\s*mCurrentFocus=([^\r\n]*)", window)
    focus_window = windows[0].strip() if len(windows) == 1 else None
    focus = re.search(r"\b(" + PACKAGE.pattern + r")/", focus_window or "")
    return {
        "unlocked": all(flags),
        "display_on": all(display_flags),
        "package": focus.group(1) if focus else None,
        "focus_window": focus_window if focus else None,
    }


def require_screen(expected=None):
    state = screen_state()
    if state.get("display_on") is not True:
        raise AgentError("screen_not_awake")
    if not state["unlocked"] or not state["package"] or not state.get("focus_window"):
        raise AgentError("screen_locked_or_unknown")
    if expected is not None and state["package"] != expected:
        raise AgentError("foreground_package_mismatch")
    return state


def validate_png(png):
    if not 8 < len(png) <= PNG_LIMIT or png[:8] != b"\x89PNG\r\n\x1a\n":
        raise AgentError("invalid_png")
    offset, header, compressed, ended, palette, finished = (
        8,
        None,
        [],
        False,
        False,
        False,
    )
    while offset < len(png):
        if offset + 12 > len(png):
            raise AgentError("truncated_png")
        size, kind = struct.unpack(">I4s", png[offset : offset + 8])
        end = offset + size + 12
        if end > len(png) or not re.fullmatch(b"[A-Za-z]{4}", kind) or kind[2] & 32:
            raise AgentError("invalid_png_chunk")
        data = png[offset + 8 : end - 4]
        if (
            binascii.crc32(kind + data) & 0xFFFFFFFF
            != struct.unpack(">I", png[end - 4 : end])[0]
        ):
            raise AgentError("png_crc_mismatch")
        if header is None and kind != b"IHDR":
            raise AgentError("missing_png_header")
        if kind == b"IHDR":
            if header is not None or offset != 8 or size != 13:
                raise AgentError("invalid_png_header")
            width, height, depth, color, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", data)
            )
            if not (
                0 < width <= 8192
                and 0 < height <= 8192
                and width * height <= 16 * 1024 * 1024
            ):
                raise AgentError("png_dimensions_limit")
            if (
                depth != 8
                or color not in (2, 6)
                or (compression, filtering, interlace) != (0, 0, 0)
            ):
                raise AgentError("unsupported_png_profile")
            header = (width, height, 3 if color == 2 else 4)
        elif kind == b"IDAT":
            if ended:
                raise AgentError("noncontiguous_png_data")
            compressed.append(data)
        elif kind == b"IEND":
            if size or not compressed or end != len(png):
                raise AgentError("invalid_png_end")
            finished = True
        elif kind == b"PLTE":
            if palette or compressed or not 0 < size <= 768 or size % 3:
                raise AgentError("invalid_png_palette")
            palette = True
        elif not kind[0] & 32:
            raise AgentError("unknown_critical_png_chunk")
        if compressed and kind != b"IDAT":
            ended = True
        offset = end
    if not finished or header is None:
        raise AgentError("incomplete_png")
    width, height, channels = header
    stride = width * channels + 1
    expected = stride * height
    decoder = zlib.decompressobj()
    decoded = decoder.decompress(b"".join(compressed), expected + 1)
    if (
        len(decoded) != expected
        or not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
        or any(decoded[i] > 4 for i in range(0, expected, stride))
    ):
        raise AgentError("invalid_png_pixels")
    return {
        "width": width,
        "height": height,
        "bytes": len(png),
        "sha256": hashlib.sha256(png).hexdigest(),
    }


def mouse_metadata():
    directory, endpoint = os.lstat(str(Path(MOUSE).parent)), os.lstat(MOUSE)
    if (
        not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != 0
        or stat.S_IMODE(directory.st_mode) != 0o700
        or not stat.S_ISSOCK(endpoint.st_mode)
        or endpoint.st_uid != 0
        or stat.S_IMODE(endpoint.st_mode) != 0o600
    ):
        raise AgentError("unsafe_mouse_socket_metadata")


def mouse_connection():
    mouse_metadata()
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(2)
    try:
        connection.connect(MOUSE)
        pid, uid, _ = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        if uid != 0 or pid <= 0:
            raise AgentError("mouse_peer_not_root")
        with open(f"/proc/{pid}/exe", "rb") as executable:
            if not stat.S_ISREG(os.fstat(executable.fileno()).st_mode):
                raise AgentError("invalid_mouse_executable")
            digest, count = hashlib.sha256(), 0
            for chunk in iter(lambda: executable.read(65536), b""):
                count += len(chunk)
                if count > 32 * 1024 * 1024:
                    raise AgentError("mouse_executable_limit")
                digest.update(chunk)
        if digest.hexdigest() != DAEMON_SHA256:
            raise AgentError("mouse_executable_mismatch")
        return connection
    except Exception:
        connection.close()
        raise


def submit_mouse(connection, command, dx, dy):
    """One internal protocol write; an uncertain delivery must never be retried."""
    try:
        connection.sendall(f"{command} {dx} {dy}\n".encode("ascii"))
        reply = bytearray()
        while len(reply) < 16 and not reply.endswith(b"\n"):
            chunk = connection.recv(16 - len(reply))
            if not chunk:
                break
            reply.extend(chunk)
    except OSError:
        raise AgentError("mouse_delivery_unknown_do_not_retry") from None
    if reply != b"OK\n":
        raise AgentError("mouse_ack_unconfirmed_do_not_retry")


def worker(request):
    command = request.get("command")
    keys = (
        {"command", "dx", "dy", "expected_package"}
        if command in ("move", "click")
        else {"command"}
    )
    if command == "capture" and "expected_package" in request:
        keys.add("expected_package")
    if set(request) != keys or command not in (
        "status",
        "probe",
        "capture",
        "move",
        "click",
        "wake",
    ):
        raise AgentError("invalid_worker_request")
    device_guard()
    if command == "status":
        state = screen_state()
        try:
            mouse_metadata()
            mouse_ready = True
        except (OSError, AgentError):
            mouse_ready = False
        return {
            "model": "Redmi 9",
            "device": "galahad",
            "root_uid": 0,
            "keyguard_locked_or_unknown": not state["unlocked"],
            "display_on": state["display_on"],
            "focus_present": bool(state["package"]),
            "mouse_metadata_valid": mouse_ready,
        }
    if command == "probe":
        with mouse_connection():
            return {
                "root_uid": 0,
                "mouse_peer_verified": True,
                "daemon_sha256": DAEMON_SHA256,
                "input_operations": "NOT_RUN",
            }
    if command == "wake":

        def already_awake():
            return {
                "command": "wake",
                "already_awake": True,
                "input_operations": "NOT_RUN",
                "verified": False,
            }

        if screen_state().get("display_on") is True:
            return already_awake()
        with mouse_connection() as connection:
            if screen_state().get("display_on") is True:
                return already_awake()
            submit_mouse(connection, "move", 1, 1)
        return {
            "command": "wake",
            "submitted": True,
            "verified": False,
            "coordinates": "relative",
        }
    if command == "capture":
        expected = request.get("expected_package")
        if "expected_package" in request and (
            not isinstance(expected, str) or PACKAGE.fullmatch(expected) is None
        ):
            raise AgentError("invalid_capture_request")
        before = require_screen(expected)
        png = bounded_command(["/system/bin/screencap", "-p"], PNG_LIMIT, 15)
        metadata = validate_png(png)
        after = require_screen(before["package"])
        if after["focus_window"] != before["focus_window"]:
            raise AgentError("foreground_window_changed")
        return {**metadata, "png_base64": base64.b64encode(png).decode("ascii")}
    if (
        any(
            type(request[key]) is not int or not -500 <= request[key] <= 500
            for key in ("dx", "dy")
        )
        or not isinstance(request["expected_package"], str)
        or PACKAGE.fullmatch(request["expected_package"]) is None
    ):
        raise AgentError("invalid_mouse_request")
    with mouse_connection() as connection:
        require_screen(request["expected_package"])
        submit_mouse(connection, command, request["dx"], request["dy"])
    return {
        "command": command,
        "submitted": True,
        "verified": False,
        "coordinates": "relative",
    }


def save_capture(path, png):
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(png)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        os.unlink(path)
        raise


def main():
    runner_values = runner_guard()
    if sys.argv[1:] == ["_worker"]:
        raw = sys.stdin.buffer.read(2049)
        if len(raw) > 2048:
            raise AgentError("worker_request_limit")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise AgentError("invalid_worker_request")
        print(json.dumps({"ok": True, "result": worker(request)}))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "probe", "capture", "move", "click", "wake"):
        sub = commands.add_parser(command)
        if command == "capture":
            sub.add_argument("--output", required=True)
            sub.add_argument("--expected-package")
        if command in ("move", "click"):
            sub.add_argument("dx", type=int)
            sub.add_argument("dy", type=int)
            sub.add_argument("--expected-package", required=True)
    args = vars(parser.parse_args())
    output = args.pop("output", None)
    if args["command"] == "capture" and args.get("expected_package") is None:
        args.pop("expected_package", None)
    if output is not None and os.geteuid() == 0:
        raise AgentError("capture_requires_unprivileged_client")
    script = str(Path(__file__).resolve())
    python = str(Path(sys.executable).resolve())
    assignments = [
        key + "=" + shlex.quote(value) for key, value in runner_values.items()
    ]
    invocation = " ".join(
        assignments
        + [shlex.quote(value) for value in (python, "-I", "-S", script, "_worker")]
    )
    names = ("PATH", "HOME", "TMPDIR", "PREFIX", "LANG")
    environment = {key: os.environ[key] for key in names if key in os.environ}
    environment.update(runner_values)
    result = subprocess.run(
        ["su", "-c", invocation],
        input=json.dumps(args).encode(),
        capture_output=True,
        env=environment,
        timeout=75,
    )
    if len(result.stdout) > 13 * 1024 * 1024:
        raise AgentError("worker_reply_limit")
    response = json.loads(result.stdout)
    if result.returncode or response.get("ok") is not True:
        raise AgentError(response.get("error", "root_worker_failed"))
    metadata = response["result"]
    if output is not None:
        png = base64.b64decode(metadata.pop("png_base64"), validate=True)
        metadata = validate_png(png)
        save_capture(output, png)
        metadata["output"] = str(Path(output).absolute())
    print(json.dumps(metadata, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        message = str(error) if isinstance(error, AgentError) else type(error).__name__
        print(json.dumps({"ok": False, "error": message, "verified": False}))
        raise SystemExit(1)
