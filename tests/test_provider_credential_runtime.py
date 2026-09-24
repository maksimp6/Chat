from pathlib import Path

import credential_crypto

ROOT = Path(__file__).resolve().parents[1]


def test_credential_crypto_supports_encryption_and_legacy_plaintext(monkeypatch):
    monkeypatch.setenv("ALICE_PROVIDER_CREDENTIAL_KEY", "test-deployment-secret")
    encrypted = credential_crypto.encrypt_secret("yandex-secret")
    assert encrypted != "yandex-secret"
    assert encrypted.startswith("gAAAA")
    assert credential_crypto.decrypt_secret(encrypted) == "yandex-secret"
    assert credential_crypto.decrypt_secret("legacy-plaintext-secret") == "legacy-plaintext-secret"


def test_production_runtime_requires_and_persists_provider_credential_key():
    script = (ROOT / "deploy/production/server.sh").read_text()
    assert "ALICE_PROVIDER_CREDENTIAL_KEY is required" in script
    assert "printf 'ALICE_PROVIDER_CREDENTIAL_KEY=%s\\n' \"$ALICE_PROVIDER_CREDENTIAL_KEY\"" in script


def test_preview_runtime_passes_provider_credential_key():
    script = (ROOT / "deploy/preview/server.sh").read_text()
    assert "ALICE_PROVIDER_CREDENTIAL_KEY is required" in script
    assert '-e ALICE_PROVIDER_CREDENTIAL_KEY="$ALICE_PROVIDER_CREDENTIAL_KEY"' in script


def test_deployment_workflows_forward_provider_credential_key_without_logging_it():
    production = (ROOT / ".github/workflows/production-deploy.yml").read_text()
    preview = (ROOT / ".github/workflows/preview-deploy.yml").read_text()
    for workflow in (production, preview):
        assert "ALICE_PROVIDER_CREDENTIAL_KEY: ${{ secrets.ALICE_PROVIDER_CREDENTIAL_KEY }}" in workflow
        assert 'printf \'%s\\n%s\\n\' "$ALICE_SHORT_TOKEN" "$ALICE_PROVIDER_CREDENTIAL_KEY"' in workflow
