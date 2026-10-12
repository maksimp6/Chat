"""Automated read-only printer bridge E2E harness (no physical printer).

Exercises the Python IPC boundary, validates the Kotlin contract's safety
invariants, and fails if Android exposes a printer WebView/HTTP entry point.
A separate instrumented test is still required for a real Android Network.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "android/app/src/main/java/com/alicepro/mobile/printer/BambuWifiDiscovery.kt"
MAIN = ROOT / "android/app/src/main/java/com/alicepro/mobile/MainActivity.kt"
MANIFEST = ROOT / "android/app/src/main/AndroidManifest.xml"
BRIDGE = ROOT / "android/app/src/main/python/printer_native_bridge.py"


class Context:
    def getApplicationContext(self):
        return self


class Identity:
    def getPinnedIp(self):
        return "192.168.31.12"

    def getModelCode(self):
        return "N1"


@pytest.fixture
def module():
    spec = importlib.util.spec_from_file_location("printer_native_e2e", BRIDGE)
    assert spec is not None and spec.loader is not None
    instance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(instance)
    return instance


def test_python_to_native_mock_e2e(module, monkeypatch):
    calls = []

    def discover(context, hostname):
        calls.append((context, hostname))
        return Identity()

    monkeypatch.setitem(
        sys.modules,
        "java",
        types.SimpleNamespace(jclass=lambda name: types.SimpleNamespace(discover=discover)),
    )
    context = Context()
    module.install_context(context)
    assert module.discover("printer.local") == {
        "pinned_ip": "192.168.31.12",
        "model_code": "N1",
        "network_transport": "WIFI",
    }
    assert calls == [(context, "printer.local")]


def test_no_context_fails_closed(module):
    with pytest.raises(RuntimeError, match="not initialized"):
        module.discover("printer.local")


def test_native_socket_bound_before_connect():
    code = NATIVE.read_text()
    assert "network.getAllByName(host)" in code
    assert "network.bindSocket(socket)" in code
    assert "socket.connect(InetSocketAddress(pinned, 3000), 3000)" in code
    assert code.index("network.bindSocket(socket)") < code.index("socket.connect(")
    assert "bindProcessToNetwork" not in code
    assert "Socket().use" in code


def test_ipc_not_exposed_to_webview_or_exported_service():
    main = MAIN.read_text()
    manifest = MANIFEST.read_text()
    assert 'getModule("printer_native_bridge")' in main
    assert "callAttr(\"install_context\", applicationContext)" in main
    assert "@JavascriptInterface\n        fun discoverPrinter" not in main
    assert "printer_native_bridge" not in manifest
    assert "android:exported=\"true\"" not in manifest.split("<provider")[1]


def test_native_discovery_is_read_only():
    code = NATIVE.read_text()
    assert '"""{"login":{"command":"detect","sequence_id":"1"}}"""' in code
    assert "socket.getOutputStream().write(detectFrame())" in code
    assert "network.getAllByName(host)" in code
