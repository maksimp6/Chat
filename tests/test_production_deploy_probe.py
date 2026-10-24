"""A public probe must not turn missing evidence into production acceptance."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
import requests

from scripts import production_public_probe as probe


@pytest.fixture
def site():
    state = {
        "health_status": 200,
        "root_status": 401,
        "body": b'{"status":"ok"}',
        "content_type": "application/json",
        "paths": [],
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["paths"].append(self.path)
            health = self.path == "/healthz"
            body = state["body"] if health else b"login required"
            self.send_response(state["health_status"] if health else state["root_status"])
            self.send_header("Content-Type", state["content_type"] if health else "text/html")
            self.send_header("Content-Length", str(len(body)))
            if not health:
                self.send_header("Location", "/must-not-follow")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_real_http_success_is_only_public_contract_not_full_acceptance(site):
    state, origin = site
    report = probe.probe_public(origin)
    assert report["status"] == "public_contract_passed"
    assert report["accepted"] is False
    assert report["authenticated_ui"] == "not_checked"
    assert report["browser"] == "not_checked"
    assert [r["http_status"] for r in report["checks"]] == [200, 401]
    assert state["paths"] == ["/healthz", "/"]


@pytest.mark.parametrize("code", [200, 302, 403, 404, 502])
def test_wrong_root_status_never_passes_or_follows_redirect(site, code):
    state, origin = site
    state["root_status"] = code
    assert probe.probe_public(origin)["status"] == "public_contract_failed"
    assert state["paths"] == ["/healthz", "/"]


@pytest.mark.parametrize(
    "body, content_type",
    [
        (b"{}", "application/json"),
        (b"[]", "application/json"),
        (b"broken", "application/json"),
        (b'{"status":"ok"}', "text/html"),
        (b'{"status":"error"}', "application/json"),
        (b"x" * 17000, "application/json"),
    ],
)
def test_health_200_requires_actual_json_contract(site, body, content_type):
    state, origin = site
    state.update(body=body, content_type=content_type)
    report = probe.probe_public(origin)
    assert report["status"] == "public_contract_failed"
    assert report["checks"][0]["error"] == "invalid_health_contract"


@pytest.mark.parametrize(
    "error, expected",
    [
        (requests.exceptions.SSLError("private certificate detail"), "tls_failed"),
        (requests.exceptions.Timeout("private timed out URL"), "timeout"),
        (requests.exceptions.ConnectionError("private backend address"), "transport_failed"),
    ],
)
def test_network_failure_is_failed_not_missing_or_success(monkeypatch, error, expected):
    def fail(*_args, **kwargs):
        assert kwargs["allow_redirects"] is False
        assert kwargs["timeout"] == (3, 5)
        raise error

    monkeypatch.setattr(requests.Session, "get", fail)
    report = probe.probe_public("https://maxxxpavlov.ru")
    assert report["status"] == "public_contract_failed"
    assert all(row["error"] == expected and row["http_status"] is None for row in report["checks"])
    assert "private" not in json.dumps(report)


@pytest.mark.parametrize(
    "origin",
    [
        "http://maxxxpavlov.ru",
        "https://foreign.invalid",
        "https://user:password@maxxxpavlov.ru",
        "https://maxxxpavlov.ru/path",
        "https://maxxxpavlov.ru?token=x",
        "https://maxxxpavlov.ru#fragment",
        "https://maxxxpavlov.ru:8443",
    ],
)
def test_unapproved_or_secret_bearing_origin_rejected(origin):
    with pytest.raises(ValueError, match="invalid_probe_origin"):
        probe.probe_public(origin)


def test_cli_exit_codes_and_no_response_body_leak(site, capsys):
    state, origin = site
    assert probe.main(["--origin", origin]) == 0
    assert json.loads(capsys.readouterr().out)["accepted"] is False
    state["body"] = b'{"status":"broken","password":"must-not-leak"}'
    assert probe.main(["--origin", origin]) == 1
    assert "must-not-leak" not in capsys.readouterr().out
    assert probe.main(["--origin", "https://foreign.invalid?token=must-not-leak"]) == 2
    assert "must-not-leak" not in capsys.readouterr().out


def test_cli_entrypoint_rejects_unapproved_target(monkeypatch, capsys):
    import runpy

    monkeypatch.setattr("sys.argv", [probe.__file__, "--origin", "https://foreign.invalid"])
    with pytest.raises(SystemExit) as error:
        runpy.run_path(probe.__file__, run_name="__main__")
    assert error.value.code == 2
    assert json.loads(capsys.readouterr().out)["accepted"] is False
