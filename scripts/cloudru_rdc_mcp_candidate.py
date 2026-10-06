#!/usr/bin/env python3
"""Deploy an isolated lightweight Alice Dev candidate without touching persistent RDC."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloud.base import CloudProviderError  # noqa: E402
from cloud.cloudru.container_apps_client import (
    SERVICE,
    CloudRuContainerAppsClient,
    ContainerSpec,
)
from cloud.cloudru.registry_client import CloudRuRegistryClient
from scripts.cloudru_browser_probe import REGISTRY, prepare_registry
from scripts.cloudru_deploy import _export_commit

REPOSITORY = "alice-dev"
DESCRIPTION = "Alice lightweight development worker; issue 906"
TOMBSTONES = Path(__file__).resolve().parents[1] / "config" / "alice" / "alice-dev-delete-ids.txt"


def fail(code: str) -> None:
    raise CloudProviderError("Alice Dev candidate operation failed", code=code)


def project_id() -> str:
    raw = os.getenv("CLOUDRU_PROJECT_ID", "").strip()
    try:
        return str(UUID(raw))
    except (ValueError, TypeError, AttributeError):
        fail("validation_error")


def candidate_name(project: str, sha: str) -> str:
    if len(sha) != 40 or any(ch not in "0123456789abcdef" for ch in sha):
        fail("validation_error")
    return f"alice-dev-{UUID(project).hex[:12]}-{sha[:8]}"


def tombstone_ids(path: Path | None = None) -> set[str]:
    ids: set[str] = set()
    source = path or TOMBSTONES
    for raw in source.read_text(encoding="utf-8").splitlines():
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        try:
            ids.add(str(UUID(value)))
        except ValueError:
            fail("invalid_tombstone")
    return ids


def alice_dev_inventory(apps: CloudRuContainerAppsClient) -> list[dict]:
    result = []
    for item in apps.list(require_total=True):
        name = item.get("name")
        if not isinstance(name, str) or not name.startswith("alice-dev-"):
            continue
        ingress = (item.get("configuration") or {}).get("ingress") or {}
        containers = (item.get("template") or {}).get("containers") or [{}]
        result.append(
            {
                "id": item.get("id"),
                "name": name,
                "origin": (f"https://{ingress['publicUri']}" if ingress.get("publicUri") else None),
                "image": containers[0].get("image"),
                "status": item.get("status"),
            }
        )
    return sorted(result, key=lambda item: item["name"])


def purge_tombstones(apps: CloudRuContainerAppsClient) -> list[str]:
    forbidden = tombstone_ids()
    inventory = apps.list(require_total=True)

    legacy_name = f"alice-dev-{UUID(project_id()).hex[:12]}"
    legacy = [item for item in inventory if item.get("name") == legacy_name]
    if len(legacy) > 1:
        fail("invalid_response")
    if legacy and legacy[0].get("id"):
        forbidden.add(str(UUID(str(legacy[0]["id"]))))
        print(
            json.dumps(
                {
                    "status": "LEGACY_TOMBSTONE_RESOLVED",
                    "container_id": str(UUID(str(legacy[0]["id"]))),
                    "name": legacy_name,
                },
                sort_keys=True,
            ),
            flush=True,
        )
    by_id = {str(item.get("id")): item for item in inventory if item.get("id")}
    deleted: list[str] = []
    for identifier in sorted(forbidden):
        item = by_id.get(identifier)
        if item is None:
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            fail("invalid_response")
        apps.delete(name)
        deleted.append(identifier)

    if deleted:
        deadline = time.monotonic() + 90
        while True:
            remaining = {
                str(item.get("id")) for item in apps.list(require_total=True) if item.get("id")
            }
            if not (forbidden & remaining):
                break
            if time.monotonic() >= deadline:
                fail("tombstone_survived")
            time.sleep(3)
    return deleted


def build_image(root: Path, sha: str) -> str:
    registry = CloudRuRegistryClient()
    with tempfile.TemporaryDirectory(prefix="alice-dev-") as exported:
        _export_commit(sha, str(root), exported)
        prepare_registry(registry)
        context = Path(exported) / "deploy" / "remote-desktop-commander"
        return registry.build_and_push(
            registry_name=REGISTRY,
            repository=REPOSITORY,
            tag=sha,
            context_dir=str(context),
            dockerfile=str(context / "Dockerfile.mcp"),
        ).pinned


def create_candidate(apps: CloudRuContainerAppsClient, image: str, token: str, sha: str) -> dict:
    project = project_id()
    name = candidate_name(project, sha)
    current = apps.find_for_deploy(name)
    spec = ContainerSpec(
        name=name,
        image=image,
        cpu="0.2",
        min_instances=0,
        max_instances=1,
        public=True,
        description=DESCRIPTION,
        idle_timeout="10s",
        env={
            "ALICE_SHORT_TOKEN": token,
        },
    )
    spec.validate()
    body = {
        "name": name,
        "projectId": project,
        "description": DESCRIPTION,
        "configuration": {
            "ingress": {
                "publiclyAccessible": True,
                "accessSettings": {"enableAuth": False},
            },
            "autoDeployments": {"enabled": False},
        },
        "template": {
            "timeout": spec.timeout,
            "idleTimeout": spec.idle_timeout,
            "protocol": spec.protocol,
            "scaling": {
                "minInstanceCount": spec.min_instances,
                "maxInstanceCount": spec.max_instances,
            },
            "containers": [spec.container_body()],
        },
    }
    if current is None:
        apps.client.request(SERVICE, "POST", "/v2/containers", json_body=body)
    else:
        if current.get("name") not in {None, name}:
            fail("resource_mismatch")
        if current.get("description") not in {None, DESCRIPTION}:
            fail("resource_mismatch")
        apps.update_from_current(spec, current)
    status = apps.wait_until_ready(name, image=image, timeout_s=300, poll_s=5)
    health = apps.health_check(status["public_uri"], attempts=18, delay_s=5)
    origin = health["url"].removesuffix("/healthz")
    return {
        "status": "ALICE_DEV_READY",
        "name": name,
        "origin": origin,
        "container_id": status.get("id"),
        "image": image,
        "resources": status.get("resources"),
        "scaling": status.get("scaling"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["deploy"])
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    if not len(args.sha) == 40 or any(ch not in "0123456789abcdef" for ch in args.sha):
        fail("validation_error")
    token = os.getenv("ALICE_SHORT_TOKEN", "")
    if not token:
        fail("auth_not_configured")
    root = Path(__file__).resolve().parents[1]
    apps = CloudRuContainerAppsClient(project_id=project_id())
    deleted = purge_tombstones(apps)
    if deleted:
        print(
            json.dumps({"status": "TOMBSTONES_PURGED", "ids": deleted}, sort_keys=True), flush=True
        )
    image = build_image(root, args.sha)
    result = create_candidate(apps, image, token, args.sha)
    print(json.dumps(result, sort_keys=True), flush=True)
    print(
        json.dumps(
            {"status": "ALICE_DEV_INVENTORY", "containers": alice_dev_inventory(apps)},
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
