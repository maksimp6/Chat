#!/usr/bin/env python3
"""Build, push and run Alice Pro on Cloud.ru Container Apps.

Usage:
  python scripts/cloudru_deploy.py deploy --tag <git-sha> [--env NAME ...]
  python scripts/cloudru_deploy.py status
  python scripts/cloudru_deploy.py inventory
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
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloud.base import CloudProviderError  # noqa: E402
from cloud.cloudru.container_apps_client import (  # noqa: E402
    CloudRuContainerAppsClient,
    ContainerSpec,
    estimate_monthly_cost,
)
from cloud.cloudru.registry_client import CloudRuRegistryClient, validate_name  # noqa: E402


CODEX_CLOUDRU_STATE_FILE = Path.home() / ".local" / "state" / "alice-pro" / "cloudru-codex.json"


def _load_cloudru_credentials() -> None:
    """Load the protected Codex hand-off without printing credential values.

    Codex removes environment secrets before the agent phase. The setup script
    writes only this exact Cloud.ru pair (and the non-secret project ID) to a
    mode-600 file outside the checkout. Explicit process environment values
    take precedence, while the historical ``CLOUDRU_KEY_*`` names remain
    accepted for direct invocations.
    """
    state_file = CODEX_CLOUDRU_STATE_FILE
    if state_file.exists():
        try:
            mode = state_file.stat().st_mode & 0o777
            if mode & 0o077:
                raise CloudProviderError(
                    "Cloud.ru credential cache has unsafe permissions",
                    code="validation_error",
                )
            payload = json.loads(state_file.read_text(encoding="utf-8"))
        except CloudProviderError:
            raise
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise CloudProviderError(
                "Cloud.ru credential cache is unreadable",
                code="validation_error",
            ) from exc
        if not isinstance(payload, dict):
            raise CloudProviderError(
                "Cloud.ru credential cache is invalid", code="validation_error"
            )
        for name in ("CLOUDRU_IAM_KEY_ID", "CLOUDRU_IAM_KEY_SECRET", "CLOUDRU_PROJECT_ID"):
            value = payload.get(name)
            if not os.environ.get(name) and isinstance(value, str) and value:
                os.environ[name] = value

    if not os.environ.get("CLOUDRU_IAM_KEY_ID"):
        legacy_key_id = os.environ.get("CLOUDRU_KEY_ID", "").strip()
        if legacy_key_id:
            os.environ["CLOUDRU_IAM_KEY_ID"] = legacy_key_id
    if not os.environ.get("CLOUDRU_IAM_KEY_SECRET"):
        legacy_key_secret = os.environ.get("CLOUDRU_KEY_SECRET", "").strip()
        if legacy_key_secret:
            os.environ["CLOUDRU_IAM_KEY_SECRET"] = legacy_key_secret


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
    # Refresh the canonical branch for direct CLI invocations too. Never trust
    # an arbitrary local branch or a stale origin/master as deployment approval.
    fetched = runner(
        ["git", "-C", repo, "fetch", "--no-tags", "origin", "master"],
        capture_output=True,
        check=False,
    )
    if fetched.returncode != 0:
        raise CloudProviderError("cannot fetch canonical master", code="validation_error")
    trusted = runner(
        ["git", "-C", repo, "merge-base", "--is-ancestor", tag, "FETCH_HEAD"],
        capture_output=True,
        check=False,
    )
    if trusted.returncode != 0:
        raise CloudProviderError("commit is not on canonical master", code="validation_error")
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
CONTROL_PLANE_ENV = frozenset(
    {
        "CLOUDRU_IAM_KEY_ID",
        "CLOUDRU_IAM_KEY_SECRET",
        "CLOUDRU_API_KEY",
        "CLOUDRU_KEY_ID",
        "CLOUDRU_KEY_SECRET",
        "GITHUB_TOKEN",
        "GH_TOKEN",
    }
)


def cmd_deploy(args: argparse.Namespace) -> dict:
    cfg = _settings()
    if CONTROL_PLANE_ENV.intersection(args.env):
        raise CloudProviderError(
            "control-plane credentials cannot be passed to the application",
            code="validation_error",
        )
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
    database_url = os.environ.get("ALICE_DATABASE_URL", "")
    if "ALICE_DATABASE_URL" not in args.env or not database_url.startswith(
        ("postgres://", "postgresql://")
    ):
        raise CloudProviderError(
            "deploy needs --env ALICE_DATABASE_URL set to durable PostgreSQL",
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


def cmd_inventory(_: argparse.Namespace) -> dict:
    """Check project-level access without assuming a container already exists."""
    cfg = _settings()
    items = CloudRuContainerAppsClient().list(order_by="name", require_total=True)
    names = [item.get("name") for item in items]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(
        names
    ):
        raise CloudProviderError("Invalid container inventory", code="invalid_response")
    # Do not publish provider metadata, environment values or even resource names.
    return {
        "status": "INVENTORY_COMPLETE",
        "container_count": len(items),
        "configured_container_exists": cfg["name"] in names,
    }


def cmd_delete(args: argparse.Namespace) -> dict:
    if not args.yes:
        raise CloudProviderError(
            "pass --yes to delete the container service", code="validation_error"
        )
    cfg = _settings()
    return {"deleted": cfg["name"], "operation": CloudRuContainerAppsClient().delete(cfg["name"])}


def cmd_verify_persistence(_: argparse.Namespace) -> dict:
    """Verify durable test state across a new Container Apps revision.

    Uses the existing conv_settings storage contract as a sentinel. The value is
    intentionally non-secret and is removed after verification.
    """
    cfg = _settings()
    if cfg["name"] != os.getenv("CLOUDRU_TEST_CONTAINER_NAME", "alice-test"):
        raise CloudProviderError(
            "persistence verification is restricted to alice-test",
            code="validation_error",
        )
    database_url = os.getenv("ALICE_DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise CloudProviderError(
            "ALICE_DATABASE_URL must select test PostgreSQL",
            code="validation_error",
        )

    from db import get_conn, init_db

    init_db()
    sentinel_id = "ci-persistence-" + uuid.uuid4().hex
    payload = json.dumps({"run_id": sentinel_id}, sort_keys=True)
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO conv_settings (conversation_id, settings_json, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP)",
            (sentinel_id, payload),
        )
        conn.commit()
    finally:
        conn.close()

    apps = CloudRuContainerAppsClient()
    before = apps.status(cfg["name"])
    app = apps.get(cfg["name"])
    if not app:
        raise CloudProviderError("alice-test is not deployed", code="not_found")
    image = _image_of_deployed_app(app)
    if not image:
        raise CloudProviderError("alice-test image is unavailable", code="invalid_response")
    spec = ContainerSpec(
        name=cfg["name"],
        image=image,
        cpu=cfg["cpu"],
        min_instances=cfg["min_instances"],
        max_instances=cfg["max_instances"],
        env=_deployed_env(app),
    )
    restarted = apps.deploy_verified(spec)

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT settings_json FROM conv_settings WHERE conversation_id = ?",
            (sentinel_id,),
        ).fetchone()
        if row is None or row["settings_json"] != payload:
            raise CloudProviderError(
                "persistence sentinel missing after revision",
                code="persistence_failed",
            )
        conn.execute("DELETE FROM conv_settings WHERE conversation_id = ?", (sentinel_id,))
        conn.commit()
    finally:
        conn.close()
    return {
        "status": "PERSISTENCE_VERIFIED",
        "container": cfg["name"],
        "before_status": before.get("status"),
        "revision": restarted.get("revision"),
        "health": restarted.get("health"),
    }


def _image_of_deployed_app(app: dict) -> str | None:
    containers = (app.get("template") or {}).get("containers") or [{}]
    return containers[0].get("image")


def _deployed_env(app: dict) -> dict[str, str]:
    containers = (app.get("template") or {}).get("containers") or [{}]
    items = containers[0].get("env") or []
    result = {}
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("value"), str):
            result[item["name"]] = item["value"]
    return result


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
    sub.add_parser("inventory", help="check project-level container access").set_defaults(
        func=cmd_inventory
    )

    delete = sub.add_parser("delete", help="delete the container service")
    delete.add_argument("--yes", action="store_true")
    delete.set_defaults(func=cmd_delete)

    sub.add_parser("estimate", help="monthly cost floor").set_defaults(func=cmd_estimate)
    sub.add_parser(
        "verify-persistence",
        help="verify alice-test PostgreSQL state across a new revision",
    ).set_defaults(func=cmd_verify_persistence)

    args = parser.parse_args(argv)
    try:
        _load_cloudru_credentials()
        _emit(args.func(args))
    except CloudProviderError as exc:
        _emit({"error": exc.code, "message": exc.message, "http_status": exc.http_status})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
