from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


MODAL_SOURCES = {
    "templates/index.html": ("modal", "modal-content"),
    "static/project_tree.js": ("modal", "modal-content"),
    "static/treasury.js": ("modal", "modal-content"),
    "static/cloudru_iam.js": ("modal", "modal-content"),
    "static/provider_credentials.js": ("modal", "modal-content"),
    "static/file_manager.js": ("modal", "modal-content"),
}


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_all_modal_implementations_use_unified_shell():
    for path, (root_class, content_class) in MODAL_SOURCES.items():
        source = read(path)
        assert root_class in source, f"{path}: missing unified .{root_class} modal root"
        assert content_class in source, f"{path}: missing unified .{content_class} content shell"


def test_unified_modal_css_defines_shared_root_and_content_contract():
    css = read("static/style.css")
    assert ".alice-pro-app .modal {" in css
    assert ".alice-pro-app .modal.visible {" in css
    assert ".alice-pro-app .modal-content {" in css
    assert "align-items: center" in css
    assert "justify-content: center" in css


def test_feature_specific_modal_classes_extend_the_shared_shell():
    for path in MODAL_SOURCES:
        source = read(path)
        assert "modal-content" in source, f"{path}: content must extend canonical modal shell"
        if path.endswith(".js"):
            assert "className" in source and "modal" in source.lower(), f"{path}: modal root missing"
        else:
            assert 'class="modal' in source, f"{path}: modal root missing"
