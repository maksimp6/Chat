from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def test_cloudru_iam_uses_canonical_modal_api():
    source = read("static/cloudru_iam.js")
    core = read("static/core_api.js")

    assert "style.cssText" not in source
    assert ".style.display" not in source
    assert ".onclick" not in source
    assert "UI.modal.create({" in source
    assert "window.AliceCoreAPI.ui.modal.open(modal)" in source
    assert "window.AliceCoreAPI.ui.modal.close(modal)" in source

    assert 'modal.setAttribute("role", "dialog")' in core
    assert 'modal.setAttribute("aria-modal", "true")' in core
    assert 'modal.classList.add("visible")' in core
    assert 'modal.classList.remove("visible")' in core
