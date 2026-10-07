from __future__ import annotations

from pathlib import Path

from scripts.check_env_reference import discover_python_env, validate_reference


def test_discovers_static_python_environment_reads(tmp_path):
    source = tmp_path / "app.py"
    source.write_text(
        'import os\n'
        'A = os.getenv("ALICE_A", "x")\n'
        'B = os.environ.get("ALICE_B")\n'
        'C = os.environ["ALICE_C"]\n',
        encoding="utf-8",
    )
    assert discover_python_env([source]) == {"ALICE_A", "ALICE_B", "ALICE_C"}


def test_ignores_dynamic_environment_names(tmp_path):
    source = tmp_path / "app.py"
    source.write_text(
        'import os\nname = "ALICE_" + "DYNAMIC"\nos.getenv(name)\n',
        encoding="utf-8",
    )
    assert discover_python_env([source]) == set()


def test_reference_requires_every_discovered_key_and_valid_classification():
    discovered = {"ALICE_PUBLIC", "ALICE_SECRET"}
    reference = {
        "ALICE_PUBLIC": {"class": "public", "purpose": "port"},
        "ALICE_SECRET": {"class": "secret", "purpose": "credential"},
    }
    assert validate_reference(discovered, reference) == []

    errors = validate_reference(discovered | {"ALICE_NEW"}, reference)
    assert errors == ["ALICE_NEW: missing from environment variable reference"]


def test_reference_never_contains_secret_values():
    discovered = {"TOKEN"}
    reference = {
        "TOKEN": {
            "class": "secret",
            "purpose": "bootstrap credential",
            "value": "must-not-be-here",
        }
    }
    errors = validate_reference(discovered, reference)
    assert errors == ["TOKEN: reference entry must not contain a value field"]
