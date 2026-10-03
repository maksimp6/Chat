#!/usr/bin/env python3
"""Bounded Chromium compatibility probe; never deploys the authenticated RDC client."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from uuid import UUID

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.base import CloudProviderError  # noqa: E402
from cloud.cloudru.container_apps_client import CloudRuContainerAppsClient, ContainerSpec  # noqa: E402
from cloud.cloudru.registry_client import CloudRuRegistryClient  # noqa: E402
from scripts.cloudru_deploy import _export_commit  # noqa: E402

REGISTRY = "alice-rdc-probe"
REPOSITORY = "chromium-probe"
DESCRIPTION = "Alice RDC browser compatibility probe; no OAuth or user data"


def find_probe(apps, name):
    found = [item for item in apps.list(require_total=True) if item.get("name") == name]
    if len(found) > 1:
        raise CloudProviderError("Ambiguous probe", code="invalid_response")
    if not found:
        return None
    item = found[0]
    try:
        identifier = str(UUID(item["id"]))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CloudProviderError("Invalid probe identity", code="invalid_response") from exc
    return identifier


def prepare_registry(registry):
    # Do not interpret an unfamiliar response as permission to create a resource.
    print('{"stage":"registry_inventory"}', flush=True)
    payload = registry.client.request(
        "artifact_registry", "GET", "/v1/registries", params={"projectId": registry.project_id}
    )
    collections = [payload[k] for k in ("registries", "items", "data") if k in payload]
    if (
        len(collections) != 1
        or not isinstance(collections[0], list)
        or not all(isinstance(x, dict) for x in collections[0])
        or payload.get("nextPageToken")
        or payload.get("next_page_token")
    ):
        raise CloudProviderError("Registry inventory incomplete", code="invalid_response")
    items = collections[0]
    if "total" in payload and str(payload["total"]) != str(len(items)):
        raise CloudProviderError("Registry inventory incomplete", code="invalid_response")
    matches = [item for item in items if item.get("name") == REGISTRY]
    if matches:
        if (
            len(matches) != 1
            or matches[0].get("isPublic") is not False
            or matches[0].get("registryType") != "DOCKER"
        ):
            raise CloudProviderError(
                "Probe registry must be private Docker", code="validation_error"
            )
        return
    print('{"stage":"registry_create"}', flush=True)
    registry.client.request(
        "artifact_registry",
        "POST",
        "/v1/registries",
        json_body={
            "projectId": registry.project_id,
            "name": REGISTRY,
            "isPublic": False,
            "registryType": "DOCKER",
        },
    )


def build_image(root, sha):
    registry = CloudRuRegistryClient()
    with tempfile.TemporaryDirectory(prefix="rdc-probe-") as exported:
        _export_commit(sha, str(root), exported)
        prepare_registry(registry)
        context = Path(exported) / "deploy" / "remote-desktop-commander"
        print('{"stage":"image_build_push"}', flush=True)
        return registry.build_and_push(
            registry_name=REGISTRY,
            repository=REPOSITORY,
            tag=sha,
            context_dir=str(context),
            dockerfile=str(context / "Dockerfile"),
        ).pinned


def verify_with_budget(*args):
    def expired(_signum, _frame):
        raise CloudProviderError("Probe exceeded 30 seconds", code="browser_probe_timeout")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 30)
    try:
        return verify_probe(*args)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def verify_probe(apps, name, identifier, image, *, http_get=requests.get, sleep=time.sleep):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        app = apps.get(name)
        if app is None:
            raise CloudProviderError("Probe disappeared", code="not_found")
        if app.get("id") != identifier:
            raise CloudProviderError("Probe identity changed", code="invalid_response")
        template = app.get("template") or {}
        containers = template.get("containers") or []
        uri = ((app.get("configuration") or {}).get("ingress") or {}).get("publicUri")
        status = str(app.get("status") or "").upper()
        if any(marker in status for marker in ("FAIL", "ERROR", "CRASH")):
            raise CloudProviderError("Probe runtime failed", code="browser_probe_failed")
        if containers and containers[0].get("image") == image and isinstance(uri, str):
            url = urlsplit(uri if "://" in uri else "https://" + uri)
            # Never forward IAM or follow a redirect to an arbitrary endpoint.
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.port not in (None, 443)
                or not url.hostname.endswith(".containers.cloud.ru")
            ):
                raise CloudProviderError("Unexpected probe origin", code="invalid_response")
            try:
                response = http_get(
                    f"https://{url.hostname}/healthz", timeout=3, allow_redirects=False
                )
                if response.status_code == 200 and response.json() == {
                    "mode": "cloud-probe",
                    "browser_ready": True,
                    "smoke_passed": True,
                }:
                    return
            except (requests.RequestException, ValueError):
                pass
        sleep(1)
    raise CloudProviderError("Browser probe exceeded 30 seconds", code="browser_probe_timeout")


def run_probe(apps, sha, image):
    name = "rdc-browser-probe-" + sha[:12]
    # Never take over or stop an existing resource, including an interrupted run.
    if find_probe(apps, name) is not None:
        raise CloudProviderError(
            "Probe already exists; inspect before reuse", code="already_exists"
        )
    identifier = None
    attempted = False
    try:
        print('{"stage":"container_create"}', flush=True)
        attempted = True
        apps.create(
            ContainerSpec(
                name=name,
                image=image,
                cpu="1",
                min_instances=0,
                max_instances=1,
                public=True,
                description=DESCRIPTION,
                env={"ALICE_RDC_MODE": "cloud-probe", "PORT": "8080"},
            )
        )
        identifier = find_probe(apps, name)
        if identifier is None:
            raise CloudProviderError("Probe identity unavailable", code="invalid_response")
        print('{"stage":"browser_verify"}', flush=True)
        verify_with_budget(apps, name, identifier, image)
        return {
            "status": "BROWSER_PROBE_PASSED",
            "source_sha": sha,
            "image": image,
            "container_id": identifier,
        }
    finally:
        if attempted:
            identifier = identifier or find_probe(apps, name)
            if identifier is not None:
                current = apps.get(name) or {}
                containers = (current.get("template") or {}).get("containers") or []
                if (
                    current.get("name") != name
                    or current.get("id") != identifier
                    or current.get("description") != DESCRIPTION
                    or not containers
                    or containers[0].get("image") != image
                ):
                    raise CloudProviderError("Cannot prove probe ownership", code="cleanup_failed")
                apps.stop(name)
                print(
                    json.dumps({"probe_stop_requested": True, "container_id": identifier}),
                    flush=True,
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        raise CloudProviderError("Invalid source SHA", code="validation_error")
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root, text=True
    )
    if actual != args.sha or dirty:
        raise CloudProviderError("Probe requires clean exact checkout", code="validation_error")
    for name in ("CLOUDRU_PROJECT_ID", "CLOUDRU_IAM_KEY_ID", "CLOUDRU_IAM_KEY_SECRET"):
        if not os.environ.get(name):
            raise CloudProviderError("Missing probe credentials", code="auth_not_configured")
    image = build_image(root, args.sha)
    print(json.dumps(run_probe(CloudRuContainerAppsClient(), args.sha, image)), flush=True)


if __name__ == "__main__":
    try:
        main()
    except CloudProviderError as exc:
        # Do not dump provider responses, build output, credentials or environment.
        print(json.dumps({"error": exc.code, "http_status": exc.http_status}), flush=True)
        sys.exit(1)
    except Exception:
        print('{"error":"probe_internal_error"}', flush=True)
        sys.exit(1)
