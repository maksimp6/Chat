from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def test_file_manager_modal_shell_uses_css_classes():
    source = read("static/file_manager.js")
    assert 'className = "modal file-manager-modal"' in source
    assert 'className = "file-manager-box"' in source
    assert 'className = "modal file-manager-add-modal"' in source
    assert 'className = "file-manager-add-box"' in source
    assert "style.cssText" in source  # File upload rendering remains in the next isolated slice.


def test_file_manager_overlays_are_scoped_to_app_shell():
    source = read("static/file_manager.js")
    assert 'document.querySelector(".alice-pro-app") || document.body' in source


def test_vector_store_rendering_uses_owned_dom_and_css():
    source = read("static/file_manager.js")
    assert "cont.replaceChildren()" in source
    assert "file-manager-vs-row" in source
    assert "file-manager-state-error" in source
    assert "cont.innerHTML" not in source.split("function renderVsManagerList", 1)[1].split("function loadVsList", 1)[0]
