from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_environment_ui_is_loaded_from_local_assets():
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "environments.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "static" / "environments.css").read_text(encoding="utf-8")
    assert "environments.js" in template
    assert "environments.css" in template
    assert "/api/environments" in script
    assert "branch_name" in script
    assert "commit_sha" in script
    assert "start" in script
    assert "stop" in script
    assert "restart" in script
    assert "delete" in script
    assert ".environment-card" in stylesheet
