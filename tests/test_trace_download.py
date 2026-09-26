from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "static" / "trace_download.js").read_text(encoding="utf-8")


def test_trace_download_has_mobile_safe_state_transitions():
    assert 'setButtonState(button, "⏳", true)' in SOURCE
    assert 'setButtonState(button, "✓ JSON", true)' in SOURCE
    assert 'setButtonState(button, "⚠ JSON", true)' in SOURCE
    assert "download = traceFilename()" in SOURCE


def test_trace_export_redacts_sensitive_fields_and_uses_json():
    assert re.search(r"api\[_-\]\?key|authorization|token|secret|password", SOURCE, re.I)
    assert "application/json" in SOURCE
    assert "link.download = traceFilename()" in SOURCE


def test_trace_download_is_local_frontend_code():
    assert "http://" not in SOURCE
    assert "https://" not in SOURCE
    assert "/api/files" in SOURCE
