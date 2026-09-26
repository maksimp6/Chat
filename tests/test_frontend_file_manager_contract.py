from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def test_file_manager_modal_shell_uses_canonical_modal_api():
    source = read("static/file_manager.js")
    assert "CoreUI.modal.create({" in source
    assert 'id: "file-manager-modal"' in source
    assert 'className: "file-manager-modal"' in source
    assert 'contentClassName: "file-manager-box"' in source
    assert 'id: "file-manager-add-modal"' in source
    assert 'className: "file-manager-add-modal"' in source
    assert 'contentClassName: "file-manager-add-box"' in source
    assert "CoreUI.modal.open(" in source


def test_file_manager_overlays_are_scoped_to_app_shell():
    source = read("static/file_manager.js")
    assert 'document.querySelector(".alice-pro-app") || document.body' in source


def test_vector_store_rendering_uses_owned_dom_and_css():
    source = read("static/file_manager.js")
    assert "cont.replaceChildren()" in source
    assert "file-manager-vs-row" in source
    assert "file-manager-state-error" in source
    assert (
        "cont.innerHTML"
        not in source.split("function renderVsManagerList", 1)[1].split("function loadVsList", 1)[0]
    )


def test_vector_store_file_picker_avoids_inline_markup():
    source = read("static/file_manager.js")
    start = source.index("function openAddFilesToVsModal")
    end = source.index("// === Рендер файлов ===", start)
    section = source[start:end]
    assert "panel.innerHTML" not in section
    assert 'style="' not in section
    assert "file-manager-add-row" in section
    assert "file-manager-state" in section
