from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def test_theme_and_provider_modules_do_not_own_presentation_inline_styles():
    for path in ("static/theme_assistant.js", "static/provider_credentials.js"):
        source = read(path)
        assert "style.cssText" not in source
        assert ".style.display" not in source
        assert ".style.margin" not in source


def test_provider_modal_uses_canonical_semantic_dialog_state():
    source = read("static/provider_credentials.js")
    core = read("static/core_api.js")

    assert "UI.modal.create({" in source
    assert "window.AliceCoreAPI.ui.modal.open(modal)" in source
    assert "window.AliceCoreAPI.ui.modal.close(modal)" in source
    assert ".onclick" not in source

    assert 'modal.setAttribute("role", "dialog")' in core
    assert 'modal.setAttribute("aria-modal", "true")' in core
