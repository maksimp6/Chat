import os
from pathlib import Path
import re
import shutil
import subprocess

from app import app, preview_base_path
from runtime import bind_runtime_request


EXTERNAL_RESOURCE_RE = re.compile(
    r"""<(?:script|link|img|source|audio|video|iframe)\b[^>]+\b(?:src|href)\s*=\s*["'](?:https?:)?//""",
    re.IGNORECASE,
)
EXTERNAL_CSS_URL_RE = re.compile(
    r"""url\(\s*["']?(?:https?:)?//""",
    re.IGNORECASE,
)




def test_index_renders_preview_prefixed_assets_and_api_paths(monkeypatch):
    old = os.environ.get("LOGOS_PREVIEW_BASE_PATH")
    monkeypatch.setenv("LOGOS_PREVIEW_BASE_PATH", "/preview/pr-203")
    try:
        with app.test_client() as client:
            response = client.get("/")
        html = response.get_data(as_text=True)
    finally:
        if old is None:
            os.environ.pop("LOGOS_PREVIEW_BASE_PATH", None)
        else:
            os.environ["LOGOS_PREVIEW_BASE_PATH"] = old

    assert response.status_code == 200
    assert 'href="/preview/pr-203/static/style.css?v=' in html
    assert 'data-base-path="/preview/pr-203"' in html
    assert 'src="/preview/pr-203/static/memory_panel.js?v=' in html








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
        Path("static/settings/ssh_runtime_modal.js"),
    ]
    for path in files:
        result = subprocess.run(
            [node, "--check", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{path}: {result.stderr}"


def test_preview_base_path_prefers_runtime_request_scope(monkeypatch):
    monkeypatch.setenv("LOGOS_PREVIEW_BASE_PATH", "/preview/global")

    with bind_runtime_request("runtime-a", "/environments/runtime-a"):
        assert preview_base_path() == "/environments/runtime-a"

    assert preview_base_path() == "/preview/global"
