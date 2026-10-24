"""Native MemoryStore persistence contracts for printer downtime #1217."""

import pytest
from kristall import Kristall as MemoryStore
from printing3d import service


def test_persist_restart_owner_isolation_and_retry(tmp_path, monkeypatch):
    path = tmp_path / "printer.memory"
    with MemoryStore(path) as store:
        monkeypatch.setattr(service, "get_store", lambda: store)
        assert service.record_printer_downtime("a", "A1", "idle", 10, event_id="first")
        assert not service.record_printer_downtime("a", "A1", "idle", 10, event_id="first")
        service.record_printer_downtime("a", "A1", "printing", 20)
        assert service.get_printer_downtime("a", "A1", 0, 30)["seconds"]["IDLE_READY"] == 10
        assert service.get_printer_downtime("b", "A1", 0, 30)["seconds"]["UNKNOWN"] == 30
    with MemoryStore(path) as recovered:
        monkeypatch.setattr(service, "get_store", lambda: recovered)
        assert service.get_printer_downtime("a", "A1", 0, 30)["seconds"]["PRINTING"] == 10


def test_invalid_observation_cannot_change_committed_state(tmp_path, monkeypatch):
    with MemoryStore(tmp_path / "printer.memory") as store:
        monkeypatch.setattr(service, "get_store", lambda: store)
        service.record_printer_downtime("a", "A1", "idle", 10)
        before = service.get_printer_downtime("a", "A1", 0, 30)
        with pytest.raises(ValueError):
            service.record_printer_downtime("a", "A1", "printing", 9)
        assert service.get_printer_downtime("a", "A1", 0, 30) == before


def test_bambu_report_timeout_and_daily_persisted(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    midnight = datetime(2026, 10, 13, tzinfo=timezone.utc).timestamp()
    with MemoryStore(tmp_path / "printer.memory") as store:
        monkeypatch.setattr(service, "get_store", lambda: store)
        assert service.record_bambu_print_report(
            "owner", "A1", {"print": {"gcode_state": "RUNNING"}}, midnight
        )
        assert service.expire_printer_telemetry(
            "owner", "A1", last_seen=midnight, now=midnight + 200, timeout=90
        )
        daily = service.get_printer_daily_downtime(
            "owner", "A1", datetime.fromtimestamp(midnight + 200, timezone.utc), days=1
        )
        assert daily[0]["seconds"]["PRINTING"] == 90
        assert daily[0]["seconds"]["UNKNOWN"] == 110
