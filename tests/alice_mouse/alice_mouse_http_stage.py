"""Isolated mouse API handler: synthetic roles, deny by default, no production writes."""
import json
import secrets
from http.server import BaseHTTPRequestHandler
from alice_mouse_bridge import MouseBridge

class MouseHandler(BaseHTTPRequestHandler):
    bridge = None
    foreground = ""
    token_roles = {}
    def log_message(self, *args):
        return
    def reply(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"ok": True, "stage": True})
        return self.reply(404, {"error": "not_found"})
    def do_POST(self):
        if self.path != "/mouse":
            return self.reply(404, {"error": "not_found"})
        length = self.headers.get("Content-Length", "")
        if not length.isdecimal() or not 0 < int(length) <= 256:
            return self.reply(413, {"error": "bad_length"})
        try:
            body = json.loads(self.rfile.read(int(length)))
        except (ValueError, UnicodeDecodeError):
            return self.reply(400, {"error": "bad_json"})
        supplied = self.headers.get("Authorization", "")
        role = next((role for token, role in self.token_roles.items() if secrets.compare_digest(supplied, "Bearer " + token)), "")
        if not role:
            return self.reply(401, {"error": "unauthorized"})
        result = self.bridge.handle(role, body, self.foreground)
        return self.reply(200 if result["ok"] else 403, result)

[executed on device: localhost (bb12d9f4-83a6-41b5-9ede-8717bac12a0a)]
