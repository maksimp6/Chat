from __future__ import annotations

import json
from pathlib import Path

from scripts.check_shell_env_reference import discover_external_shell_env, discover_repository_shell_env

ROOT = Path(__file__).resolve().parents[1]


def test_discovers_reads_not_locally_assigned_variables():
    script = """
set -euo pipefail
LOCAL=value
OTHER="$LOCAL"
echo "$ALICE_TOKEN"
echo "${CLOUDRU_PROJECT_ID:-}"
"""
    assert discover_external_shell_env(script) == {"ALICE_TOKEN", "CLOUDRU_PROJECT_ID"}


def test_treats_export_assignment_as_local_not_external():
    script = """
export LOCAL_TOKEN=value
printf '%s' "$LOCAL_TOKEN"
printf '%s' "$HOME"
"""
    assert discover_external_shell_env(script) == {"HOME"}


def test_ignores_positional_special_and_lowercase_shell_variables():
    script = """
name=value
echo "$name" "$1" "$@" "$?" "$$"
"""
    assert discover_external_shell_env(script) == set()


def test_shell_environment_inputs_are_in_canonical_reference():
    reference = json.loads(
        (ROOT / "docs" / "configuration" / "environment-variables.json").read_text(
            encoding="utf-8"
        )
    ) if (ROOT / "docs" / "configuration" / "environment-variables.json").exists() else {}
    missing = sorted(discover_repository_shell_env(ROOT) - set(reference))
    assert missing == []
