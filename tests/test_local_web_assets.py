from pathlib import Path
import re


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
    assert '<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">' in html
    assert '<script src="/static/eruda.js" defer></script>' in html
    assert '<script src="/static/eruda_init.js" defer></script>' in html
    assert html.index('/static/eruda.js') < html.index('/static/eruda_init.js')


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
