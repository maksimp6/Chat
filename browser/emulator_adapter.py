"""Browser adapter backed by the lightweight Node.js browser emulator.

The emulator parses HTML into the same DOM shim used by frontend tests. It does
not execute remote page scripts and holds no real browser profile, cookies or
credentials, so it can serve local and cloud browsing without a Chrome container.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Mapping, TextIO

from .capabilities import BrowserAction

SERVER_SCRIPT = Path(__file__).resolve().parent / "emulator" / "server.js"


class EmulatorBrowserAdapter:
    """Keeps one emulator session (one page) per adapter instance."""

    def __init__(self, node: str | None = None, timeout: float = 30.0):
        self._node = node or shutil.which("node") or "node"
        self._timeout = timeout
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def execute(self, action: BrowserAction) -> Mapping[str, Any]:
        request = json.dumps(
            {"action": action.action, "target": action.target, "value": action.value}
        )
        with self._lock:
            process = self._ensure_process()
            try:
                stdin = process.stdin
                stdout = process.stdout
                if stdin is None or stdout is None:
                    line = ""
                else:
                    stdin.write(request + "\n")
                    stdin.flush()
                    line = stdout.readline()
            except BrokenPipeError:
                line = ""
        if not line:
            self.close()
            raise RuntimeError("browser emulator exited unexpectedly")
        result: dict[str, Any] = json.loads(line)
        return result

    def close(self) -> None:
        with self._lock:
            if self._process is not None:
                self._process.kill()
                self._process.wait(timeout=self._timeout)
                self._process = None

    def _ensure_process(self) -> subprocess.Popen[str]:
        if self._process is None or self._process.poll() is not None:
            self._process = subprocess.Popen(
                [self._node, str(SERVER_SCRIPT)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        return self._process


__all__ = ["EmulatorBrowserAdapter", "SERVER_SCRIPT"]
