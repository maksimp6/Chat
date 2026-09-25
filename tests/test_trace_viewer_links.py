from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_trace_viewer_has_correlation_navigation():
    source = (ROOT / "static" / "trace_viewer.js").read_text(encoding="utf-8")
    assert "correlation_id" in source
    assert "Связанные события" in source
    assert "selectItem(other)" in source


def test_chat_has_direct_trace_viewer_action():
    source = (ROOT / "static" / "chat.js").read_text(encoding="utf-8")
    assert "alice-trace-open-viewer" in source
    assert "window.openTraceViewer(traceObj)" in source
    assert "traceViewerDirect" in source


def test_trace_viewer_auto_preserves_direct_chat_action():
    source = (ROOT / "static" / "trace_viewer_auto.js").read_text(encoding="utf-8")
    assert 'details.dataset.traceViewerDirect === "1"' in source
