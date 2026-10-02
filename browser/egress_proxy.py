"""Browser egress proxy: connect only to resolved public IPs on web ports."""

import ipaddress
import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


def connect_public(host, port):
    if port not in {80, 443}:
        raise ValueError("web_port_required")
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("non_public_destination")
    # Connect to the checked numeric address, never resolve the name a second time.
    family, socktype, proto, _, address = addresses[0]
    upstream = socket.socket(family, socktype, proto)
    upstream.settimeout(10)
    try:
        upstream.connect(address)
    except Exception:
        upstream.close()
        raise
    return upstream


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def tunnel(self, upstream):
        try:
            while True:
                ready, _, _ = select.select([self.connection, upstream], [], [], 35)
                if not ready:
                    break
                for source in ready:
                    chunk = source.recv(65536)
                    if not chunk:
                        return
                    (upstream if source is self.connection else self.connection).sendall(chunk)
        finally:
            upstream.close()
            self.close_connection = True

    def do_CONNECT(self):
        try:
            parsed = urlsplit("https://" + self.path)
            if not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("bad_target")
            upstream = connect_public(parsed.hostname, parsed.port or 443)
        except (ValueError, OSError):
            self.send_error(403, "Destination unavailable")
            return
        self.send_response(200)
        self.end_headers()
        self.tunnel(upstream)

    def do_GET(self):
        try:
            parsed = urlsplit(self.path)
            if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("bad_target")
            upstream = connect_public(parsed.hostname, parsed.port or 80)
        except (ValueError, OSError):
            self.send_error(403, "Destination unavailable")
            return
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        request = f"GET {target} HTTP/1.1\r\nHost: {parsed.netloc}\r\nConnection: close\r\n"
        for key, value in self.headers.items():
            if key.lower() not in {"host", "connection", "proxy-connection", "proxy-authorization"}:
                request += f"{key}: {value}\r\n"
        upstream.sendall((request + "\r\n").encode("latin-1"))
        self.tunnel(upstream)


def start_proxy():
    server = ThreadingHTTPServer(("127.0.0.1", 8899), Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
