#!/usr/bin/env python3
"""Build, push and run Alice Pro on Cloud.ru Container Apps.

Usage:
  python scripts/cloudru_deploy.py deploy --tag <git-sha> [--env NAME ...]
  python scripts/cloudru_deploy.py status
  python scripts/cloudru_deploy.py delete --yes
  python scripts/cloudru_deploy.py estimate

Configuration (environment): CLOUDRU_IAM_KEY_ID, CLOUDRU_IAM_KEY_SECRET,
CLOUDRU_PROJECT_ID, CLOUDRU_REGISTRY_NAME, and optionally CLOUDRU_CONTAINER_NAME,
CLOUDRU_REPOSITORY_NAME, CLOUDRU_CONTAINER_CPU, CLOUDRU_MIN_INSTANCES,
CLOUDRU_MAX_INSTANCES. Output is JSON without secret values.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloud.base import CloudProviderError  # noqa: E402
from cloud.cloudru.container_apps_client import (  # noqa: E402
    CloudRuContainerAppsClient,
    ContainerSpec,
    estimate_monthly_cost,
)
from cloud.cloudru.registry_client import CloudRuRegistryClient  # noqa: E402


def _settings() -> dict:
    try:
        min_instances = int(os.getenv("CLOUDRU_MIN_INSTANCES", "0"))
        max_instances = int(os.getenv("CLOUDRU_MAX_INSTANCES", "1"))
    except ValueError as exc:
        raise CloudProviderError(
            "CLOUDRU_MIN_INSTANCES and CLOUDRU_MAX_INSTANCES must be integers",
            code="validation_error",
        ) from exc
    cfg = {
        "registry": os.getenv("CLOUDRU_REGISTRY_NAME", "alice-pro"),
        "repository": os.getenv("CLOUDRU_REPOSITORY_NAME", "alice-pro"),
        "name": os.getenv("CLOUDRU_CONTAINER_NAME", "alice-pro"),
        "cpu": os.getenv("CLOUDRU_CONTAINER_CPU", "0.5"),
        "min_instances": min_instances,
        "max_instances": max_instances,
    }
    # Validate everything up front, before any command touches the provider.
    ContainerSpec(
        name=cfg["name"],
        image="validation-only",
        cpu=cfg["cpu"],
        min_instances=min_instances,
        max_instances=max_instances,
    ).validate()
    return cfg


def _verify_commit(tag: str, context: str, runner=subprocess.run) -> None:
    """The image tag must be the full commit SHA checked out, unmodified, in the build context."""
    if not re.fullmatch(r"[0-9a-f]{40}", tag):
        raise CloudProviderError(
            "--tag must be a full 40-character commit SHA", code="validation_error"
        )

    def git(*args: str) -> str:
        done = runner(["git", "-C", context, *args], capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise CloudProviderError(
                f"build context is not a git checkout: {done.stderr.strip()}",
                code="validation_error",
            )
        return done.stdout.strip()

    head = git("rev-parse", "HEAD")
    if head != tag:
        raise CloudProviderError(
            f"--tag {tag} does not match the build context HEAD {head}", code="validation_error"
        )
    if git("status", "--porcelain", "--untracked-files=no"):
        raise CloudProviderError(
            "build context has uncommitted changes to tracked files", code="validation_error"
        )


def _emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


def cmd_deploy(args: argparse.Namespace) -> dict:
    cfg = _settings()
    missing = [name for name in args.env if name not in os.environ]
    if missing:
        raise CloudProviderError(
            f"environment variables not set: {', '.join(missing)}", code="validation_error"
        )
    _verify_commit(args.tag, args.context)
    registry = CloudRuRegistryClient()
    apps = CloudRuContainerAppsClient()

    registry.ensure_registry(cfg["registry"])
    image = registry.build_and_push(
        registry_name=cfg["registry"],
        repository=cfg["repository"],
        tag=args.tag,
        context_dir=args.context,
    )
    spec = ContainerSpec(
        name=cfg["name"],
        image=image.pinned,
        cpu=cfg["cpu"],
        min_instances=cfg["min_instances"],
        max_instances=cfg["max_instances"],
        env={name: os.environ[name] for name in args.env},
    )
    result = apps.deploy_verified(spec, timeout_s=args.timeout)
    return {
        **result,
        "image": image.pinned,
        "env_names": sorted(spec.env),
        "cost_floor": estimate_monthly_cost(spec.cpu, spec.min_instances),
    }


def cmd_status(_: argparse.Namespace) -> dict:
    cfg = _settings()
    return CloudRuContainerAppsClient().status(cfg["name"])


def cmd_delete(args: argparse.Namespace) -> dict:
    if not args.yes:
        raise CloudProviderError(
            "pass --yes to delete the container service", code="validation_error"
        )
    cfg = _settings()
    return {"deleted": cfg["name"], "operation": CloudRuContainerAppsClient().delete(cfg["name"])}


def cmd_estimate(_: argparse.Namespace) -> dict:
    cfg = _settings()
    return {
        "cpu": cfg["cpu"],
        "min_instances": cfg["min_instances"],
        "cost_floor": estimate_monthly_cost(cfg["cpu"], cfg["min_instances"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    deploy = sub.add_parser("deploy", help="build, push, deploy and health-check")
    deploy.add_argument("--tag", required=True, help="full commit SHA checked out in --context")
    deploy.add_argument("--context", default=".", help="docker build context")
    deploy.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="NAME",
        help="pass this environment variable into the container (repeatable)",
    )
    deploy.add_argument("--timeout", type=float, default=600, help="seconds to wait for readiness")
    deploy.set_defaults(func=cmd_deploy)

    sub.add_parser("status", help="show service status").set_defaults(func=cmd_status)

    delete = sub.add_parser("delete", help="delete the container service")
    delete.add_argument("--yes", action="store_true")
    delete.set_defaults(func=cmd_delete)

    sub.add_parser("estimate", help="monthly cost floor").set_defaults(func=cmd_estimate)

    args = parser.parse_args(argv)
    try:
        _emit(args.func(args))
    except CloudProviderError as exc:
        _emit({"error": exc.code, "message": exc.message, "http_status": exc.http_status})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
