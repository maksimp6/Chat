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
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloud.base import CloudProviderError  # noqa: E402
from cloud.cloudru.container_apps_client import (  # noqa: E402
    CloudRuContainerAppsClient,
    ContainerSpec,
    estimate_monthly_cost,
)
from cloud.cloudru.registry_client import CloudRuRegistryClient, validate_name  # noqa: E402


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
    validate_name(cfg["registry"], "CLOUDRU_REGISTRY_NAME")
    validate_name(cfg["repository"], "CLOUDRU_REPOSITORY_NAME")
    ContainerSpec(
        name=cfg["name"],
        image="validation-only",
        cpu=cfg["cpu"],
        min_instances=min_instances,
        max_instances=max_instances,
    ).validate()
    return cfg


def _export_commit(tag: str, repo: str, dest: str, runner=subprocess.run) -> None:
    """Write exactly the files of commit ``tag`` to ``dest``, so the image is that commit.

    Building from an export (not the working tree) keeps uncommitted, untracked and
    ignored files such as ``.env`` out of the image.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", tag):
        raise CloudProviderError(
            "--tag must be a full 40-character commit SHA", code="validation_error"
        )
    done = runner(
        ["git", "-C", repo, "archive", "--format=tar", tag], capture_output=True, check=False
    )
    if done.returncode != 0:
        raise CloudProviderError(
            f"cannot export commit {tag}: {done.stderr.decode(errors='replace').strip()[:300]}",
            code="validation_error",
        )
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
        archive.extractall(dest, filter="data")


def _emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


REQUIRED_APP_ENV = ("ALICE_REQUIRE_SHORT_TOKEN", "ALICE_SHORT_TOKEN")


def cmd_deploy(args: argparse.Namespace) -> dict:
    cfg = _settings()
    missing = [name for name in args.env if name not in os.environ]
    if missing:
        raise CloudProviderError(
            f"environment variables not set: {', '.join(missing)}", code="validation_error"
        )
    # The service is public, so it must never start without the short-token gate.
    if (
        any(name not in args.env for name in REQUIRED_APP_ENV)
        or os.environ["ALICE_REQUIRE_SHORT_TOKEN"] != "1"
        or not os.environ["ALICE_SHORT_TOKEN"]
    ):
        raise CloudProviderError(
            "deploy needs --env ALICE_REQUIRE_SHORT_TOKEN (set to 1) and --env ALICE_SHORT_TOKEN",
            code="validation_error",
        )
    registry = CloudRuRegistryClient()
    apps = CloudRuContainerAppsClient()

    with tempfile.TemporaryDirectory(prefix="alice-build-") as build_dir:
        _export_commit(args.tag, args.context, build_dir)
        registry.ensure_registry(cfg["registry"])
        image = registry.build_and_push(
            registry_name=cfg["registry"],
            repository=cfg["repository"],
            tag=args.tag,
            context_dir=build_dir,
            dockerfile=os.path.join(build_dir, "Dockerfile"),
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
    deploy.add_argument(
        "--tag", required=True, help="full commit SHA to build (exported from --context)"
    )
    deploy.add_argument("--context", default=".", help="git repository holding the commit")
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
