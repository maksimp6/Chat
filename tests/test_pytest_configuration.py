from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_pytest_rejects_invalid_configuration_and_markers():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pytest_options = config["tool"]["pytest"]["ini_options"]

    assert pytest_options["minversion"] == "8.0"
    assert set(pytest_options["addopts"]) == {"--strict-config", "--strict-markers"}


def test_unexpected_xpass_fails_the_suite():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert config["tool"]["pytest"]["ini_options"]["xfail_strict"] is True
