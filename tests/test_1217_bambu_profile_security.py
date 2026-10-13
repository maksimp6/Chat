"""Private credential loading and fail-closed TLS pin regressions."""

import hashlib
import os
from pathlib import Path
import ssl
from types import SimpleNamespace

import pytest

from printing3d import bambu_lan, bambu_local_profile
from printing3d.bambu_requests import MockBroker


def _private_profile(path: Path) -> None:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    for name, content in {
        "host": "192.168.31.12",
        "serial": "A1MINI123",
        "access_code": "ABC12345",
        "printer.pem": "dummy-cert",
    }.items():
        target = path / name
        target.write_text(content)
        target.chmod(0o600)


def test_load_owner_private_profile(tmp_path, monkeypatch):
    folder = tmp_path / "profile"
    _private_profile(folder)
    monkeypatch.setattr(bambu_local_profile, "_certificate_fingerprint", lambda pem: b"x")
    result = bambu_local_profile.load_local_profile(folder)
    assert result.host == "192.168.31.12"
    assert result.serial == "A1MINI123"
    assert result.access_code == "ABC12345"


def test_no_public_directory_or_file_permissions(tmp_path, monkeypatch):
    folder = tmp_path / "profile"
    _private_profile(folder)
    monkeypatch.setattr(bambu_local_profile, "_certificate_fingerprint", lambda pem: b"x")
    folder.chmod(0o755)
    with pytest.raises(PermissionError):
        bambu_local_profile.load_local_profile(folder)
    folder.chmod(0o700)
    (folder / "access_code").chmod(0o644)
    with pytest.raises(PermissionError):
        bambu_local_profile.load_local_profile(folder)


def test_symlinked_secret_is_refused(tmp_path):
    folder = tmp_path / "profile"
    _private_profile(folder)
    (folder / "access_code").unlink()
    (folder / "access_code").symlink_to(folder / "host")
    with pytest.raises(OSError):
        bambu_local_profile.load_local_profile(folder)


def test_missing_secret_fails_closed(tmp_path):
    folder = tmp_path / "profile"
    _private_profile(folder)
    (folder / "access_code").unlink()
    with pytest.raises(FileNotFoundError):
        bambu_local_profile.load_local_profile(folder)


def test_tls_pin_matches_exact_leaf_before_mqtt_connect(monkeypatch):
    class FakeSSLSocket:
        def __init__(self, cert):
            self.cert = cert

        def getpeercert(self, *, binary_form=False):
            assert binary_form
            return self.cert

    monkeypatch.setattr(ssl, "SSLSocket", FakeSSLSocket)
    client = SimpleNamespace(on_socket_open=None, suppress_exceptions=True)
    bambu_lan._install_tls_pin(client, hashlib.sha256(b"trusted-leaf").digest())
    assert client.suppress_exceptions is False
    client.on_socket_open(client, None, FakeSSLSocket(b"trusted-leaf"))
    with pytest.raises(ssl.SSLError, match="differs"):
        client.on_socket_open(client, None, FakeSSLSocket(b"impostor"))


def test_subscribe_sends_only_safe_status_query():
    published = []

    class FakeClient:
        def publish(self, topic, payload, qos=0):
            published.append((topic, payload, qos))

    client = FakeClient()
    bambu_lan._install_callbacks(
        client, "owner", "A1MINI123", "device/A1MINI123/report", lambda: 1.0, [], []
    )
    client.on_subscribe(client, None, 1, [SimpleNamespace(is_failure=False)], None)
    assert len(published) == 1
    topic, payload, qos = published[0]
    assert qos == 0
    assert MockBroker("A1MINI123").publish(topic, payload)["command"] == "pushall"


def test_delta_without_gcode_state_not_mistaken_for_unknown(monkeypatch):
    persisted = []
    monkeypatch.setattr(bambu_lan, "record_bambu_print_report", lambda *a: persisted.append(a))
    with pytest.raises(ValueError, match="not a printer state report"):
        bambu_lan.consume_print_report(
            "owner", "ABC", "device/ABC/report", b'{"print":{"bed_temper":25}}', 7
        )
    assert persisted == []
