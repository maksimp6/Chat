from pathlib import Path

import pytest

from alice_platform.config import PlatformConfigError, load_platform_config


ROOT = Path(__file__).parents[1]


def test_repository_platform_config_is_valid():
    config = load_platform_config(ROOT)
    assert set(config.services) == {"oauth", "chrome", "agent-shell"}
    assert config.lanes["production"]["sign_in"] == "github"
    assert config.lanes["test"]["sign_in"] == "passphrase"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("unknown_field", "schema_invalid"),
        ("missing_dependency", "dependency_missing"),
        ("dependency_cycle", "dependency_cycle"),
        ("plaintext_secret", "secret_plaintext"),
        ("production_http", "production_url_invalid"),
        ("unattached_domain", "domain_service_missing"),
        ("shared_secret_ref", "secret_ref_shared"),
        ("docs_missing_service", "docs_service_mismatch"),
    ],
)
def test_invalid_platform_contract_fails_with_stable_code(tmp_path, mutation, code):
    source = ROOT / "config" / "alice"
    docs = ROOT / "docs" / "platform"
    target = tmp_path / "repo"
    import shutil

    shutil.copytree(source, target / "config" / "alice")
    shutil.copytree(docs, target / "docs" / "platform")
    mutate_fixture(target, mutation)
    with pytest.raises(PlatformConfigError) as exc:
        load_platform_config(target)
    assert exc.value.code == code


def mutate_fixture(root, mutation):
    import yaml

    def edit(name, fn):
        path = root / "config" / "alice" / name
        value = yaml.safe_load(path.read_text())
        fn(value)
        path.write_text(yaml.safe_dump(value, sort_keys=False))

    if mutation == "unknown_field":
        edit("platform.yaml", lambda v: v.update({"surprise": True}))
    elif mutation == "missing_dependency":
        edit("platform.yaml", lambda v: v["services"]["chrome"].update({"depends_on": ["ghost"]}))
    elif mutation == "dependency_cycle":
        edit("platform.yaml", lambda v: v["services"]["oauth"].update({"depends_on": ["chrome"]}))
    elif mutation == "plaintext_secret":
        edit("secrets.yaml", lambda v: v["production"].update({"bad": "-----BEGIN PRIVATE KEY-----"}))
    elif mutation == "production_http":
        edit("production.yaml", lambda v: v["services"]["oauth"].update({"url": "http://oauth.maxxxpavlov.online"}))
    elif mutation == "unattached_domain":
        edit("domains.yaml", lambda v: v["domains"][0].update({"service": "ghost"}))
    elif mutation == "shared_secret_ref":
        def share(v):
            v["test"]["oauth"] = v["production"]["oauth"]
        edit("secrets.yaml", share)
    elif mutation == "docs_missing_service":
        path = root / "docs" / "platform" / "architecture.md"
        path.write_text(path.read_text().replace("## agent-shell\n", ""))
