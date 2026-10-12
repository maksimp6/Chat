"""In-process Chaquopy IPC tests; no Android runtime, network or printer."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def bridge(monkeypatch):
    path = (
        Path(__file__).resolve().parents[1] / "android/app/src/main/python/printer_native_bridge.py"
    )
    spec = importlib.util.spec_from_file_location("printer_native_bridge_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Context:
    def getApplicationContext(self):
        return self


class Result:
    def getPinnedIp(self):
        return "192.168.31.12"

    def getModelCode(self):
        return "N1"


def test_bridge_uninitialized_fails_closed(bridge):
    with pytest.raises(RuntimeError, match="not initialized"):
        bridge.discover("printer.local")


def test_bridge_rejects_null_context(bridge):
    with pytest.raises(ValueError):
        bridge.install_context(None)


def test_bridge_calls_native_discovery(bridge, monkeypatch):
    calls = []
    native = types.SimpleNamespace(
        discover=lambda context, host: calls.append((context, host)) or Result()
    )
    monkeypatch.setitem(sys.modules, "java", types.SimpleNamespace(jclass=lambda name: native))
    context = Context()
    bridge.install_context(context)
    assert bridge.discover("printer.local") == {
        "pinned_ip": "192.168.31.12",
        "model_code": "N1",
        "network_transport": "WIFI",
    }
    assert calls == [(context, "printer.local")]


def test_bridge_rejects_empty_hostname(bridge):
    bridge.install_context(Context())
    with pytest.raises(ValueError):
        bridge.discover("")
