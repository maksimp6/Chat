from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def test_cloudru_iam_uses_css_owned_modal_state():
    source = read("static/cloudru_iam.js")
    assert "style.cssText" not in source
    assert ".style.display" not in source
    assert ".onclick" not in source
    assert 'classList.add("visible")' in source
    assert 'classList.remove("visible")' in source
    assert 'role", "dialog"' in source
