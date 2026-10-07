from __future__ import annotations

from scripts.check_shell_env_reference import discover_external_shell_env


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
