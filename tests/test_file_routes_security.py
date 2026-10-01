"""
RED regression tests for #692 — file route security defects.

Tests in this file fail on current master (defects present) and must pass
GREEN after the backend fix is applied.  Each test is annotated with the
specific defect number from the Team Lead issue-contract comment:
https://github.com/maksimp6/Chat/issues/692#issuecomment-5930830844

Refs: https://github.com/maksimp6/Chat/issues/692
"""

import os
from unittest.mock import MagicMock

import pytest
from flask import Flask

import file_routes
from file_routes import file_bp
from yandex_client_modules.errors import YandexClientError


@pytest.fixture()
def local_files_client(tmp_path, monkeypatch):
    """Flask test client with LOCAL_REPO_DIR pointing at a controlled temp root."""
    root = tmp_path / "repo_root"
    root.mkdir()
    monkeypatch.setattr(file_routes, "LOCAL_REPO_DIR", str(root))
    app = Flask(__name__)
    app.register_blueprint(file_bp)
    app.testing = True
    yield app.test_client(), root


# ---------------------------------------------------------------------------
# Traversal / path guard
# ---------------------------------------------------------------------------


def test_dotdot_traversal_returns_400(local_files_client):
    """Regression: ../ traversal must be blocked (already blocked by abspath on master)."""
    client, _ = local_files_client
    resp = client.get("/api/local-files?path=../../etc/passwd")
    assert resp.status_code == 400


def test_absolute_path_input_returns_400(local_files_client):
    """Defect (path-strip): /etc/passwd stripped to etc/passwd inside root must return 400.

    RED on current master: strip() silently accepts the input and returns 404 (not 400).
    """
    client, _ = local_files_client
    resp = client.get("/api/local-files?path=/etc/passwd")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Symlink escape — defect #1 (abspath used instead of realpath)
# ---------------------------------------------------------------------------


def test_symlink_escape_returns_400(local_files_client, tmp_path):
    """Defect #1: symlink inside root pointing outside must return 400.

    RED on current master: abspath() does not resolve symlinks so the guard
    passes and os.scandir() follows the link outside the root.
    """
    client, root = local_files_client
    outside = tmp_path / "outside_target"
    outside.mkdir()
    (root / "escape_link").symlink_to(outside)
    resp = client.get("/api/local-files?path=escape_link")
    assert resp.status_code == 400


def test_nested_symlink_escape_returns_400(local_files_client, tmp_path):
    """Defect #1 (nested): symlink nested one level inside root must also return 400.

    RED on current master for the same reason as test_symlink_escape_returns_400.
    """
    client, root = local_files_client
    outside = tmp_path / "outside_nested"
    outside.mkdir()
    inner = root / "subdir"
    inner.mkdir()
    (inner / "escape_link").symlink_to(outside)
    resp = client.get("/api/local-files?path=subdir/escape_link")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Absolute-path leak in response body — defects #2 and #3
# ---------------------------------------------------------------------------


def test_404_body_must_not_contain_server_path(local_files_client):
    """Defect #2: 404 response leaks the absolute server path.

    RED on current master: returns f'Папка не найдена: {target_dir}'.
    """
    client, root = local_files_client
    resp = client.get("/api/local-files?path=nonexistent_dir")
    assert resp.status_code == 404
    body = resp.get_data(as_text=True)
    assert str(root) not in body, "Server filesystem path must not appear in 404 response"


def test_200_body_must_not_contain_server_path(local_files_client):
    """Defect #3: 200 response leaks the absolute server path in the 'path' field.

    RED on current master: returns {"path": target_dir, ...}.
    """
    client, root = local_files_client
    allowed = root / "safe_subdir"
    allowed.mkdir()
    (allowed / "readme.txt").write_text("hello")
    resp = client.get("/api/local-files?path=safe_subdir")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert str(root) not in body, "Server filesystem path must not appear in 200 response body"


# ---------------------------------------------------------------------------
# Raw exception leak in 500 response — defect #4
# ---------------------------------------------------------------------------


def test_500_body_must_not_contain_raw_exception(local_files_client, monkeypatch):
    """Defect #4: 500 response returns str(e) verbatim.

    RED on current master: scandir exception message leaks to HTTP response body.
    """
    client, root = local_files_client
    trigger = root / "trigger_error"
    trigger.mkdir()

    secret_msg = "secret_diagnostic_path_var_internal"

    def raise_oserror(path):
        raise OSError(secret_msg)

    monkeypatch.setattr(os, "scandir", raise_oserror)
    resp = client.get("/api/local-files?path=trigger_error")
    assert resp.status_code == 500
    body = resp.get_data(as_text=True)
    assert secret_msg not in body, "Raw exception message must not appear in 500 response"


# ---------------------------------------------------------------------------
# _err_response leaks str(e) — defect #5
# ---------------------------------------------------------------------------


def test_err_response_must_not_contain_raw_yandex_exception(local_files_client, monkeypatch):
    """Defect #5: _err_response returns str(e) of YandexClientError verbatim.

    RED on current master: error detail leaks directly into the HTTP response body.
    """
    client, _ = local_files_client
    secret_detail = "internal_yandex_credential_leak_xyz"

    mock_client = MagicMock()
    mock_client.list_files.side_effect = YandexClientError(secret_detail, status_code=503)
    monkeypatch.setattr(file_routes, "get_client", lambda: mock_client)

    resp = client.get("/api/files")
    body = resp.get_data(as_text=True)
    assert secret_detail not in body, "Raw YandexClientError message must not appear in HTTP response"


# ---------------------------------------------------------------------------
# Positive: valid permitted path returns 200 with file listing (non-regression)
# ---------------------------------------------------------------------------


def test_valid_permitted_path_returns_200_with_items(local_files_client):
    """Positive: a valid subpath inside the root must return 200 with a correct listing."""
    client, root = local_files_client
    subdir = root / "valid_dir"
    subdir.mkdir()
    (subdir / "file.txt").write_text("content")
    resp = client.get("/api/local-files?path=valid_dir")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("success") is True
    names = [item["name"] for item in data.get("items", [])]
    assert "file.txt" in names
