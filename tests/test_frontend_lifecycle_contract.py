from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def test_sidebar_and_models_initializers_are_idempotent():
    sidebar = read("static/sidebar.js")
    models = read("static/models.js")

    assert "function initSidebar()" in sidebar
    assert "sidebarInitialized" in sidebar
    assert "document.addEventListener(" in sidebar
    assert "function initModels()" in models
    assert "modelsInitialized" in models


def test_sidebar_and_model_rendering_avoid_presentation_inline_styles():
    sidebar = read("static/sidebar.js")
    models = read("static/models.js")

    assert 'list.innerHTML' not in sidebar
    assert 'style="' not in sidebar
    assert 'modelList.innerHTML' not in models
    assert 'style="' not in models
    assert "textContent = m.name" in models
    assert "model-option-price" in models
