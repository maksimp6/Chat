from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def test_core_initialization_is_named_and_idempotent():
    source = read("static/core.js")

    assert "function initCore()" in source
    assert "coreInitialized" in source
    assert 'document.addEventListener("DOMContentLoaded", initCore);' in source


def test_core_remote_enhancement_isolated_from_shell_initialization():
    source = read("static/core.js")

    assert "async function enhanceCore()" in source
    assert "Network/API enhancement is deliberately separate from shell startup." in source
    assert 'localStorage.getItem("conversations")' in source


def test_core_model_ui_uses_state_classes_instead_of_inline_display():
    source = read("static/core.js")

    assert "alice-model-voice" in source
    assert "alice-model-multimodal" in source
    assert "style.display = isVoice" not in source
    assert "style.display = isMultimodal" not in source
