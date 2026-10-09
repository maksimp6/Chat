"""Unprivileged policy gate for mouse requests.
No root, no input device and no network server.
"""
import socket
from alice_mouse_policy import authorize, parse_mouse_command

class MouseBridge:
    def __init__(self, socket_path, allowed_apps):
        self.socket_path = socket_path
        self.allowed_apps = frozenset(allowed_apps)

    def handle(self, role, command, foreground, *, sensitive=False, confirmed=False):
        parsed = parse_mouse_command(command)
        if parsed is None:
            return {"ok": False, "error": "invalid_command"}
        action, x, y = parsed
        decision = authorize(role, action, foreground=foreground,
                             allowed_apps=self.allowed_apps,
                             sensitive=sensitive, confirmed=confirmed)
        if not decision.allowed:
            return {"ok": False, "error": decision.reason}
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                sock.connect(self.socket_path)
                sock.sendall(f"{action} {x} {y}\n".encode("ascii"))
                response = sock.recv(16)
        except (OSError, TimeoutError):
            return {"ok": False, "error": "bridge_unavailable"}
        return {"ok": response == b"OK\n", "error": None if response == b"OK\n" else "device_denied"}

[executed on device: localhost (bb12d9f4-83a6-41b5-9ede-8717bac12a0a)]
