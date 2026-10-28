"""What this process serves: the UI fingerprint, the code revision and the boot time."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _static_asset_version(root=None):
    """Fingerprint the served UI bytes, independently of file timestamps or overrides."""
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    paths = sorted(
        path
        for path in (root / "static").rglob("*")
        if path.is_file()
        and path.suffix in {".js", ".css", ".svg", ".png", ".woff", ".woff2"}
    )
    paths.extend(sorted((root / "templates").rglob("*.html")))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _runtime_revision(root=None):
    configured = str(os.environ.get("LOGOS_APP_REVISION") or "").strip()
    if re.fullmatch(r"[0-9a-f]{40}", configured):
        return configured
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(
                    Path(root)
                    if root is not None
                    else Path(__file__).resolve().parents[1]
                ),
                "rev-parse",
                "--verify",
                "HEAD",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        value = result.stdout.strip()
        if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value):
            return value
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def boot_version(root: str | Path) -> dict[str, Any]:
    """The version of this process: code revision, UI fingerprint, process ID, start time."""
    static_asset_version = _static_asset_version(root)
    return {
        "runtime_revision": _runtime_revision(root),
        "ui_version": static_asset_version,
        "process_id": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
