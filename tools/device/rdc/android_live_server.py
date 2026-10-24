#!/data/data/com.termux/files/usr/bin/python
"""Loopback-only authenticated Android live server: screen and OCR/actions."""
import json
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import threading
from android_delta import DeltaDetector
from android_accessibility import read_accessibility
from android_public_tree import public_tree
from android_screen_dom import build as build_ocr_dom
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = Path.home() / ".alice-rdc"
TOKEN_PATH = BASE / "live-token"
ACTIONS = Path(os.environ.get("ALICE_LIVE_ACTIONS", str(BASE / "android_actions.py")))
OCR = BASE / "android_find_mark.py"
DOM = BASE / "android_screen_dom.py"
IMAGE = BASE / "screenshots" / "latest.jpg"
MAX_BODY = 2048
ALLOWED = {"tap", "swipe", "back", "home", "open", "screenshot", "wake", "screen.unlock"}
TRUSTED_TARGET = "Redmi_9"

def ensure_token():
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not TOKEN_PATH.exists():
        TOKEN_PATH.write_text(secrets.token_urlsafe(32))
        TOKEN_PATH.chmod(0o600)
    return TOKEN_PATH.read_text().strip()

TOKEN = ensure_token()
FRAME_LOCK = threading.RLock()
DETECTOR = DeltaDetector()
CAPTURE_STATE = {"revision": 0, "frame_id": None}

def execute(argv):
    result = subprocess.run([sys.executable, *map(str, argv)], capture_output=True,
                            text=True, timeout=25)
    if result.returncode:
        raise RuntimeError("Operation failed")
    return json.loads(result.stdout)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return  # never log URLs, headers, or credentials

    def send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        supplied = self.headers.get("Authorization", "")
        if not secrets.compare_digest(supplied, "Bearer " + TOKEN):
            self.send(401, {"error": "unauthorized"})
            return False
        return True

    def bound_status(self):
        """Re-evaluate device binding on every request, keep identity private."""
        status=execute([ACTIONS,"status"])
        serial=status.get("device")
        if (status.get("target")!=TRUSTED_TARGET
            or not isinstance(serial,str) or not serial
            or not isinstance(status.get("focus"),str)
            or type(status.get("keyguard_locked")) is not bool):
            return None
        self._verified_serial=serial
        return status

    def safe_screen(self):
        """No observation or generic actions while Keyguard is showing."""
        status=self.bound_status()
        return status is not None and not status["keyguard_locked"]

    def do_GET(self):
        if not self.authorized():
            return
        path = urlparse(self.path).path
        try:
            if path == "/health":
                return self.send(200, {"ok": True, "modes": ["screen", "ocr", "actions"], "device_policy": "redmi9_bound", "commands": sorted(ALLOWED)})
            if path == "/screen/capture":
                params = parse_qs(urlparse(self.path).query)
                since = params.get("since_revision", ["0"])[0]
                if not since.isdecimal():
                    return self.send(400, {"error": "invalid_revision"})
                with FRAME_LOCK:
                    if not self.safe_screen():
                        return self.send(403, {"error": "device_not_authorized"})
                    started = time.monotonic_ns()
                    execute([ACTIONS, "screenshot"])
                    finished = time.monotonic_ns()
                    if not self.safe_screen():
                        return self.send(409, {"error": "screen_changed"})
                    frame_hash = hashlib.sha256(IMAGE.read_bytes()).hexdigest()[:24]
                    changed = frame_hash != CAPTURE_STATE["frame_id"]
                    if changed:
                        CAPTURE_STATE["revision"] += 1
                        CAPTURE_STATE["frame_id"] = frame_hash
                    revision = CAPTURE_STATE["revision"]
                    result = {"revision": revision, "frame_id": frame_hash,
                              "changed_since_client": int(since) != revision,
                              "image_changed": changed,
                              "capture_start_monotonic_ns": started,
                              "capture_end_monotonic_ns": finished,
                              "capture_duration_ms": round((finished-started)/1e6, 2),
                              "ocr_executed": False, "vsync_aligned": False}
                return self.send(200, result)
            if path == "/screen/tree":
                with FRAME_LOCK:
                    if not self.safe_screen():
                        return self.send(403, {"error": "device_not_authorized"})
                    hierarchy = read_accessibility(self._verified_serial)
                    if not self.safe_screen():
                        return self.send(409, {"error": "screen_changed"})
                    tree = public_tree(hierarchy)
                return self.send(200, tree)
            if path == "/screen/semantics":
                with FRAME_LOCK:
                    if not self.safe_screen():
                        return self.send(403, {"error": "device_not_authorized"})
                    hierarchy = read_accessibility(self._verified_serial)
                    if not self.safe_screen():
                        return self.send(409, {"error": "screen_changed"})
                roles = {role: sum(n["role"] == role for n in hierarchy["nodes"]) for role in ("button", "input", "text", "container")}
                return self.send(200, {"source": "uiautomator", "count": hierarchy["count"], "roles": roles, "redacted": True})
            if path == "/status":
                result=self.bound_status()
                if result is None:
                    return self.send(403,{"error":"device_not_authorized"})
                return self.send(200,{"target":TRUSTED_TARGET,"device_bound":True,
                                      "keyguard_locked":result["keyguard_locked"],
                                      "focus_present":bool(result["focus"])})
            if path == "/screen/changes":
                with FRAME_LOCK:
                    if not self.safe_screen():
                        return self.send(403, {"error": "device_not_authorized"})
                    execute([ACTIONS, "screenshot"])
                    if not self.safe_screen():
                        return self.send(409, {"error": "screen_changed"})
                    result = DETECTOR.compare(IMAGE)
                return self.send(200, result)
            if path == "/screen/dom":
                # Legacy text-bearing diff, merge, checkpoint and journal disabled.
                # Only return geometric roles without OCR labels or raw text.
                with FRAME_LOCK:
                    if not self.safe_screen():
                        return self.send(423,{"error":"screen_locked_or_unavailable"})
                    execute([ACTIONS,"screenshot"])
                    changed=DETECTOR.compare(IMAGE)
                    if not changed["changed"]:
                        return self.send(200,{"changed":False,"sequence":changed["sequence"],
                                              "redacted":True,"persistent":False})
                    current=build_ocr_dom(IMAGE)
                    if not self.safe_screen():
                        return self.send(409,{"error":"screen_changed"})
                    tree=public_tree(current)
                    return self.send(200,{"changed":True,"sequence":changed["sequence"],
                                          "tree":tree,"redacted":True,"persistent":False})
            if path == "/screen.jpg":
                with FRAME_LOCK:
                    if not self.safe_screen():
                        return self.send(403, {"error": "device_not_authorized"})
                    execute([ACTIONS, "screenshot"])
                    data = IMAGE.read_bytes()
                    if not self.safe_screen():
                        return self.send(409, {"error": "screen_changed"})
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/screen.mjpeg":
                return self.send(410, {"error": "stream_disabled_use_capture"})
            self.send(404, {"error": "not_found"})
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, BrokenPipeError):
            try:
                self.send(503, {"error": "operation_failed"})
            except OSError:
                pass

    def do_POST(self):
        if not self.authorized():
            return
        length = self.headers.get("Content-Length", "")
        if not length.isdecimal() or not 0 < int(length) <= MAX_BODY:
            return self.send(413, {"error": "invalid_length"})
        try:
            data = json.loads(self.rfile.read(int(length)))
            if not isinstance(data, dict):
                raise ValueError()
            path = urlparse(self.path).path
            if path == "/ocr/find":
                text = data.get("text")
                if not isinstance(text, str) or not 0 < len(text) <= 80:
                    raise ValueError()
                with FRAME_LOCK:
                    if not self.safe_screen():
                        return self.send(403, {"error": "device_not_authorized"})
                    execute([ACTIONS, "screenshot"])
                    result = execute([OCR, "--text", text, "--image", IMAGE])
                    if not self.safe_screen():
                        return self.send(409, {"error": "screen_changed"})
                return self.send(200, result)
            if path == "/action":
                command = data.get("command")
                if command not in ALLOWED:
                    raise ValueError()
                argv = [ACTIONS, "unlock" if command == "screen.unlock" else command]
                for key in ("x", "y", "x2", "y2", "ms"):
                    if key in data:
                        val = data[key]
                        if type(val) is not int or not 0 <= val <= 10000:
                            raise ValueError()
                        argv.extend(["--" + key, str(val)])
                if "package" in data:
                    val = data["package"]
                    if not isinstance(val, str) or len(val) > 150:
                        raise ValueError()
                    argv.extend(["--package", val])
                with FRAME_LOCK:
                    status=self.bound_status()
                    if status is None:
                        return self.send(403,{"error":"device_not_authorized"})
                    if status["keyguard_locked"] and command not in ("wake","status","screen.unlock"):
                        return self.send(423,{"error":"screen_locked"})
                    result=execute(argv)
                    if command == "screen.unlock":
                        return self.send(200 if result.get("verified") is True else 423, result)
                    return self.send(200,result)
            self.send(404, {"error": "not_found"})
        except (ValueError, TypeError, json.JSONDecodeError):
            self.send(400, {"error": "invalid_request"})
        except (OSError, RuntimeError, subprocess.SubprocessError):
            self.send(503, {"error": "operation_failed"})

if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
