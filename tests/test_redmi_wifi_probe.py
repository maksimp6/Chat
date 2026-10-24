"""Real local HTTP checks plus Wi-Fi address parsing; no provider calls."""
import http.server
import importlib.util
from pathlib import Path
import threading

import pytest

spec = importlib.util.spec_from_file_location("wifi_probe", Path(__file__).parents[1] / "scripts/redmi_wifi_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.fixture
def server(tmp_path):
    responses = {
        "/healthz": b'{"status":"ok"}', "/": b"<title>Logos</title>",
        "/static/chat.js": b"matching JS", "/static/style.css": b"matching CSS",
    }
    for name in ("chat.js", "style.css"):
        (tmp_path / name).write_bytes(responses["/static/" + name])
    requests = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            body = responses[self.path]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        yield "http://127.0.0.1:" + str(httpd.server_port), tmp_path, responses, requests
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


def test_matching_application_and_assets_are_required(server):
    base, assets, _, requests = server
    result = probe.check_base(base, assets)
    assert result["status"] == "ready"
    assert result["external_peer_verified"] is False
    assert requests == ["/healthz", "/", "/static/chat.js", "/static/style.css"]


@pytest.mark.parametrize("path,body,error", [
    ("/healthz", b'{"status":"failed"}', "health_not_ready"),
    ("/healthz", b'[]', "health_not_ready"),
    ("/healthz", b'not JSON', "invalid_health_json"),
    ("/", b'<title>Other app</title>', "wrong_application_html"),
    ("/static/chat.js", b'wrong JS', "static_asset_mismatch"),
])
def test_http_200_does_not_hide_wrong_application(server, path, body, error):
    base, assets, responses, _ = server
    responses[path] = body
    with pytest.raises(probe.ProbeFailure, match=error):
        probe.check_base(base, assets)


def test_connection_refusal_is_not_readiness(server):
    base, assets, _, _ = server
    with pytest.raises(probe.ProbeFailure, match="http_read_failed"):
        probe.check_base("http://127.0.0.1:1", assets, timeout=0.2)


def test_wifi_address_is_taken_only_from_wlan0():
    output = "1: lo inet 127.0.0.1/8 scope host lo\n3: ccmni1 inet 198.51.100.3/24 scope global ccmni1\n32: wlan0 inet 192.0.2.10/24 scope global wlan0\n"
    assert probe.parse_wifi_ip(output) == "192.0.2.10"
    with pytest.raises(probe.ProbeFailure, match="unavailable"):
        probe.parse_wifi_ip("1: lo inet 127.0.0.1/8 scope host lo\n")


@pytest.mark.parametrize("value", ["127.0.0.1", "0.0.0.0", "224.0.0.1", "255.255.255.255", "bad"])
def test_loopback_or_invalid_addresses_cannot_prove_wifi(value):
    with pytest.raises(probe.ProbeFailure):
        probe.address(value)
