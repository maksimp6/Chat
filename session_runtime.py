"""Reusable AI sessions and a lightweight, lazy execution runtime.

The runtime intentionally stays small: it is a managed subprocess environment,
not a security container. Strong isolation should be supplied by a container/VM
backend in deployments that execute untrusted code.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None


@dataclass
class VirtualServerConfig:
    runtime: str = "python"
    cwd: str = "."
    env: Dict[str, str] = field(default_factory=dict)
    memory_mb: int = 256
    cpu_seconds: int = 30
    command_timeout_seconds: int = 30
    idle_timeout_seconds: int = 300
    max_output_bytes: int = 1_000_000


class VirtualLowConsumptionServer:
    """Lazy-started session runtime with explicit lifecycle and resource limits."""

    STATES = {"stopped", "starting", "running", "idle", "suspended", "stopping", "error"}

    def __init__(self, session_id: str, config: Optional[VirtualServerConfig] = None):
        self.session_id = session_id
        self.config = config or VirtualServerConfig()
        self.state = "stopped"
        self.runtime_id = f"runtime_{uuid.uuid4().hex}"
        self._last_used = 0.0
        self._lock = threading.RLock()
        self._process: Optional[subprocess.Popen[str]] = None

    @property
    def last_used(self) -> float:
        return self._last_used

    def _touch(self) -> None:
        self._last_used = time.time()
        if self.state == "idle":
            self.state = "running"

    def start(self) -> str:
        with self._lock:
            if self.state in {"running", "idle"}:
                self._touch()
                return self.runtime_id
            self.state = "starting"
            Path(self.config.cwd).mkdir(parents=True, exist_ok=True)
            self.state = "running"
            self._touch()
            return self.runtime_id

    def execute(self, command: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")

        with self._lock:
            self.start()
            env = os.environ.copy()
            env.update({str(k): str(v) for k, v in self.config.env.items()})
            timeout = timeout or self.config.command_timeout_seconds

            def limits() -> None:
                if resource is None:
                    return
                resource.setrlimit(resource.RLIMIT_CPU, (self.config.cpu_seconds, self.config.cpu_seconds))
                memory = self.config.memory_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (memory, memory))

            started = time.perf_counter()
            try:
                completed = subprocess.run(
                    command,
                    shell=True,
                    cwd=self.config.cwd,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    preexec_fn=limits if resource is not None and os.name == "posix" else None,
                )
                stdout = completed.stdout[: self.config.max_output_bytes]
                stderr = completed.stderr[: self.config.max_output_bytes]
                self._touch()
                return {
                    "runtime_id": self.runtime_id,
                    "state": self.state,
                    "exit_code": completed.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            except subprocess.TimeoutExpired as exc:
                self._touch()
                return {
                    "runtime_id": self.runtime_id,
                    "state": self.state,
                    "exit_code": None,
                    "stdout": (exc.stdout or "")[: self.config.max_output_bytes],
                    "stderr": (exc.stderr or "")[: self.config.max_output_bytes],
                    "timed_out": True,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            except Exception:
                self.state = "error"
                raise

    def suspend(self) -> None:
        with self._lock:
            if self.state in {"running", "idle"}:
                self.state = "suspended"

    def stop(self) -> None:
        with self._lock:
            self.state = "stopping"
            if self._process and self._process.poll() is None:
                self._process.terminate()
            self._process = None
            self.state = "stopped"

    def mark_idle(self) -> None:
        with self._lock:
            if self.state == "running":
                self.state = "idle"

    def reap_if_idle(self, now: Optional[float] = None) -> bool:
        now = now or time.time()
        with self._lock:
            if self.state in {"running", "idle"} and now - self._last_used >= self.config.idle_timeout_seconds:
                self.suspend()
                return True
            return False

    def snapshot(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "runtime_id": self.runtime_id,
            "state": self.state,
            "last_used": self._last_used,
            "config": asdict(self.config),
        }


@dataclass
class ReadyMadeSession:
    """Persistent configuration for a reusable AI environment."""

    id: str
    name: str
    type: str = "assistant"
    model: str = ""
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)
    environment: Dict[str, Any] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    state: Dict[str, Any] = field(default_factory=dict)
    virtual_server: VirtualServerConfig = field(default_factory=VirtualServerConfig)
    template: bool = False

    def create_runtime(self) -> VirtualLowConsumptionServer:
        return VirtualLowConsumptionServer(self.id, self.virtual_server)

    def clone(self, name: str, session_id: Optional[str] = None) -> "ReadyMadeSession":
        data = asdict(self)
        data["id"] = session_id or f"sess_{uuid.uuid4().hex}"
        data["name"] = name
        data["template"] = False
        data["state"] = json.loads(json.dumps(self.state))
        data["virtual_server"] = VirtualServerConfig(**data["virtual_server"])
        return ReadyMadeSession(**data)


DEFAULT_SESSIONS = [
    ReadyMadeSession("developer", "Developer", "developer", tools=["filesystem", "terminal", "git"], template=True),
    ReadyMadeSession("researcher", "Researcher", "researcher", tools=["web", "mcp"], template=True),
    ReadyMadeSession("assistant", "Assistant", "assistant", template=True),
    ReadyMadeSession("terminal", "Terminal", "terminal", tools=["terminal", "filesystem"], template=True),
    ReadyMadeSession("agent", "Agent", "agent", tools=["filesystem", "terminal", "mcp"], template=True),
]
