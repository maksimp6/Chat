#!/usr/bin/env python3
"""Deploy an isolated lightweight Alice Dev candidate without touching persistent RDC."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
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


def fail(code: str) -> None:
    raise CloudProviderError("Alice Dev candidate operation failed", code=code)


def project_id() -> str:
    raw = os.getenv("CLOUDRU_PROJECT_ID", "").strip()
    try:
        return str(UUID(raw))
    except (ValueError, TypeError, AttributeError):
        fail("validation_error")


def candidate_name(project: str) -> str:
    return "alice-dev-" + UUID(project).hex[:12]


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


def create_candidate(apps: CloudRuContainerAppsClient, image: str, token: str) -> dict:
    project = project_id()
    name = candidate_name(project)
    if apps.find_for_deploy(name) is not None:
        fail("already_exists")
    spec = ContainerSpec(
        name=name,
        image=image,
        cpu="0.2",
        min_instances=0,
        max_instances=1,
        public=True,
        description=DESCRIPTION,
        idle_timeout="900s",
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
    apps.client.request(SERVICE, "POST", "/v2/containers", json_body=body)
    status = apps.wait_until_ready(name, image=image, timeout_s=300, poll_s=5)
    health = apps.health_check(status["public_uri"], attempts=18, delay_s=5)
    origin = health["url"].removesuffix("/healthz")
    return {
        "status": "ALICE_DEV_READY",
        "name": name,
        "origin": origin,
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
    image = build_image(root, args.sha)
    result = create_candidate(CloudRuContainerAppsClient(project_id=project_id()), image, token)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
