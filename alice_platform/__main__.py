"""Alice Platform CLI."""

import argparse
import json
import sys
from pathlib import Path

from alice_platform.config import load_config, ConfigError
from alice_platform.planner import plan_actions
from alice_platform.health import generate_health_plan
from alice_platform.reconciler import reconcile
from alice_platform.billing import calculate_lane_cost
from alice_platform.providers.secrets import get_secret


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


def cmd_plan(args):
    """Show plan for a lane."""
    config_dir = Path(args.config_dir) if args.config_dir else Path("config/alice")

    try:
        config = load_config(config_dir)

        # For now, use empty observed state
        # In production, this would fetch from cloud provider
        observed = {"containers": []}

        actions = plan_actions(args.lane, config, observed)

        if not actions:
            print(f"✓ {args.lane}: no changes needed")
            return 0

        print(f"Plan for {args.lane}:")
        for i, action in enumerate(actions, 1):
            approval = "🔒 needs approval" if action.needs_approval else ""
            print(f"  {i}. [{action.type.value}] {action.service}: {action.description} {approval}")

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


def cmd_reconcile(args):
    """Apply planned actions to reconcile reality to desired state."""
    config_dir = Path(args.config_dir) if args.config_dir else Path("config/alice")

    try:
        config = load_config(config_dir)

        # For now, use empty observed state (dry-run)
        observed = {"containers": []}

        actions = plan_actions(args.lane, config, observed)

        if not actions:
            print(f"✓ {args.lane}: no changes needed")
            return 0

        print(f"Applying plan for {args.lane}:")
        reconcile(args.lane, actions, config)
        print(f"✓ Reconciliation complete (dry-run mode)")

        return 0
    except ConfigError as e:
        print(f"✗ Config error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        return 1


def cmd_billing(args):
    """Show billing information for a lane."""
    config_dir = Path(args.config_dir) if args.config_dir else Path("config/alice")

    try:
        config = load_config(config_dir)

        result = calculate_lane_cost(args.lane, config)

        print(f"Billing for {args.lane}:")
        print(f"  Total: {result['total_rub_per_month']:.2f} RUB/month")
        print(f"  Services: {result['services']}")

        if result["breakdown"]:
            print(f"  Breakdown:")
            for billing in result["breakdown"]:
                gpu_str = f" + GPU {billing.gpu_cost_rub:.2f}" if billing.gpu_cost_rub else ""
                print(f"    {billing.service}: {billing.base_cost_rub:.2f} RUB/month{gpu_str}")

        return 0
    except ConfigError as e:
        print(f"✗ Config error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        return 1


def cmd_secret(args):
    """Fetch a secret from Cloud.ru Secret Management."""
    try:
        secret_value = get_secret(args.path)
        print(f"✓ Secret retrieved: {args.path}")
        print(f"  Value length: {len(secret_value)} chars")
        return 0
    except Exception as e:
        print(f"✗ Error retrieving secret: {e}", file=sys.stderr)
        return 1


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(description="Alice Platform config management")
    subparsers = parser.add_subparsers(dest="command", help="Command")

    # Validate command
    validate_parser = subparsers.add_parser("validate", help="Validate config")
    validate_parser.add_argument("--config-dir", help="Config directory (default: config/alice)")
    validate_parser.set_defaults(func=cmd_validate)

    # Plan command
    plan_parser = subparsers.add_parser("plan", help="Show plan for a lane")
    plan_parser.add_argument("lane", help="Lane name (test, production, etc)")
    plan_parser.add_argument("--config-dir", help="Config directory (default: config/alice)")
    plan_parser.set_defaults(func=cmd_plan)

    # Health command
    health_parser = subparsers.add_parser("health", help="Show health checks")
    health_parser.add_argument("lane", help="Lane name (test, production, etc)")
    health_parser.add_argument("--config-dir", help="Config directory (default: config/alice)")
    health_parser.set_defaults(func=cmd_health)

    # Reconcile command
    reconcile_parser = subparsers.add_parser("reconcile", help="Apply planned actions")
    reconcile_parser.add_argument("lane", help="Lane name (test, production, etc)")
    reconcile_parser.add_argument("--config-dir", help="Config directory (default: config/alice)")
    reconcile_parser.set_defaults(func=cmd_reconcile)

    # Billing command
    billing_parser = subparsers.add_parser("billing", help="Show billing information")
    billing_parser.add_argument("lane", help="Lane name (test, production, etc)")
    billing_parser.add_argument("--config-dir", help="Config directory (default: config/alice)")
    billing_parser.set_defaults(func=cmd_billing)

    # Secret command
    secret_parser = subparsers.add_parser("secret", help="Fetch a secret from Cloud.ru")
    secret_parser.add_argument("path", help="Secret path (e.g., alice/prod/oauth-client-secret)")
    secret_parser.set_defaults(func=cmd_secret)

    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
