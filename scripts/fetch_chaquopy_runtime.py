"""Fetch the pinned Chaquopy Python runtime for the Android build.

The runtime is the Maven Central artifact com.chaquo.python:target, version and
checksums pinned in android/runtime/chaquopy-target-<version>.sha256. Every file is
verified against its pinned SHA-256 before it is returned; a mismatch aborts without
writing the file, so an unverified runtime is never used.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "android" / "runtime"
MAVEN_BASE = "https://repo1.maven.org/maven2/com/chaquo/python/target"
VERSION = "3.14.8-0"
PIN_FILE = RUNTIME_DIR / f"chaquopy-target-{VERSION}.sha256"


class RuntimeVerificationError(RuntimeError):
    """Raised when a downloaded runtime file does not match its pinned digest."""


def load_pins(path: Path = PIN_FILE) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        digest, name = line.split(maxsplit=1)
        if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
            raise RuntimeVerificationError(f"malformed SHA-256 for {name}")
        pins[name] = digest
    if not pins:
        raise RuntimeVerificationError(f"no pinned files in {path}")
    return pins


def verify_file(path: Path, expected_sha256: str) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected_sha256:
        raise RuntimeVerificationError(
            f"{path.name}: SHA-256 {actual} does not match pinned {expected_sha256}"
        )


def fetch(name: str, digest: str, out_dir: Path, opener=None) -> Path:
    url = f"{MAVEN_BASE}/{VERSION}/{name}"
    target = out_dir / name
    open_url = opener or urllib.request.urlopen
    with open_url(url, timeout=300) as response:
        data = response.read()
    partial = target.with_suffix(target.suffix + ".partial")
    partial.write_bytes(data)
    try:
        verify_file(partial, digest)
    except RuntimeVerificationError:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(target)
    return target


def verify_local(out_dir: Path) -> list[str]:
    """Verify runtime files already present in out_dir (for example from Git LFS); never downloads."""
    verified = []
    for name, digest in load_pins().items():
        path = out_dir / name
        if not path.is_file():
            raise RuntimeVerificationError(f"{name}: missing from {out_dir}")
        verify_file(path, digest)
        verified.append(name)
    return verified


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="verify files already in out_dir against the pins instead of downloading",
    )
    args = parser.parse_args(argv)
    if args.verify_only:
        for name in verify_local(args.out_dir):
            print(f"verified {name}")
        return 0
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, digest in load_pins().items():
        path = fetch(name, digest, args.out_dir)
        print(f"verified {path.name}")
    return 0


def run(argv: list[str]) -> int:
    """Run main and report a verification failure as exit status 1, never silently."""
    try:
        return main(argv)
    except RuntimeVerificationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
