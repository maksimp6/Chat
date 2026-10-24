"""Exercise patched Live Server on a disposable localhost port.

Never outputs bearer tokens, Android serials, focus details or UI text.
The only real-device action is the user-requested, bounded WAKEUP via Live Server.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = Path.home() / ".alice-rdc"
TOKEN = (BASE / "live-token").read_text().strip()
port_sock = socket.socket()
port_sock.bind(("127.0.0.1", 0))
port = port_sock.getsockname()[1]
port_sock.close()

source = (
    "import sys; sys.path.insert(0, " + repr(str(BASE)) + "); "
    "from android_live_server import Handler; "
    "from http.server import ThreadingHTTPServer; "
    f"ThreadingHTTPServer(('127.0.0.1',{port}), Handler).serve_forever()"
)
log = open(BASE / "probe-live-server.log", "w")
os.chmod(BASE / "probe-live-server.log", 0o600)
process = subprocess.Popen([sys.executable, "-c", source], stdin=subprocess.DEVNULL,
                           stdout=log, stderr=log, start_new_session=True)
log.close()

def fetch(path, method="GET", payload=None, authenticated=True, timeout=18):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if authenticated:
        headers["Authorization"] = "Bearer " + TOKEN
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}" + path,
        data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            code = response.status
            raw = response.read(4096)
    except urllib.error.HTTPError as error:
        code = error.code
        raw = error.read(4096)
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        data = {}
    return code, data

results = {}
try:
    for _ in range(30):
        if process.poll() is not None:
            raise RuntimeError("candidate exited before health")
        try:
            code, body = fetch("/health", timeout=2)
            if code == 200:
                break
        except OSError:
            time.sleep(.1)
    else:
        raise RuntimeError("candidate not listening")
    results["health"] = code
    results["no_token"] = fetch("/health", authenticated=False)[0]
    status, body = fetch("/status")
    results["status"] = status
    results["bound_target"] = body.get("target") == "Redmi_9"
    results["focus_available"] = isinstance(body.get("focus"), str) and bool(body.get("focus"))
    results["invalid_action"] = fetch("/action","POST",{"command":"arbitrary_shell"})[0]
    results["no_token_action"] = fetch("/action","POST",{"command":"wake"},authenticated=False)[0]
    # First candidate Live Server-only action; deliberately does not unlock PIN.
    wake_status, wake_body = fetch("/action","POST",{"command":"wake"},timeout=18)
    results["wake"] = wake_status
    results["wake_submitted"] = wake_body.get("submitted") is True
    sem_status, sem_body = fetch("/screen/semantics",timeout=25)
    results["semantics"] = sem_status
    if sem_status == 200:
        results["semantic_nodes"] = sem_body.get("count")
    else:
        results["semantics_reason"] = sem_body.get("error")
    assert results["health"] == 200
    assert results["no_token"] == 401
    assert results["status"] == 200
    assert results["bound_target"]
    assert results["invalid_action"] == 400
    assert results["no_token_action"] == 401
    assert results["wake"] == 200
finally:
    process.terminate()
    try:
        process.wait(timeout=4)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)
print(json.dumps(results, ensure_ascii=False))
