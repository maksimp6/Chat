"""Regression coverage for all new security and downtime failure paths.

MQTT client simulations never open a network socket or send printer commands.
"""

import hashlib
import json
import ssl
from pathlib import Path
from types import SimpleNamespace

import pytest

from printing3d import bambu_lan, bambu_local_profile, service
from printing3d.bambu_requests import capture_request, request_topic
from printing3d.downtime import DowntimeTracker


def test_failure_inputs_do_not_change_downtime_history():
    t = DowntimeTracker()
    for kw in (
        {"state": "idle", "timestamp": True},
        {"state": "idle", "timestamp": "10"},
        {"state": "idle", "timestamp": 0, "reason": "secret-not-a-reason"},
        {"state": "idle", "timestamp": 0, "event_id": ""},
    ):
        with pytest.raises(ValueError):
            t.observe(**kw)
    t.observe("blocked", 10, reason="maintenance")
    with pytest.raises(ValueError, match="Conflicting simultaneous observation"):
        t.observe("printing", 10)
    with pytest.raises(ValueError, match="Conflicting simultaneous reason"):
        t.observe("blocked", 10, reason="no_filament")
    for start, end in ((10, 9), (-1, 3), (0, float("nan"))):
        with pytest.raises(ValueError):
            t.summary(start, end)
    for corrupt in ({"schema": 2, "events": []}, {"schema": 1, "events": [{}]}):
        with pytest.raises(ValueError):
            DowntimeTracker.from_snapshot(corrupt)
    assert len(t.snapshot()["events"]) == 1


@pytest.mark.parametrize(
    "fn",
    [
        lambda: service.record_printer_downtime("", "ABC", "idle", 1),
        lambda: service.get_printer_downtime("", "ABC", 0, 1),
        lambda: service.expire_printer_telemetry("", "ABC", last_seen=0, now=2),
        lambda: service.get_printer_daily_downtime("", "ABC", None),
    ],
)
def test_missing_owner_rejected_before_store_open(fn):
    with pytest.raises(ValueError, match="owner and printer"):
        fn()


def test_request_malformed_json_has_secret_free_exception():
    with pytest.raises(ValueError, match="invalid JSON"):
        capture_request(request_topic("ABC"), b"{bad-json", "ABC")


def test_certificate_parse_paths(monkeypatch):
    pem = "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"
    monkeypatch.setattr(bambu_lan.ssl, "PEM_cert_to_DER_cert", lambda value: b"fake-der")
    assert bambu_lan._certificate_fingerprint(pem) == hashlib.sha256(b"fake-der").digest()

    def reject(_pem):
        raise ValueError("malformed")

    monkeypatch.setattr(bambu_lan.ssl, "PEM_cert_to_DER_cert", reject)
    with pytest.raises(ValueError, match="invalid printer certificate"):
        bambu_lan._certificate_fingerprint(pem)


def test_tls_pin_rejects_plaintext_transport(monkeypatch):
    client = SimpleNamespace(on_socket_open=None, suppress_exceptions=True)
    bambu_lan._install_tls_pin(client, hashlib.sha256(b"cert").digest())
    with pytest.raises(ssl.SSLError, match="not TLS"):
        client.on_socket_open(client, None, object())


def test_mqtt_callback_error_and_incomplete_deltas(monkeypatch):
    received, failures = [], []
    client = SimpleNamespace(on_connect=None, on_subscribe=None, on_message=None)
    bambu_lan._install_callbacks(
        client, "owner", "ABC", "device/ABC/report", lambda: 12, received, failures
    )
    client.on_connect(client, None, None, SimpleNamespace(is_failure=True), None)
    assert failures == ["MQTT authentication failed"]
    failures.clear()
    client.on_subscribe(client, None, 1, [SimpleNamespace(is_failure=True)], None)
    assert failures == ["MQTT subscription rejected"]
    failures.clear()
    for data in (b"[]", b'{"print":{}}', b'{"print":{"bed_temper":25}}'):
        client.on_message(client, None, SimpleNamespace(topic="device/ABC/report", payload=data))
    assert received == []
    assert failures == []
    client.on_message(client, None, SimpleNamespace(topic="device/ABC/report", payload=b"{bad"))
    assert failures == ["invalid MQTT report"]


@pytest.mark.parametrize(
    "scenario, expected",
    [
        ("ok", True),
        ("auth", False),
        ("subscribe", False),
        ("timeout", False),
    ],
)
def test_fake_mqtt_full_connection_lifecycle(monkeypatch, scenario, expected):
    """Observe CONNECT -> SUBSCRIBE -> pushall -> report without a real printer."""
    clients = []

    class FakeClient:
        on_connect = None
        on_subscribe = None
        on_message = None

        def __init__(self, *args, **kwargs):
            self.calls = []
            clients.append(self)

        def tls_set_context(self, ctx):
            self.calls.append("tls")
            assert isinstance(ctx, ssl.SSLContext)

        def username_pw_set(self, user, password):
            assert self.on_socket_open is not None
            assert (user, password) == ("bblp", "12345678")
            self.calls.append("credentials_configured")

        def connect(self, host, port, keepalive):
            assert (host, port, keepalive) == ("192.168.31.12", 8883, 15)
            self.calls.append("connect")

        def subscribe(self, topic, qos):
            self.calls.append("subscribe")
            assert topic == "device/ABC/report"
            return 0, 1

        def publish(self, topic, payload, qos):
            self.calls.append("pushall")
            assert topic == "device/ABC/request"
            assert json.loads(payload)["pushing"]["command"] == "pushall"
            self.on_message(
                self,
                None,
                SimpleNamespace(
                    topic="device/ABC/report", payload=b'{"print":{"gcode_state":"IDLE"}}'
                ),
            )

        def loop_start(self):
            self.calls.append("started")
            if scenario != "timeout":
                self.on_connect(
                    self,
                    None,
                    None,
                    SimpleNamespace(is_failure=scenario == "auth"),
                    None,
                )
                if scenario in ("ok", "subscribe"):
                    self.on_subscribe(
                        self,
                        None,
                        1,
                        [SimpleNamespace(is_failure=scenario == "subscribe")],
                        None,
                    )

        def loop_stop(self):
            self.calls.append("stopped")

        def disconnect(self):
            self.calls.append("disconnected")

    fake_module = SimpleNamespace(
        Client=FakeClient,
        CallbackAPIVersion=SimpleNamespace(VERSION2=2),
        MQTTv311=4,
    )
    monkeypatch.setattr(bambu_lan.importlib, "import_module", lambda name: fake_module)
    monkeypatch.setattr(bambu_lan, "_certificate_fingerprint", lambda pem: b"fixed-pin")
    monkeypatch.setattr(bambu_lan, "record_bambu_print_report", lambda *args: True)
    result = bambu_lan.read_live_status(
        "owner",
        "ABC",
        "12345678",
        host="192.168.31.12",
        timeout=0.01,
        trusted_cert_pem="trusted",
    )
    assert result["received"] is expected
    assert clients[0].calls[-2:] == ["stopped", "disconnected"]
    if scenario == "ok":
        assert "pushall" in clients[0].calls
    else:
        assert "pushall" not in clients[0].calls


def _config(path: Path):
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    for name, value in (
        ("serial", "ABC"),
        ("host", "192.168.31.12"),
        ("access_code", "12345678"),
        ("printer.pem", "-----BEGIN CERTIFICATE-----\nAA==\n-----END CERTIFICATE-----"),
    ):
        (path / name).write_text(value)
        (path / name).chmod(0o600)


@pytest.mark.parametrize(
    "name,value,error",
    [
        ("host", "invalid", "LAN IPv4"),
        ("host", "127.0.0.1", "private LAN"),
        ("access_code", "invalid!", "access code"),
    ],
)
def test_private_profile_rejects_bad_fields(tmp_path, monkeypatch, name, value, error):
    folder = tmp_path / "profile"
    _config(folder)
    (folder / name).write_text(value)
    monkeypatch.setattr(bambu_local_profile, "_certificate_fingerprint", lambda pem: b"ok")
    with pytest.raises(ValueError, match=error):
        bambu_local_profile.load_local_profile(folder)


def test_private_profile_rejects_oversize(tmp_path):
    folder = tmp_path / "profile"
    _config(folder)
    (folder / "serial").write_text("ABC" * 100)
    with pytest.raises(ValueError, match="too large"):
        bambu_local_profile.load_local_profile(folder)


def test_no_mqtt_connection_on_invalid_inputs():
    with pytest.raises(ValueError, match="missing printer credentials"):
        bambu_lan.read_live_status(
            "owner", "ABC", "", host="192.168.31.12",
            trusted_cert_pem="", timeout=1,
        )


@pytest.mark.parametrize("payload", [b"[]", b'{"print":[]}', b'{"pushing":{"command":"pushall"}}'])
def test_non_print_reports_never_reach_memory(payload, monkeypatch):
    seen = []
    monkeypatch.setattr(bambu_lan, "record_bambu_print_report", lambda *args: seen.append(args))
    with pytest.raises(ValueError, match="invalid printer report"):
        bambu_lan.consume_print_report("owner", "ABC", "device/ABC/report", payload, 1)
    assert not seen
