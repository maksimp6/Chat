"""Private W3C browser worker. Access is through SSH + docker exec, never a public port."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from browser.capabilities import BrowserAction, validate_browser_action
from browser.egress_proxy import start_proxy


class Worker:
    """One browser/profile per worker; serialized actions preserve page state."""

    def __init__(self, request=None):
        self.request = request or self._request
        self.session = None
        self.lock = threading.Lock()

    @staticmethod
    def _request(method, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:9515" + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        # Never route internal driver traffic through environment proxies.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=40) as response:
            result = json.load(response)["value"]
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError("webdriver_failure")
        return result

    def start(self):
        if self.session is None:
            result = self.request(
                "POST",
                "/session",
                {
                    "capabilities": {
                        "alwaysMatch": {
                            "browserName": "chrome",
                            "goog:chromeOptions": {
                                "binary": "/usr/bin/chromium",
                                "args": [
                                    "--headless=new",
                                    "--user-data-dir=/data/profile",
                                    "--disable-dev-shm-usage",
                                    "--window-size=1280,900",
                                    "--proxy-server=http://127.0.0.1:8899",
                                    "--proxy-bypass-list=<-loopback>",
                                    "--disable-quic",
                                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                                ],
                                "prefs": {"download.default_directory": "/data/downloads"},
                            },
                        }
                    }
                },
            )
            self.session = result["sessionId"]
            self.request("POST", self.path("timeouts"), {"pageLoad": 30000, "script": 5000})

    def path(self, suffix):
        return f"/session/{self.session}/{suffix}"

    def execute(self, envelope):
        if not isinstance(envelope, dict) or set(envelope) - {"action", "target", "value"}:
            return {"success": False, "error": "invalid_request"}
        action = BrowserAction(
            "browser_cloud",
            envelope.get("action", ""),
            envelope.get("target", ""),
            envelope.get("value"),
        )
        if (
            not isinstance(action.action, str)
            or not isinstance(action.target, str)
            or validate_browser_action(action)
        ):
            return {"success": False, "error": "invalid_action"}
        # First vertical slice is inspection only. Interactive actions require an
        # executor-bound approval channel; raw SSH callers cannot self-approve.
        if action.action not in {"navigate", "inspect", "assert_state"}:
            return {"success": False, "error": "interactive_channel_not_configured"}
        if action.action == "navigate":
            url = urlsplit(action.target)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
            ):
                return {"success": False, "error": "invalid_url"}
            if url.query or url.fragment:
                return {"success": False, "error": "credential_free_url_required"}
        with self.lock:
            try:
                self.start()
                if action.action == "navigate":
                    self.request("POST", self.path("url"), {"url": action.target})
                title = self.request("GET", self.path("title"))
                # Only title and origin are returned in this slice; no HTML,
                # cookies, form values or credential-bearing redirect URLs.
                current = urlsplit(self.request("GET", self.path("url")))
                origin = f"{current.scheme}://{current.netloc.rsplit('@', 1)[-1]}"
                if action.action == "assert_state" and title != action.value:
                    return {"success": False, "error": "title_mismatch"}
                return {"success": True, "data": {"title": str(title)[:512], "origin": origin}}
            except Exception:
                # Driver exceptions may contain URLs, cookies or page data.
                return {"success": False, "error": "browser_execution_failed"}

    def close(self):
        if self.session:
            try:
                self.request("DELETE", f"/session/{self.session}")
            finally:
                self.session = None


def create_server(worker=None, address=("127.0.0.1", 8765)):
    worker = worker or Worker()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            if self.path != "/action":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    raise ValueError("request_size")
                result = worker.execute(json.loads(self.rfile.read(length)))
            except (ValueError, TypeError, AttributeError):
                result = {"success": False, "error": "invalid_request"}
            payload = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return HTTPServer(address, Handler)


def serve():
    worker = Worker()
    server = create_server(worker)
    try:
        server.serve_forever()
    finally:
        worker.close()
        server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["serve", "request"])
    args = parser.parse_args()
    if args.command == "serve":

        def terminate(_signum, _frame):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, terminate)
        Path("/data/profile").mkdir(parents=True, exist_ok=True)
        os.chmod("/data/profile", 0o700)
        proxy = start_proxy()
        driver = subprocess.Popen(
            ["chromedriver", "--port=9515", "--allowed-ips=127.0.0.1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(50):
                try:
                    Worker._request("GET", "/status")
                    break
                except (OSError, urllib.error.URLError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("driver_unavailable")
            serve()
        finally:
            proxy.shutdown()
            driver.terminate()
            driver.wait(timeout=10)
    else:
        import sys

        payload = sys.stdin.buffer.read(8193)
        if len(payload) > 8192:
            raise SystemExit("request_too_large")
        req = urllib.request.Request(
            "http://127.0.0.1:8765/action",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            req, timeout=45
        ) as response:
            result = json.loads(response.read(16384))
            sys.stdout.write(json.dumps(result))
            if not result.get("success"):
                raise SystemExit(1)


if __name__ == "__main__":
    main()
