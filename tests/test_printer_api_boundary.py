"""Boundary tests: application API cannot silently fall back to LAN or cloud."""

from unittest.mock import Mock

import pytest

from printer_service.printers.api import CloudReader, LanReader, PrinterService
from printer_service.printers.models import PrinterIdentity


def test_service_uses_injected_reader_only():
    reader = Mock()
    reader.identity.return_value = PrinterIdentity(
        "192.0.2.1", "N1", "Bambu Lab A1 mini", "1.0", "cloud", "occupied"
    )
    reader.status.return_value = {"print": {"gcode_state": "IDLE", "serial": "private"}}
    service = PrinterService(reader)
    assert service.describe()["model"] == "Bambu Lab A1 mini"
    assert service.status() == {"gcode_state": "IDLE"}
    reader.identity.assert_called_once_with()
    reader.status.assert_called_once_with()


def test_lan_status_fails_closed():
    with pytest.raises(NotImplementedError, match="authorized MQTT"):
        LanReader("192.0.2.1").status()


def test_cloud_is_explicitly_unconfigured():
    with pytest.raises(NotImplementedError, match="not configured"):
        CloudReader().status()
