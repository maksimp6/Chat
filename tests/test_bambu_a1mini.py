"""Unit tests for safe A1 mini discovery helpers."""
import json
import struct

import pytest

from alice_pro.printers.bambu_a1mini import (
    PrinterProtocolError,
    decode_probe,
    merge_report,
    summarize_status,
)


def frame(obj):
    body = json.dumps(obj).encode()
    return b"\xa5\xa5" + struct.pack("<H", len(body) + 6) + body + b"\xa7\xa7"


def test_detect_frame():
    assert decode_probe(frame({"login": {"model": "N1"}}))["login"]["model"] == "N1"


@pytest.mark.parametrize("invalid", [
    b"", b"abcd", b"\xa5\xa5\x06\x00\xa7\x00",
    b"\xa5\xa5\x08\x00xx\xa7\xa7",
])
def test_invalid_frame(invalid):
    with pytest.raises(PrinterProtocolError):
        decode_probe(invalid)


def test_delta_merge_preserves_other_fields_and_inputs():
    old = {"print": {"gcode_state": "IDLE", "nozzle_temper": 22}}
    new = {"print": {"nozzle_temper": 23}}
    merged = merge_report(old, new)
    assert merged == {"print": {"gcode_state": "IDLE", "nozzle_temper": 23}}
    assert old["print"]["nozzle_temper"] == 22


def test_safe_summary_excludes_sensitive_data():
    report = {"print": {"gcode_state": "IDLE", "nozzle_temper": 21,
                        "serial": "SECRET", "access_code": "SECRET"}}
    assert summarize_status(report) == {"gcode_state": "IDLE", "nozzle_temper": 21}


def test_status_validation():
    with pytest.raises(ValueError):
        summarize_status({"print": "invalid"})
