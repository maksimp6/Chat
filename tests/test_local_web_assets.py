import os
from pathlib import Path
import re
import shutil
import subprocess

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
    assert 'id="alice-boot"' in html
    assert 'data-base-path="{{ preview_base_path or \'\' }}"' in html
    assert 'data-static-base="{{ static_root }}"' in html
    assert html.index("boot.js") < html.index("core.js") < html.index("eruda_init.js")
    assert '<script src="{{ static_root }}/eruda.js' not in html


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
    assert 'href="/preview/pr-203/static/style.css?v=' in html
    assert 'src="/preview/pr-203/static/eruda_init.js?v=' in html
    assert '/preview/pr-203/static/eruda.js?v={{' not in html
    assert 'data-base-path="/preview/pr-203"' in html
    assert 'src="/preview/pr-203/static/memory_panel.js?v=' in html


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


def test_web_boot_and_startup_guards_are_present():
    boot = Path("static/boot.js").read_text(encoding="utf-8")
    eruda_loader = Path("static/eruda_init.js").read_text(encoding="utf-8")
    core = Path("static/core.js").read_text(encoding="utf-8")
    assert "document.currentScript" in boot
    assert "getRegistrations" in boot
    assert "alice-pro-" in boot
    assert "maxAttempts = 5" in eruda_loader
    assert "script.async = true" in eruda_loader
    assert 'script.src = (window.__ALICE_STATIC_BASE || "/static") + "/eruda.js' in eruda_loader
    assert "fetchWithTimeout" in core
    assert "AbortController" in core
    assert "setTimeout(resolve, 5000)" in core


def test_index_response_disables_shell_caching():
    with app.test_client() as client:
        response = client.get("/")
    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == "no-store, max-age=0"


def test_application_javascript_parses_when_node_is_available():
    node = shutil.which("node")
    if not node:
        return
    files = [
        Path("static/boot.js"),
        Path("static/core.js"),
        Path("static/sidebar.js"),
        Path("static/models.js"),
        Path("static/settings.js"),
        Path("static/settings/settings_storage.js"),
        Path("static/settings/settings_mcp.js"),
        Path("static/settings/settings_modal.js"),
        Path("static/eruda_init.js"),
    ]
    for path in files:
        result = subprocess.run(
            [node, "--check", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{path}: {result.stderr}"
