"""Loaded build evidence and content fingerprinting, without model requests."""
from pathlib import Path

import app as app_module


def test_version_is_boot_snapshot_not_mutable_environment(monkeypatch):
    expected = dict(app_module.APP_VERSION)
    monkeypatch.setenv("LOGOS_APP_REVISION", "f" * 40)
    monkeypatch.setenv("LOGOS_STATIC_VERSION", "stale-manual-value")
    monkeypatch.setattr(app_module, "_storage_ready", False)
    monkeypatch.setattr(app_module, "_initialize_storage", lambda: (_ for _ in ()).throw(AssertionError("version read initialized storage")))
    response = app_module.app.test_client().get("/api/version")
    assert response.status_code == 200
    assert response.get_json() == expected
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["process_id"] > 0


def test_fingerprint_changes_for_ui_bytes_and_template_not_file_times(tmp_path, monkeypatch):
    (tmp_path / "static").mkdir()
    (tmp_path / "templates").mkdir()
    script = tmp_path / "static/chat.js"
    template = tmp_path / "templates/index.html"
    script.write_text("old UI")
    template.write_text("<html>old</html>")
    original = app_module._static_asset_version(tmp_path)
    script.touch()
    monkeypatch.setenv("LOGOS_STATIC_VERSION", "old-static-override")
    assert app_module._static_asset_version(tmp_path) == original
    script.write_text("new UI")
    changed_script = app_module._static_asset_version(tmp_path)
    assert changed_script != original
    template.write_text("<html>new</html>")
    assert app_module._static_asset_version(tmp_path) != changed_script


def test_rendered_page_exposes_the_loaded_revision_and_content_version(monkeypatch):
    monkeypatch.setattr(app_module, "_storage_ready", True)
    response = app_module.app.test_client().get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="app-version"' in html
    assert 'data-app-revision="' + app_module.APP_VERSION["runtime_revision"] + '"' in html
    assert 'data-static-version="' + app_module.APP_VERSION["ui_version"] + '"' in html
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
