"""Tests for Alice Platform CLI."""

import sys
from pathlib import Path
from unittest.mock import patch
import argparse

from alice_platform.__main__ import cmd_validate, cmd_plan, cmd_health, main


class TestCLIValidate:
    """CLI validate command tests."""

    def test_validate_command_success(self):
        """Validate command succeeds on valid config."""
        args = argparse.Namespace(config_dir="config/alice")
        result = cmd_validate(args)
        assert result == 0

    def test_validate_command_default_config_dir(self):
        """Validate command uses default config directory."""
        args = argparse.Namespace(config_dir=None)
        result = cmd_validate(args)
        assert result == 0


class TestCLIPlan:
    """CLI plan command tests."""

    def test_plan_command_test_lane(self):
        """Plan command generates plan for test lane."""
        args = argparse.Namespace(lane="test", config_dir="config/alice")
        result = cmd_plan(args)
        assert result == 0

    def test_plan_command_production_lane(self):
        """Plan command generates plan for production lane."""
        args = argparse.Namespace(lane="production", config_dir="config/alice")
        result = cmd_plan(args)
        assert result == 0

    def test_plan_command_default_config_dir(self):
        """Plan command uses default config directory."""
        args = argparse.Namespace(lane="test", config_dir=None)
        result = cmd_plan(args)
        assert result == 0


class TestCLIHealth:
    """CLI health command tests."""

    def test_health_command_test_lane(self):
        """Health command shows checks for test lane."""
        args = argparse.Namespace(lane="test", config_dir="config/alice")
        result = cmd_health(args)
        assert result == 0

    def test_health_command_production_lane(self):
        """Health command shows checks for production lane."""
        args = argparse.Namespace(lane="production", config_dir="config/alice")
        result = cmd_health(args)
        assert result == 0

    def test_health_command_default_config_dir(self):
        """Health command uses default config directory."""
        args = argparse.Namespace(lane="test", config_dir=None)
        result = cmd_health(args)
        assert result == 0


class TestMainEntry:
    """CLI main entry point tests."""

    def test_main_validate_command(self):
        """Main entry parses validate command."""
        with patch("sys.argv", ["alice_platform", "validate"]):
            result = main()
            assert result == 0

    def test_main_plan_command(self):
        """Main entry parses plan command."""
        with patch("sys.argv", ["alice_platform", "plan", "test"]):
            result = main()
            assert result == 0

    def test_main_health_command(self):
        """Main entry parses health command."""
        with patch("sys.argv", ["alice_platform", "health", "test"]):
            result = main()
            assert result == 0

    def test_main_no_args(self):
        """Main entry without args returns error."""
        with patch("sys.argv", ["alice_platform"]):
            result = main()
            assert result == 1
