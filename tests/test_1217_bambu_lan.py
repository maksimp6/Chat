"""Safety and status-report tests for LAN MQTT integration."""
import json

import pytest

from printing3d import bambu_lan


def test_only_concrete_report_topic():
    assert bambu_lan.printer_report_topic("A1MINI123") == "device/A1MINI123/report"
    for bad in ("", "+", "#", "../foo", "serial/other"):
        with pytest.raises(ValueError):
            bambu_lan.printer_report_topic(bad)


def test_reject_wrong_topic_and_oversize_without_persistence(monkeypatch):
    events = []
    monkeypatch.setattr(bambu_lan, "record_bambu_print_report", lambda *a: events.append(a))
    with pytest.raises(ValueError):
        bambu_lan.consume_print_report("owner", "ABC", "device/OTHER/report", b"{}", 10)
    with pytest.raises(ValueError):
        bambu_lan.consume_print_report(
            "owner", "ABC", "device/ABC/report", b"x" * (256 * 1024 + 1), 10
        )
    assert not events


def test_authenticated_report_forwards_to_canonical_store(monkeypatch):
    observed = []
    def persist(*args):
        observed.append(args)
        return True
    monkeypatch.setattr(bambu_lan, "record_bambu_print_report", persist)
    assert bambu_lan.consume_print_report(
        "owner", "ABC", "device/ABC/report",
        json.dumps({"print": {"gcode_state": "IDLE"}}).encode(), 12
    )
    assert observed == [("owner", "ABC", {"print": {"gcode_state": "IDLE"}}, 12)]
