from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def test_trace_viewer_lifecycle_is_idempotent_and_css_owned():
    source = read("static/trace_viewer_auto.js")
    assert "var observer = null;" in source
    assert "if (observer) return;" in source
    assert "style.cssText" not in source
    assert "details.style." not in source
    assert "button.style." not in source
    assert "trace-viewer-auto-button" in source
    assert "trace-viewer-auto-details" in source


def test_android_diagnostics_initialization_is_idempotent():
    source = read("static/android_diagnostics.js")
    assert "function initDiagnostics()" in source
    assert "androidDiagnosticsInitialized" in source
    assert 'addEventListener("click"' in source
    assert ".onclick =" not in source
