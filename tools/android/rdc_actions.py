#!/usr/bin/env python3
"""Minimal Android control via an existing, authorized ADB connection."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ADB_ENV = dict(os.environ)
ADB_ENV.setdefault("TMPDIR", str(Path.home() / ".cache" / "tmp"))
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

def run(*args: object, binary: bool = False) -> bytes | str:
    """Execute a fixed ADB/ffmpeg command without shell interpolation."""
    result = subprocess.run(["adb", *(str(a) for a in args)], env=ADB_ENV,
                            capture_output=True, check=True, timeout=20)
    return result.stdout if binary else result.stdout.decode(errors="replace").strip()

def device() -> str:
    """Require exactly one authenticated ADB device."""
    rows = [line.split()[0] for line in str(run("devices")).splitlines()[1:]
            if len(line.split()) >= 2 and line.split()[1] == "device"]
    if len(rows) != 1:
        raise RuntimeError(f"Expected one authorized device, found {len(rows)}")
    return rows[0]

def execute(args: argparse.Namespace) -> dict:
    """Perform one bounded Android operation; never claim UI verification for taps."""
    serial = device()
    prefix = ("-s", serial)
    if args.command == "status":
        raw = str(run(*prefix, "shell", "dumpsys", "window"))
        match = re.search(r"mCurrentFocus=([^\r\n]+)", raw)
        return {"device_connected": True, "focus": match.group(1) if match else None}
    if args.command == "screenshot":
        png = run(*prefix, "exec-out", "screencap", "-p", binary=True)
        if not isinstance(png, bytes) or not png.startswith(PNG_MAGIC):
            raise RuntimeError("Invalid screenshot data")
        output_dir = Path(args.output_dir).expanduser()
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        output = output_dir / "latest.jpg"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", "pipe:0", "-vf", "scale=1280:-2", "-q:v", "5",
                        "-frames:v", "1", str(output)], input=png,
                       capture_output=True, check=True, timeout=20)
        output.chmod(0o600)
        return {"path": str(output), "bytes": output.stat().st_size}
    if args.command in {"tap", "swipe"}:
        coords = (args.x, args.y) if args.command == "tap" else (args.x, args.y, args.x2, args.y2)
        if any(v < 0 or v > 10000 for v in coords):
            raise ValueError("Coordinates must be within 0..10000")
        if args.command == "swipe" and not 50 <= args.ms <= 10000:
            raise ValueError("Swipe duration must be 50..10000 ms")
        run(*prefix, "shell", "input", args.command, *coords,
            *((args.ms,) if args.command == "swipe" else ()))
    elif args.command in {"back", "home"}:
        run(*prefix, "shell", "input", "keyevent",
            "KEYCODE_BACK" if args.command == "back" else "KEYCODE_HOME")
    elif args.command == "open":
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", args.package):
            raise ValueError("Invalid Android package name")
        run(*prefix, "shell", "monkey", "-p", args.package, "1")
    return {"command": args.command, "submitted": True, "verified": False}

def main() -> int:
    """Parse a single safe action and emit a JSON response."""
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["status", "screenshot", "tap", "swipe", "back", "home", "open"])
    for name in ("x", "y", "x2", "y2"):
        parser.add_argument(f"--{name}", type=int, default=0)
    parser.add_argument("--ms", type=int, default=300)
    parser.add_argument("--package", default="")
    parser.add_argument("--output-dir", default="~/.alice-rdc/screenshots")
    args = parser.parse_args()
    try:
        print(json.dumps(execute(args), ensure_ascii=False))
        return 0
    except (ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": "Android action failed"}), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
