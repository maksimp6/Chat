"""Alice Platform CLI."""

import argparse
import json
import sys
from pathlib import Path

from alice_platform.config import load_config, ConfigError
from alice_platform.health import generate_health_plan


def cmd_validate(args):
    """Validate config."""
    config_dir = Path(args.config_dir) if args.config_dir else Path("config/alice")

    try:
        config = load_config(config_dir)
        print(f"✓ Config valid: {config_dir}")
        print(f"  Services: {len(config.get('services', {}))}")
        print(f"  Lanes: {len(config.get('lanes', {}))}")
        return 0
    except ConfigError as e:
        print(f"✗ Config error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        return 1


def cmd_health(args):
    """Show health checks for a lane."""
    config_dir = Path(args.config_dir) if args.config_dir else Path("config/alice")

    try:
        config = load_config(config_dir)

        checks = generate_health_plan(args.lane, config)

        if not checks:
            print(f"✓ {args.lane}: no health checks (no services)")
            return 0

        print(f"Health checks for {args.lane}:")
        for check in checks:
            print(f"  {check.service}: {check.endpoint}")
            print(f"    Expected sign_in: {check.expected_sign_in}")

        return 0
    except ConfigError as e:
        print(f"✗ Config error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        return 1


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(description="Alice Platform config management")
    subparsers = parser.add_subparsers(dest="command", help="Command")

    # Validate command
    validate_parser = subparsers.add_parser("validate", help="Validate config")
    validate_parser.add_argument("--config-dir", help="Config directory (default: config/alice)")
    validate_parser.set_defaults(func=cmd_validate)

    # Health command
    health_parser = subparsers.add_parser("health", help="Show health checks")
    health_parser.add_argument("lane", help="Lane name (test, production, etc)")
    health_parser.add_argument("--config-dir", help="Config directory (default: config/alice)")
    health_parser.set_defaults(func=cmd_health)

    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
