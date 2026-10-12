"""Transport boundary regression tests."""

import pytest

from alice_pro.printers.cloud_api import CloudApiNotConfigured, DisabledCloudApi
from alice_pro.printers.lan import probe
from alice_pro.printers.models import merge_report, summarize_status


def test_lan_probe_is_separate_entrypoint():
    assert callable(probe)


def test_cloud_fails_closed_without_lan_fallback(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("LAN transport must not be invoked")

    monkeypatch.setattr("socket.create_connection", forbidden)
    with pytest.raises(CloudApiNotConfigured):
        DisabledCloudApi().get_status("printer-id")


def test_models_have_no_network_dependencies():
    assert merge_report({"print": {"gcode_state": "IDLE"}}, {"print": {"mc_percent": 0}}) == {
        "print": {"gcode_state": "IDLE", "mc_percent": 0}
    }
    assert summarize_status({"print": {"bed_temper": 24, "access_code": "secret"}}) == {
        "bed_temper": 24
    }
