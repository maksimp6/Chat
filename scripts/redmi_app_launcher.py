"""Launch the existing Redmi installation with its private state and network settings."""

import errno
import json
import os
from pathlib import Path
import socket
import subprocess


def main():
    os.umask(0o077)
    app = Path(
        os.environ.get("LOGOS_APP_DIR", str(Path.home() / "logos-local"))
    ).resolve()
    state = Path(
        os.environ.get("LOGOS_STATE_DIR", str(Path.home() / "logos-local-state"))
    ).resolve()
    settings = state / "server-network.json"
    try:
        network = json.loads(settings.read_text()) if settings.exists() else {}
    except (OSError, ValueError) as exc:
        raise SystemExit("Invalid server network settings") from exc
    host = os.environ.get("LOGOS_BIND_HOST") or network.get("bind_host", "127.0.0.1")
    if host not in ("127.0.0.1", "0.0.0.0"):
        raise SystemExit("Unsupported server bind address")
    port = int(os.environ.get("LOGOS_PORT", "8080"))
    native = list((state / "native-deps").glob("**/site-packages/cryptography"))
    if len(native) != 1:
        raise SystemExit("Native dependencies missing")
    with socket.socket() as check:
        check.settimeout(1)
        if check.connect_ex(("127.0.0.1", port)) == 0:
            raise SystemExit("Application port is already occupied")
    if host == "0.0.0.0":
        # A listener on any single interface blocks the wildcard bind; probe it
        # exactly as the server binds (SO_REUSEADDR ignores TIME_WAIT sockets).
        with socket.socket() as check:
            check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                check.bind((host, port))
            except OSError as exc:
                if exc.errno != errno.EADDRINUSE:
                    raise
                raise SystemExit("Application port is already occupied") from exc
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("GITHUB_", "ACTIONS_", "RUNNER_", "GH_"))
    }
    env.update(
        HOST=host, PORT=str(port), FLASK_DEBUG="0", PYTHONPATH=str(native[0].parent)
    )
    try:
        secret = (state / "provider-encryption.key").read_text().strip()
    except OSError as exc:
        raise SystemExit("Provider encryption key is missing") from exc
    for prefix in ("LOGOS", "ALICE"):
        env[prefix + "_MEMORY_PATH"] = str(state / "logos.memory")
        env[prefix + "_PROVIDER_CREDENTIAL_KEY"] = secret
    log_path = state / "backend.log"
    if log_path.exists() and log_path.stat().st_size > 10 * 1024 * 1024:
        log_path.replace(state / "backend.log.1")
    with log_path.open("ab", buffering=0) as log:
        process = subprocess.Popen(
            [str(app / ".venv/bin/python"), "app.py"],
            cwd=app,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    temporary = state / "backend.pid.next"
    with temporary.open("w") as stream:
        stream.write(str(process.pid))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(state / "backend.pid")
    print(json.dumps({"pid": process.pid, "bind_host": host, "port": port}))


if __name__ == "__main__":
    main()
