from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_trace_viewer_has_correlation_navigation():
    source = (ROOT / "static" / "trace_viewer.js").read_text(encoding="utf-8")
    assert "correlation_id" in source
    assert "Связанные события" in source
    assert 'selectItem(other)' in source
