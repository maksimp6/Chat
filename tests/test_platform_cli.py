"""Tests for the fail-closed Alice Platform CLI."""

import argparse
from unittest.mock import patch

import pytest

from alice_platform.__main__ import cmd_health, cmd_validate, main


class TestCLIValidate:
    """CLI validate command tests."""

    def test_validate_command_success(self):
        args = argparse.Namespace(config_dir="config/alice")
        assert cmd_validate(args) == 0

    def test_validate_command_default_config_dir(self):
        args = argparse.Namespace(config_dir=None)
        assert cmd_validate(args) == 0


class TestCLIHealth:
    """CLI health command tests."""

    def test_health_command_test_lane(self):
        args = argparse.Namespace(lane="test", config_dir="config/alice")
        assert cmd_health(args) == 0

    def test_health_command_production_lane(self):
        args = argparse.Namespace(lane="production", config_dir="config/alice")
        assert cmd_health(args) == 0

    def test_health_command_default_config_dir(self):
        args = argparse.Namespace(lane="test", config_dir=None)
        assert cmd_health(args) == 0


class TestMainEntry:
    """CLI main entry point tests."""

    def test_main_validate_command(self):
        with patch("sys.argv", ["alice_platform", "validate"]):
            assert main() == 0

    def test_main_health_command(self):
        with patch("sys.argv", ["alice_platform", "health", "test"]):
            assert main() == 0

    @pytest.mark.parametrize("command", ["plan", "reconcile"])
    def test_unobserved_mutation_commands_are_not_exposed(self, command):
        with patch("sys.argv", ["alice_platform", command, "test"]):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 2

    def test_main_no_args(self):
        with patch("sys.argv", ["alice_platform"]):
            assert main() == 1
