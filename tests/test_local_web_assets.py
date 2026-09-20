import os
from pathlib import Path
import re

from app import app


EXTERNAL_RESOURCE_RE = re.compile(
    r"""<(?:script|link|img|source|audio|video|iframe)\b[^>]+\b(?:src|href)\s*=\s*["'](?:https?:)?//""",
    re.IGNORECASE,
)
EXTERNAL_CSS_URL_RE = re.compile(
    r"""url\(\s*["']?(?:https?:)?//""",
    re.IGNORECASE,
)


def test_index_uses_only_local_ui_resources():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    assert not EXTERNAL_RESOURCE_RE.search(html), "UI resources must be served locally"
    assert "{% set static_root" in html
    assert 'window.__ALICE_BASE_PATH' in html
    assert 'window.__ALICE_STATIC_BASE' in html
    assert html.index('eruda.js') < html.index('eruda_init.js')


def test_index_renders_preview_prefixed_assets_and_api_paths(monkeypatch):
    old = os.environ.get("ALICE_PREVIEW_BASE_PATH")
    monkeypatch.setenv("ALICE_PREVIEW_BASE_PATH", "/preview/pr-203")
    try:
        with app.test_client() as client:
            response = client.get("/")
        html = response.get_data(as_text=True)
    finally:
        if old is None:
            os.environ.pop("ALICE_PREVIEW_BASE_PATH", None)
        else:
            os.environ["ALICE_PREVIEW_BASE_PATH"] = old

    assert response.status_code == 200
    assert 'href="/preview/pr-203/static/style.css?v=13"' in html
    assert 'src="/preview/pr-203/static/eruda.js" defer' in html
    assert 'window.__ALICE_BASE_PATH = "/preview/pr-203"' in html
    assert 'fetch("/api/memory/manage")' in html


def test_static_stylesheets_have_no_external_asset_urls():
    for css in Path("static").rglob("*.css"):
        content = css.read_text(encoding="utf-8")
        assert not EXTERNAL_CSS_URL_RE.search(content), (
            f"External CSS resource found in {css}"
        )


def test_local_eruda_loader_initializes_the_bundled_library():
    eruda = Path("static/eruda.js")
    loader = Path("static/eruda_init.js")
    assert eruda.is_file()
    assert loader.is_file()
    content = loader.read_text(encoding="utf-8")
    assert "window.eruda.init()" in content
