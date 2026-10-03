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
from urllib.parse import quote, urlsplit
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
PROBE_ERROR_CODES = frozenset(
    {
        "authorization_failed",
        "auth_failed",
        "auth_not_configured",
        "provider_http_error",
        "invalid_response",
        "validation_error",
        "unsupported_capability",
        "probe_identity_timeout",
        "browser_probe_timeout",
        "browser_probe_failed",
        "cleanup_failed",
        "already_exists",
        "registry_create_failed",
        "registry_create_timeout",
        "registry_creation_unconfirmed",
        "registry_inventory_timeout",
        "docker_error",
    }
)
VALIDATION_FIELDS = frozenset(
    {
        "name",
        "projectId",
        "description",
        "configuration.ingress.publiclyAccessible",
        "configuration.privileged",
        "configuration.autoDeployments.enabled",
        "template.timeout",
        "template.idleTimeout",
        "template.protocol",
        "template.scaling.minInstanceCount",
        "template.scaling.maxInstanceCount",
        "template.containers[0].name",
        "template.containers[0].image",
        "template.containers[0].containerPort",
        "template.containers[0].resources.cpu",
        "template.containers[0].resources.memory",
    }
)


def probe_error_details(exc):
    """Fixed error identifiers only; never messages, URLs or provider values."""
    code = getattr(exc, "code", None)
    status = getattr(exc, "http_status", None)
    result = {
        "error": code
        if isinstance(code, str) and code in PROBE_ERROR_CODES
        else "probe_internal_error",
        "http_status": status if type(status) is int and 100 <= status <= 599 else None,
    }
    current = exc
    for _ in range(4):
        response = current.response if isinstance(current, requests.RequestException) else None
        if response is not None and len(response.content) <= 65536:
            try:
                payload = response.json()
            except (ValueError, RecursionError):
                payload = None
            if isinstance(payload, dict):
                provider_code = payload.get("code")
                if type(provider_code) is int and 0 <= provider_code <= 16:
                    result["provider_status_code"] = provider_code
                fields = set()
                details = payload.get("details")
                for detail in details[:16] if isinstance(details, list) else []:
                    if (
                        not isinstance(detail, dict)
                        or detail.get("@type") != "type.googleapis.com/google.rpc.BadRequest"
                    ):
                        continue
                    violations = detail.get("fieldViolations")
                    for violation in violations[:32] if isinstance(violations, list) else []:
                        field = violation.get("field") if isinstance(violation, dict) else None
                        if isinstance(field, str) and field in VALIDATION_FIELDS:
                            fields.add(field)
                if fields:
                    result["provider_validation_fields"] = sorted(fields)
            break
        current = current.__cause__
        if current is None:
            break
    return result


def registry_response_shape(payload):
    """Only fixed field names, JSON types and counts; never echo provider values."""
    fields = (
        "registries",
        "items",
        "data",
        "result",
        "results",
        "total",
        "count",
        "totalCount",
        "total_count",
        "nextPageToken",
        "next_page_token",
        "pagination",
        "registryList",
        "registry_list",
        "content",
        "response",
        "code",
        "message",
        "error",
        "errors",
        "details",
        "status",
    )

    def shape(value, depth=0):
        if isinstance(value, dict):
            result = {"type": "object", "field_count": len(value)}
            if depth < 2:
                result["fields"] = {k: shape(value[k], depth + 1) for k in fields if k in value}
            return result
        if isinstance(value, list):
            return {"type": "array", "length": len(value)}
        if type(value) is int:
            return {"type": "integer", "zero": value == 0}
        if isinstance(value, str):
            return {"type": "string", "empty": value == "", "zero": value == "0"}
        if value is None:
            return {"type": "null"}
        return {"type": "boolean" if isinstance(value, bool) else "unknown"}

    return shape(payload)


def probe_record(apps, name):
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
    return {**item, "id": identifier}


def find_probe(apps, name):
    item = probe_record(apps, name)
    return item["id"] if item is not None else None


def registry_inventory(registry):
    """Current official protobuf contract: registries + nextPageToken, no total."""
    records = []
    token = None
    seen_tokens = set()
    seen_ids = set()
    for _ in range(100):
        params = {"projectId": registry.project_id, "pageSize": 100}
        if token:
            params["pageToken"] = token
        payload = registry.client.request(
            "artifact_registry", "GET", "/v1/registries", params=params
        )
        print(
            json.dumps(
                {"stage": "registry_response_shape", "shape": registry_response_shape(payload)}
            ),
            flush=True,
        )
        if set(payload) - {"registries", "nextPageToken"}:
            raise CloudProviderError("Unknown registry list schema", code="invalid_response")
        # ProtoJSON omits default repeated fields and accepts null as unset.
        items = payload.get("registries")
        if items is None:
            items = []
        if not isinstance(items, list):
            raise CloudProviderError("Invalid registry list", code="invalid_response")
        for item in items:
            identifier = registry_identifier(item)
            if identifier in seen_ids:
                raise CloudProviderError("Duplicate registry", code="invalid_response")
            seen_ids.add(identifier)
            records.append(item)
        token = payload.get("nextPageToken")
        if token in (None, ""):
            return records
        if not isinstance(token, str) or len(token) > 4096 or token in seen_tokens:
            raise CloudProviderError("Invalid registry pagination", code="invalid_response")
        seen_tokens.add(token)
    raise CloudProviderError("Registry pagination exceeded bound", code="invalid_response")


def registry_identifier(item):
    try:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
            raise ValueError("Missing registry name")
        return str(UUID(item["id"]))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CloudProviderError("Invalid registry identity", code="invalid_response") from exc


def wait_registry_ready(registry, identifier):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            item = registry.client.request(
                "artifact_registry",
                "GET",
                f"/v1/registries/{identifier}",
                params={"projectId": registry.project_id},
            )
        except CloudProviderError as exc:
            if exc.http_status != 404:
                raise
            time.sleep(1)
            continue
        kind = item.get("registryType", "DOCKER")
        if (
            registry_identifier(item) != identifier
            or item["name"] != REGISTRY
            or item.get("isPublic", False) is not False
            or not (kind == "DOCKER" or type(kind) is int and kind == 0)
        ):
            raise CloudProviderError(
                "Probe registry must be the expected private Docker registry",
                code="validation_error",
            )
        status = item.get("status", "CREATING")
        if status == "ACTIVE" or type(status) is int and status == 1:
            return
        if status != "CREATING" and not (type(status) is int and status == 0):
            raise CloudProviderError("Registry creation failed", code="registry_create_failed")
        time.sleep(1)
    raise CloudProviderError(
        "Registry readiness exceeded 30 seconds", code="registry_create_timeout"
    )


def wait_registry_operation(registry, operation):
    operation_id = operation.get("id")
    if (
        not isinstance(operation_id, str)
        or not 1 <= len(operation_id) <= 256
        or operation_id in {".", ".."}
        or any(ord(char) < 33 or ord(char) > 126 for char in operation_id)
    ):
        raise CloudProviderError(
            "Registry operation identity unavailable", code="registry_creation_unconfirmed"
        )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        error = operation.get("error")
        if error is not None and (
            not isinstance(error, dict)
            or type(error.get("code", 0)) is not int
            or error.get("code", 0) != 0
        ):
            raise CloudProviderError("Registry operation failed", code="registry_create_failed")
        done = operation.get("done", False)
        if type(done) is not bool:
            raise CloudProviderError("Invalid operation status", code="invalid_response")
        if done:
            try:
                return str(UUID(operation["resourceId"]))
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise CloudProviderError(
                    "Registry resource identity unavailable", code="invalid_response"
                ) from exc
        time.sleep(1)
        try:
            operation = registry.client.request(
                "artifact_registry", "GET", f"/v1/operations/{quote(operation_id, safe='')}"
            )
        except CloudProviderError as exc:
            if exc.http_status != 404:
                raise
            continue
        if operation.get("id") != operation_id:
            raise CloudProviderError("Registry operation identity changed", code="invalid_response")
    raise CloudProviderError(
        "Registry operation exceeded 30 seconds", code="registry_create_timeout"
    )


def prepare_registry(registry):
    # Contract extracted from the checksum-verified official Terraform provider v2.1.3.
    print('{"stage":"registry_inventory"}', flush=True)
    items = with_budget(registry_inventory, registry, code="registry_inventory_timeout")
    matches = [item for item in items if item["name"] == REGISTRY]
    if len(matches) > 1:
        raise CloudProviderError("Ambiguous probe registry", code="invalid_response")
    if matches:
        identifier = registry_identifier(matches[0])
    else:
        print('{"stage":"registry_create"}', flush=True)
        operation = registry.client.request(
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
        identifier = with_budget(
            wait_registry_operation, registry, operation, code="registry_create_timeout"
        )
    with_budget(wait_registry_ready, registry, identifier, code="registry_create_timeout")
    print(json.dumps({"stage": "registry_ready", "registry_id": identifier}), flush=True)


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


def with_budget(operation, *args, code):
    def expired(_signum, _frame):
        raise CloudProviderError("Probe phase exceeded 30 seconds", code=code)

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 30)
    try:
        return operation(*args)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def verify_with_budget(*args):
    return with_budget(verify_probe, *args, code="browser_probe_timeout")


def wait_for_probe(apps, name):
    """Creation is asynchronous: an empty inventory is not a terminal result."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        identifier = find_probe(apps, name)
        if identifier is not None:
            return identifier
        time.sleep(1)
    raise CloudProviderError("Probe identity still unavailable", code="probe_identity_timeout")


def stop_probe(apps, name, identifier, image):
    # Recovery gets its own budget, including after an ambiguous create error.
    # Never interpret expiry as proof that the asynchronous create did not commit.
    identifier = identifier or wait_for_probe(apps, name)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        current = probe_record(apps, name)
        if current is None:
            time.sleep(1)
            continue
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
        return
    raise CloudProviderError("Probe detail still unavailable", code="cleanup_failed")


def verify_probe(apps, name, identifier, image, *, http_get=requests.get, sleep=time.sleep):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        app = probe_record(apps, name)
        if app is None:
            sleep(1)
            continue
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
    phase = "container_create"
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
        phase = "container_discovery"
        print(json.dumps({"stage": phase}), flush=True)
        identifier = with_budget(wait_for_probe, apps, name, code="probe_identity_timeout")
        phase = "browser_verify"
        print('{"stage":"browser_verify"}', flush=True)
        verify_with_budget(apps, name, identifier, image)
        return {
            "status": "BROWSER_PROBE_PASSED",
            "source_sha": sha,
            "image": image,
            "container_id": identifier,
        }
    except Exception as exc:
        print(json.dumps({"stage": phase, **probe_error_details(exc)}), flush=True)
        raise
    finally:
        if attempted:
            try:
                with_budget(stop_probe, apps, name, identifier, image, code="cleanup_failed")
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "stage": "container_cleanup",
                            "probe_cleanup_unconfirmed": True,
                            "container_name": name,
                            **probe_error_details(exc),
                        }
                    ),
                    flush=True,
                )
                raise CloudProviderError(
                    "Probe may still appear or run; inspect before retry", code="cleanup_failed"
                ) from exc


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
    if not isinstance(image, str) or not re.fullmatch(
        re.escape(REGISTRY + ".cr.cloud.ru/" + REPOSITORY) + r"@sha256:[0-9a-f]{64}", image
    ):
        raise CloudProviderError("Invalid probe image digest", code="invalid_response")
    print(json.dumps({"stage": "image_ready", "image": image}), flush=True)
    print(json.dumps(run_probe(CloudRuContainerAppsClient(), args.sha, image)), flush=True)


def cli():
    try:
        main()
    except CloudProviderError as exc:
        # Do not dump provider responses, build output, credentials or environment.
        print(json.dumps(probe_error_details(exc)), flush=True)
        return 1
    except Exception:
        print('{"error":"probe_internal_error"}', flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli())
