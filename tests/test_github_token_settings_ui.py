from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "static" / "provider_credentials.js"


def test_provider_credentials_ui_exposes_github_token_save_contract():
    text = ASSET.read_text(encoding="utf-8")

    assert 'id = "provider-github-token"' not in text
    assert '"provider-github-token"' in text
    assert '"GitHub token"' in text
    assert "body.github_token = github" in text
    assert 'document.getElementById("provider-github-token").value = ""' in text
    assert 'type || "password"' in text
