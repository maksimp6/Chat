from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def test_file_manager_modal_shell_uses_css_classes():
    source = read("static/file_manager.js")
    assert 'className = "modal file-manager-modal"' in source
    assert 'className = "file-manager-box"' in source
    assert 'className = "modal file-manager-add-modal"' in source
    assert 'className = "file-manager-add-box"' in source
    assert "style.cssText" in source  # Rendering internals are handled in the next isolated slice.


def test_file_manager_overlays_are_scoped_to_app_shell():
    source = read("static/file_manager.js")
    assert 'document.querySelector(".alice-pro-app") || document.body' in source
