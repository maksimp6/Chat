"""Phone-side agent for the self-hosted fallback control server (#1178).

Runs on the Redmi 9 (Termux Python, standard library only). It connects
*outbound* to the owner's HTTPS ingress in front of the fallback control server,
long-polls for commands, runs only registered handlers and acknowledges each
command. No inbound port, vendor tunnel or RDC is involved.

Execution is at most once per command ID: before a handler runs, the agent
fsyncs a "started" record to its own journal, and the result afterwards. If the
server redelivers a command (lost ACK, server restart), the agent replays the
recorded result; a command that started but never recorded a result is
acknowledged as failed instead of being run again.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import random
import re
import ssl
import stat
import sys
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 60.0
POLL_TIMEOUT_MAX_MS = 30000
RESPONSE_MAX_BYTES = 4 * 1024 * 1024
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

Handler = Callable[[dict], None]


class ConfigError(ValueError):
    """Agent configuration is unsafe or invalid; the agent must not start."""


class AuthError(RuntimeError):
    """The server rejected the agent token (401/403)."""


class TransportError(RuntimeError):
    """Network failure or unexpected server response; safe to retry later."""


@dataclass
class AgentConfig:
    base_url: str
    token_file: Path
    state_dir: Path
    poll_timeout_ms: int = 25000
    ca_file: Path | None = None


def _check_url(url: str) -> urllib.parse.SplitResult:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ConfigError("control URL must be https://host[:port][/path]")
    if parts.scheme == "http" and parts.hostname not in _LOOPBACK_HOSTS:
        raise ConfigError("plain http is only allowed for a loopback control URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ConfigError("control URL must not carry credentials, query or fragment")
    return parts


def _read_token(path: Path) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ConfigError(f"cannot open token file {path}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ConfigError(f"token file {path} must be a private regular file (0600)")
        raw = os.read(fd, 4097)
    finally:
        os.close(fd)
    token = raw.decode("ascii", "replace").strip()
    if len(token) < 16 or len(raw) > 4096 or not all(33 <= ord(ch) <= 126 for ch in token):
        raise ConfigError(f"token file {path} must hold one printable token of 16..4096 bytes")
    return token


class ExecutionJournal:
    """Append-only local record of started/finished command IDs."""

    def __init__(self, state_dir: Path):
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = os.lstat(state_dir)
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
            raise ConfigError("agent state directory must be a private (0700) directory")
        self.path = state_dir / "executed.log"
        self._entries: dict[str, str] = {}
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        data = self.path.read_bytes()
        good = 0
        for line in data.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                break  # torn final line from a crash mid-append
            fields = line.decode("ascii", "strict").split()
            if len(fields) == 2 and fields[0] == "S":
                self._entries.setdefault(fields[1], "started")
            elif len(fields) == 3 and fields[0] == "F" and fields[2] in ("done", "failed"):
                self._entries[fields[1]] = fields[2]
            else:
                raise ConfigError("agent journal is corrupt")
            good += len(line)
        if good != len(data):
            with open(self.path, "r+b") as handle:
                handle.truncate(good)
                os.fsync(handle.fileno())

    def _append(self, line: str) -> None:
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, line.encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)

    def lookup(self, command_id: str) -> str | None:
        return self._entries.get(command_id)

    def started(self, command_id: str) -> None:
        self._append(f"S {command_id}\n")
        self._entries[command_id] = "started"

    def finished(self, command_id: str, status: str) -> None:
        self._append(f"F {command_id} {status}\n")
        self._entries[command_id] = status


class FallbackAgent:
    def __init__(self, config: AgentConfig, handlers: dict[str, Handler] | None = None):
        _check_url(config.base_url)
        _read_token(config.token_file)
        if not 0 <= config.poll_timeout_ms <= POLL_TIMEOUT_MAX_MS:
            raise ConfigError("poll timeout must be 0..30000 ms")
        self.config = config
        self.handlers = dict(handlers if handlers is not None else default_handlers())
        self.journal = ExecutionJournal(Path(config.state_dir))

    # -- transport -------------------------------------------------------

    def _request(self, method: str, path: str, body: dict | None = None):
        parts = _check_url(self.config.base_url)
        token = _read_token(self.config.token_file)  # re-read: allows rotation
        timeout = self.config.poll_timeout_ms / 1000 + 10
        if parts.scheme == "https":
            context = ssl.create_default_context(
                cafile=str(self.config.ca_file) if self.config.ca_file else None
            )
            conn = http.client.HTTPSConnection(
                parts.hostname, parts.port, timeout=timeout, context=context
            )
        else:
            conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
        headers = {"Authorization": "Bearer " + token}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, parts.path.rstrip("/") + path, body=data, headers=headers)
            response = conn.getresponse()
            payload = response.read(RESPONSE_MAX_BYTES + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise TransportError(f"{method} {path}: {type(exc).__name__}") from exc
        finally:
            conn.close()
        if len(payload) > RESPONSE_MAX_BYTES:
            raise TransportError("response too large")
        if response.status in (401, 403):
            raise AuthError(f"{method} {path}: HTTP {response.status}")
        return response.status, payload

    def poll(self) -> list[dict]:
        status, payload = self._request(
            "GET", f"/v1/commands/poll?timeout_ms={self.config.poll_timeout_ms}"
        )
        if status == 204:
            return []
        if status != 200:
            raise TransportError(f"poll: HTTP {status}")
        try:
            commands = json.loads(payload)["commands"]
        except (ValueError, KeyError, TypeError) as exc:
            raise TransportError("poll: malformed response") from exc
        if not isinstance(commands, list):
            raise TransportError("poll: malformed response")
        return commands

    def ack(self, command_id: str, status: str) -> None:
        code, _ = self._request("POST", f"/v1/commands/{command_id}/ack", {"status": status})
        if code not in (200, 204, 404, 409):  # 404/409: nothing left to acknowledge
            raise TransportError(f"ack: HTTP {code}")

    # -- execution -------------------------------------------------------

    def _execute(self, command: dict) -> tuple[str, str] | None:
        command_id = command.get("id") if isinstance(command, dict) else None
        if not isinstance(command_id, str) or not _ID_RE.match(command_id):
            return None
        prior = self.journal.lookup(command_id)
        if prior in ("done", "failed"):
            return command_id, prior
        if prior == "started":
            # Outcome unknown after a crash: never run twice.
            self.journal.finished(command_id, "failed")
            return command_id, "failed"
        handler = self.handlers.get(command.get("kind"))
        payload = command.get("payload")
        if handler is None or not isinstance(payload, dict):
            self.journal.finished(command_id, "failed")
            return command_id, "failed"
        self.journal.started(command_id)
        try:
            handler(payload)
            status = "done"
        except Exception:  # noqa: BLE001 - handler failures are reported, never crash the agent
            status = "failed"
        self.journal.finished(command_id, status)
        return command_id, status

    def run_once(self) -> int:
        commands = self.poll()
        for command in commands:
            outcome = self._execute(command)
            if outcome is not None:
                self.ack(*outcome)
        return len(commands)

    def run_forever(
        self,
        stop: threading.Event,
        sleep: Callable[[float], bool] | None = None,
    ) -> None:
        wait = sleep or stop.wait
        failures = 0
        while not stop.is_set():
            try:
                self.run_once()
                failures = 0
                continue
            except (TransportError, AuthError) as exc:
                failures += 1
                print(f"logos-fallback-agent: {exc}", file=sys.stderr)
            delay = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * 2 ** min(failures - 1, 16))
            if wait(delay * random.uniform(0.5, 1.0)):
                return


def default_handlers() -> dict[str, Handler]:
    """Only side-effect-free kinds. Device actions are added with RootAgentService (#650)."""
    return {"noop": lambda payload: None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--poll-timeout-ms", type=int, default=25000)
    args = parser.parse_args(argv)
    try:
        agent = FallbackAgent(
            AgentConfig(
                args.url, args.token_file, args.state_dir, args.poll_timeout_ms, args.ca_file
            )
        )
    except ConfigError as exc:
        print(f"logos-fallback-agent: {exc}", file=sys.stderr)
        return 2
    stop = threading.Event()
    try:
        agent.run_forever(stop)
    except KeyboardInterrupt:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
