#!/usr/bin/env python3
"""Container entrypoint: Alice Pro, plus OpenAI Secure MCP Tunnel when configured.

Without tunnel settings this only runs ``python app.py``. With all of
OPENAI_TUNNEL_ID, OPENAI_TUNNEL_API_KEY, ALICE_MCP_BEARER_TOKEN and
ALICE_MCP_USER_ID it also runs openai/tunnel-client next to Alice. The tunnel
client polls OpenAI over outbound HTTPS and forwards MCP requests from ChatGPT
to Alice's /mcp on loopback, adding Alice's MCP bearer token itself.

Each process gets only its own secrets, no secret is put on a command line,
and if either process exits the other is stopped and the container exits
non-zero so the platform restarts it.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass

TUNNEL_BINARY = "/usr/local/bin/tunnel-client-runtime"
TUNNEL_SETTINGS = (
    "OPENAI_TUNNEL_ID",
    "OPENAI_TUNNEL_API_KEY",
    "ALICE_MCP_BEARER_TOKEN",
    "ALICE_MCP_USER_ID",
)
TUNNEL_ONLY = ("OPENAI_TUNNEL_ID", "OPENAI_TUNNEL_API_KEY")
MCP_AUTH_ENV = "ALICE_TUNNEL_MCP_AUTHORIZATION"
# Variables the tunnel client may inherit; everything else (Alice secrets) stays out.
TUNNEL_INHERIT = ("PATH", "HOME", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "NO_PROXY")
_TUNNEL_ID_RE = re.compile(r"^tunnel_[A-Za-z0-9_-]{8,128}$")
MIN_BEARER_LENGTH = 24
STOP_GRACE_SECONDS = 10


class TunnelConfigError(ValueError):
    """Tunnel settings are incomplete or unsafe; nothing may start."""


@dataclass
class Process:
    argv: list[str]
    env: dict[str, str]


@dataclass
class Plan:
    app_argv: list[str]
    app_env: dict[str, str]
    tunnel: Process | None


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def plan(
    environ: Mapping[str, str],
    tunnel_binary: str = TUNNEL_BINARY,
    app_argv: list[str] | None = None,
) -> Plan:
    app_argv = list(app_argv or [sys.executable, "app.py"])
    app_env = {k: v for k, v in environ.items() if k not in TUNNEL_ONLY}
    if not any(environ.get(name) for name in TUNNEL_ONLY):
        return Plan(app_argv, app_env, None)

    missing = [name for name in TUNNEL_SETTINGS if not environ.get(name)]
    if missing:
        raise TunnelConfigError("OpenAI tunnel needs " + ", ".join(missing))
    if not _TUNNEL_ID_RE.match(environ["OPENAI_TUNNEL_ID"]):
        raise TunnelConfigError("OPENAI_TUNNEL_ID must look like tunnel_<id>")
    if len(environ["ALICE_MCP_BEARER_TOKEN"]) < MIN_BEARER_LENGTH:
        raise TunnelConfigError(
            f"ALICE_MCP_BEARER_TOKEN must be at least {MIN_BEARER_LENGTH} characters"
        )
    if _truthy(environ.get("ALICE_MCP_ALLOW_ANONYMOUS")):
        raise TunnelConfigError("ALICE_MCP_ALLOW_ANONYMOUS must be off when the tunnel is on")
    port = environ.get("PORT", "8080")
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise TunnelConfigError("PORT must be a TCP port number")

    tunnel_env = {k: environ[k] for k in TUNNEL_INHERIT if k in environ}
    tunnel_env.update(
        {
            "CONTROL_PLANE_TUNNEL_ID": environ["OPENAI_TUNNEL_ID"],
            "CONTROL_PLANE_API_KEY": environ["OPENAI_TUNNEL_API_KEY"],
            "MCP_SERVER_URL": f"http://127.0.0.1:{port}/mcp",
            "MCP_EXTRA_HEADERS": f"Authorization: env:{MCP_AUTH_ENV}",
            MCP_AUTH_ENV: "Bearer " + environ["ALICE_MCP_BEARER_TOKEN"],
            "MCP_STARTUP_WAIT_TIMEOUT": "120s",
        }
    )
    tunnel_argv = [tunnel_binary, "run", "--health.listen-addr", "127.0.0.1:8081"]
    return Plan(app_argv, app_env, Process(tunnel_argv, tunnel_env))


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _supervise(app_argv: list[str], app_env: dict[str, str], tunnel: Process) -> int:
    """Run Alice and the tunnel client; when either exits, stop both and fail."""
    procs = [subprocess.Popen(app_argv, env=app_env)]
    try:
        procs.append(subprocess.Popen(tunnel.argv, env=tunnel.env))
    except OSError:
        _stop(procs[0])
        raise

    stopping = []

    def forward(signum, _frame):
        stopping.append(signum)

    previous = {s: signal.signal(s, forward) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        while not stopping and all(p.poll() is None for p in procs):
            time.sleep(0.2)
    finally:
        for proc in procs:
            _stop(proc)
        for s, handler in previous.items():
            signal.signal(s, handler)
    if stopping:
        return 0
    names = ("alice", "tunnel-client")
    for name, proc in zip(names, procs):
        print(f"alice-entrypoint: {name} exited with {proc.returncode}", file=sys.stderr)
    return 1


def main(
    environ: Mapping[str, str] | None = None,
    tunnel_binary: str = TUNNEL_BINARY,
    app_argv: list[str] | None = None,
    replace_process: bool = False,
) -> int:
    environ = dict(os.environ if environ is None else environ)
    try:
        chosen = plan(environ, tunnel_binary, app_argv)
    except TunnelConfigError as exc:
        print(f"alice-entrypoint: {exc}", file=sys.stderr)
        return 2

    if chosen.tunnel is None:
        if replace_process:
            # Same process as before the entrypoint existed: Alice is PID 1 and gets signals directly.
            os.execvpe(chosen.app_argv[0], chosen.app_argv, chosen.app_env)
        return subprocess.call(chosen.app_argv, env=chosen.app_env)

    return _supervise(chosen.app_argv, chosen.app_env, chosen.tunnel)


if __name__ == "__main__":
    raise SystemExit(main(replace_process=True))
