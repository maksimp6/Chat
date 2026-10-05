[Reading 880 lines from start (total: 880 lines, 0 remaining)]

#!/usr/bin/env python3
"""Deploy the reviewed Chrome MCP worker without touching the existing RDC service.

The worker is served directly from its stable Container Apps origin. Evolution API
Gateway has no public management API, so the service does not depend on it; the
worker is the access boundary. Anonymous routes are ``/healthz`` and the
protocol-required OAuth surface (discovery, dynamic registration, authorize,
short-token authorization, token and revoke). Browser control and MCP require the
worker bearer token or an OAuth token issued after the owner enters that short token, and deployment verifies that refusal on the public origin.
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
import time
from urllib.parse import urlsplit
from uuid import UUID

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.base import CloudProviderError  # noqa: E402
from cloud.cloudru.container_apps_client import (  # noqa: E402
    CloudRuContainerAppsClient,
    ContainerSpec,
    estimate_monthly_cost,
)
from cloud.cloudru.object_storage import CloudRuObjectStorage, cloudru_s3_credentials  # noqa: E402
from cloud.cloudru.registry_client import CloudRuRegistryClient  # noqa: E402
from scripts.cloudru_browser_probe import (  # noqa: E402
    registry_inventory,
    registry_identifier,
    wait_registry_operation,
)
from scripts.cloudru_deploy import _export_commit, _load_cloudru_credentials  # noqa: E402
from scripts.cloudru_rdc import application_origin, verify_private_acl  # noqa: E402
from storage import StorageObjectNotFound  # noqa: E402

REGISTRY = "alice-chrome-browser"
REPOSITORY = "chrome-worker"
DESCRIPTION = "Alice persistent Google Chrome Playwright MCP; issue 751"
# Container Apps offers fixed CPU/memory pairs; Chrome measured ~340 MiB idle and
# ~800 MiB on a heavy page, so 0.5 vCPU / 1 GiB is the economical size. Scaling
# from zero lets Cloud.ru stop the only replica when idle (profile is saved on
# SIGTERM); a cold start on the next request is accepted for cost.
CPU = "0.5"
IDLE_TIMEOUT = "900s"
# Earlier always-on revisions that deploy may still update in place.
ACCEPTED_RESOURCES = ({"cpu": CPU, "memory": "1024Mi"}, {"cpu": "1", "memory": "4096Mi"})
VOLUME = "chrome-state"
MOUNT = "/chrome-state"
IMAGE_RE = re.compile(re.escape(f"{REGISTRY}.cr.cloud.ru/{REPOSITORY}@sha256:") + "[0-9a-f]{64}")
APP_ENV = frozenset(
    {
        "BROWSER_API_TOKEN",
        "BROWSER_PUBLIC_URL",
        "BROWSER_IDP_ISSUER",
        "BROWSER_DEPLOYMENT_SHA",
        "BROWSER_OAUTH_STATE_DIR",
        "BROWSER_OAUTH_STATE_FILE",
        "ALICE_GITHUB_ALLOWED_IDS",
        "CHROME_PROFILE_DIR",
        "CHROME_STATE_DIR",
        "CHROME_STATE_REQUIRE_MOUNT",
    }
)
SAFE_ERRORS = frozenset(
    {
        "validation_error",
        "auth_not_configured",
        "authorization_failed",
        "auth_failed",
        "provider_http_error",
        "invalid_response",
        "docker_error",
        "registry_not_ready",
        "chrome_ownership_unconfirmed",
        "chrome_bucket_ownership_unconfirmed",
        "chrome_readiness_timeout",
        "chrome_runtime_failed",
        "chrome_checkpoint_failed",
        "chrome_stop_timeout",
        "chrome_rollback_failed",
        "chrome_ingress_unconfirmed",
    }
)


def fail(code):
    raise CloudProviderError("Chrome operation could not be verified", code=code)


def uuid(value):
    try:
        result = str(UUID(value))
    except (TypeError, ValueError, AttributeError):
        fail("validation_error")
    if result != value:
        fail("validation_error")
    return result


def lane(env=os.environ):
    # The test lane deploys any branch to a separate container and bucket so
    # experiments never touch the production worker or its browser profile.
    value = env.get("CHROME_LANE") or "production"
    if value not in ("production", "test"):
        fail("validation_error")
    return value


def names(project):
    suffix = uuid(project).replace("-", "")[:12]
    if lane() == "test":
        return "chrome-test-" + suffix, "alice-chrome-test-state-" + suffix
    return "chrome-" + suffix, "alice-chrome-state-" + suffix


def owner_marker(project):
    return {
        "schema": 1,
        "purpose": "alice-chrome",
        "project_id": uuid(project),
        "container_name": names(project)[0],
    }


def public_origin(value):
    try:
        parts = urlsplit(value)
        valid = (
            parts.scheme == "https"
            and parts.hostname
            and parts.hostname.endswith((".containerapps.ru", ".containers.cloud.ru"))
            and not parts.username
            and not parts.password
            and parts.port in (None, 443)
            and parts.path in ("", "/")
            and not parts.query
            and not parts.fragment
        )
    except (TypeError, ValueError):
        fail("validation_error")
    if not valid:
        fail("validation_error")
    return "https://" + parts.hostname


def idp_issuer(value):
    # The central IdP lives on its own provider origin or on the owner's domain.
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        valid = (
            parts.scheme == "https"
            and (
                host.endswith((".containerapps.ru", ".containers.cloud.ru"))
                or host == "maxxxpavlov.online"
                or host.endswith(".maxxxpavlov.online")
            )
            and not parts.username
            and not parts.password
            and parts.port in (None, 443)
            and parts.path in ("", "/")
            and not parts.query
            and not parts.fragment
        )
    except (TypeError, ValueError, AttributeError):
        fail("validation_error")
    if not valid:
        fail("validation_error")
    return "https://" + host


def runtime_env(sha, env=os.environ):
    token = env.get("BROWSER_API_TOKEN") or env.get("ALICE_SHORT_TOKEN", "")
    if not token:
        fail("auth_not_configured")
    owner = env.get("ALICE_GITHUB_ALLOWED_IDS") or "293531601"
    if not re.fullmatch(r"[1-9][0-9]*", owner) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        fail("validation_error")
    result = {
        "BROWSER_API_TOKEN": token,
        "BROWSER_DEPLOYMENT_SHA": sha,
        "ALICE_GITHUB_ALLOWED_IDS": owner,
        "CHROME_PROFILE_DIR": "/tmp/chrome-profile",
        "CHROME_STATE_DIR": MOUNT,
        "CHROME_STATE_REQUIRE_MOUNT": "1",
        "BROWSER_OAUTH_STATE_DIR": "/tmp/chrome-auth",
        "BROWSER_OAUTH_STATE_FILE": "/tmp/chrome-auth/oauth.json",
    }
    if env.get("BROWSER_PUBLIC_URL"):
        result["BROWSER_PUBLIC_URL"] = public_origin(env["BROWSER_PUBLIC_URL"])
    if env.get("BROWSER_IDP_ISSUER"):
        result["BROWSER_IDP_ISSUER"] = idp_issuer(env["BROWSER_IDP_ISSUER"])
    return result


def creation_body(project, image, environment):
    if not isinstance(image, str) or not IMAGE_RE.fullmatch(image):
        fail("validation_error")
    if set(environment) - APP_ENV or not environment.get("BROWSER_API_TOKEN"):
        fail("validation_error")
    name, bucket = names(project)
    spec = ContainerSpec(
        name=name,
        image=image,
        cpu=CPU,
        min_instances=0,
        max_instances=1,
        public=True,
        description=DESCRIPTION,
        env=environment,
    )
    spec.validate()
    return {
        "name": name,
        "projectId": project,
        "description": DESCRIPTION,
        "configuration": {
            "ingress": {"publiclyAccessible": True, "accessSettings": {"enableAuth": False}},
            "autoDeployments": {"enabled": False},
        },
        "template": {
            "timeout": "300s",
            "idleTimeout": IDLE_TIMEOUT,
            "protocol": "http_1",
            "scaling": {"minInstanceCount": 0, "maxInstanceCount": 1},
            "volumes": [{"name": VOLUME, "type": "s3", "volumeAttributes": {"bucketName": bucket}}],
            "containers": [
                {
                    **spec.container_body(),
                    "volumeMounts": [{"name": VOLUME, "mountPath": MOUNT, "readOnly": False}],
                }
            ],
        },
    }


def owned_record(apps, *, identifier=None):
    matches = [
        item
        for item in provider(apps.list, require_total=True)
        if item.get("name") == names(apps.project_id)[0]
    ]
    if not matches:
        return None
    if len(matches) != 1:
        fail("invalid_response")
    record = matches[0]
    try:
        template = record["template"]
        containers = template["containers"]
        container = containers[0]
        volumes = template["volumes"]
        volume = volumes[0]
        mounts = container["volumeMounts"]
        variables = container["env"]
        environment = {item["name"]: item["value"] for item in variables}
        ingress = record["configuration"]["ingress"]
        valid = (
            record.get("projectId") == apps.project_id
            and record.get("description") == DESCRIPTION
            and uuid(record["id"]) == (identifier or record["id"])
            and len(containers) == 1
            and container["name"] == record["name"]
            and container["containerPort"] == 8080
            and IMAGE_RE.fullmatch(container["image"])
            and container.get("resources") in ACCEPTED_RESOURCES
            and len(variables) == len(environment)
            and not (set(environment) - APP_ENV)
            and environment.get("CHROME_PROFILE_DIR") == "/tmp/chrome-profile"
            and environment.get("CHROME_STATE_DIR") == MOUNT
            and environment.get("CHROME_STATE_REQUIRE_MOUNT") == "1"
            and environment.get("BROWSER_OAUTH_STATE_DIR") == "/tmp/chrome-auth"
            and environment.get("BROWSER_OAUTH_STATE_FILE") == "/tmp/chrome-auth/oauth.json"
            and bool(environment.get("BROWSER_API_TOKEN"))
            and template["scaling"].get("minInstanceCount", 0) in (0, 1)
            and template["scaling"].get("maxInstanceCount") == 1
            and len(volumes) == 1
            and volume.get("name") == VOLUME
            and volume.get("type") == "s3"
            and volume["volumeAttributes"].get("bucketName") == names(apps.project_id)[1]
            and volume["volumeAttributes"].get("entrypoint", "https://s3.cloud.ru")
            == "https://s3.cloud.ru"
            and volume["volumeAttributes"].get("region", "ru-central-1") == "ru-central-1"
            and len(mounts) == 1
            and mounts[0].get("name") == VOLUME
            and mounts[0].get("mountPath") == MOUNT
            and mounts[0].get("readOnly", False) is False
            and mounts[0].get("subPath", "") == ""
            and ingress.get("publiclyAccessible") is True
            # Explicit either way: False is the current contract; True is only a
            # legacy container that deploy restores and readiness then rejects.
            and isinstance(ingress.get("accessSettings", {}).get("enableAuth"), bool)
            and record["configuration"].get("privileged", False) is False
            and record["configuration"].get("autoDeployments", {}).get("enabled", False) is False
        )
    except (KeyError, IndexError, TypeError, AttributeError):
        fail("chrome_ownership_unconfirmed")
    if not valid:
        fail("chrome_ownership_unconfirmed")
    return record


def storage_client(project, tenant, env=os.environ):
    tenant = uuid(tenant)
    key = env.get("CLOUDRU_IAM_KEY_ID", "")
    if ":" in key and (not key.startswith(tenant + ":") or key.count(":") != 1):
        fail("validation_error")
    credentials = cloudru_s3_credentials(key, env.get("CLOUDRU_IAM_KEY_SECRET", ""), tenant)
    if credentials is None:
        fail("auth_not_configured")
    return CloudRuObjectStorage(names(project)[1]), credentials


def bucket_exists(store, credentials, project):
    try:
        store._request("HEAD", credentials=credentials)
    except StorageObjectNotFound:
        return False
    verify_private_acl(store, credentials)
    try:
        contents = store.download("owner.json", credentials=credentials)
        if len(contents) > 4096 or json.loads(contents) != owner_marker(project):
            fail("chrome_bucket_ownership_unconfirmed")
    except (StorageObjectNotFound, ValueError, RecursionError):
        fail("chrome_bucket_ownership_unconfirmed")
    return True


def prepare_bucket(store, credentials, project):
    if bucket_exists(store, credentials, project):
        return
    store._request("PUT", credentials=credentials)
    verify_private_acl(store, credentials)
    store.upload(
        "owner.json",
        json.dumps(owner_marker(project), sort_keys=True).encode(),
        credentials=credentials,
    )
    if not bucket_exists(store, credentials, project):
        fail("chrome_bucket_ownership_unconfirmed")


def prepare_registry(registry, *, sleep=time.sleep, clock=time.monotonic):
    matches = [item for item in registry_inventory(registry) if item["name"] == REGISTRY]
    if len(matches) > 1:
        fail("invalid_response")
    identifier = registry_identifier(matches[0]) if matches else None
    if identifier is None:
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
        identifier = wait_registry_operation(registry, operation)
    deadline = clock() + 60
    while clock() < deadline:
        if identifier is None:
            candidates = [item for item in registry_inventory(registry) if item["name"] == REGISTRY]
            if len(candidates) > 1:
                fail("invalid_response")
            if candidates:
                identifier = registry_identifier(candidates[0])
        if identifier is not None:
            try:
                item = registry.client.request(
                    "artifact_registry",
                    "GET",
                    f"/v1/registries/{uuid(identifier)}",
                    params={"projectId": registry.project_id},
                )
            except CloudProviderError as exc:
                if exc.http_status != 404:
                    raise
                sleep(2)
                continue
            if (
                registry_identifier(item) != identifier
                or item.get("name") != REGISTRY
                or item.get("isPublic", False) is not False
                or item.get("registryType", "DOCKER") not in ("DOCKER", 0)
            ):
                fail("validation_error")
            if item.get("status") in ("ACTIVE", 1):
                return
        sleep(2)
    fail("registry_not_ready")


def export_source(sha, root, dest):
    if lane() != "test":
        _export_commit(sha, str(root), dest)
        return
    # The test lane builds the checked-out branch commit; require_reviewed_head
    # already proved HEAD is exactly this clean commit.
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        fail("validation_error")
    archive = subprocess.run(
        ["git", "-C", str(root), "archive", "--format=tar", sha], capture_output=True, check=False
    )
    if archive.returncode:
        fail("validation_error")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as stream:
        stream.extractall(dest, filter="data")


def build_image(root, sha):
    registry = CloudRuRegistryClient()
    with tempfile.TemporaryDirectory(prefix="chrome-build-") as exported:
        export_source(sha, root, exported)
        prepare_registry(registry)
        context = Path(exported) / "deploy" / "chrome-worker"
        print(json.dumps({"stage": "chrome_build_push"}), flush=True)
        image = registry.build_and_push(
            registry_name=REGISTRY,
            repository=REPOSITORY,
            tag=sha,
            context_dir=str(context),
            dockerfile=str(context / "Dockerfile"),
        )
        if not IMAGE_RE.fullmatch(image.pinned):
            fail("invalid_response")
        return image.pinned


# The provider proxy abandons a testCall while a cold or 0.5 vCPU replica is
# still starting Chrome; these worker operations are idempotent, so retry.
RETRY_STATUSES = frozenset({499, 502, 503, 504})


def provider(operation, *args, timeout=240, sleep=time.sleep, clock=time.monotonic, **kwargs):
    # Lifecycle and inventory calls are idempotent; the provider proxy abandons
    # them (HTTP 499) while a slow operation is still in progress.
    deadline = clock() + timeout
    while True:
        try:
            return operation(*args, **kwargs)
        except CloudProviderError as exc:
            if exc.http_status not in RETRY_STATUSES or clock() >= deadline:
                raise
            sleep(5)


def request_worker(
    apps, record, token, path, *, method="GET", timeout=240, sleep=time.sleep, clock=time.monotonic
):
    if (path, method) not in (
        ("/healthz", "GET"),
        ("/browser/v1/status", "GET"),
        ("/browser/v1/sleep", "POST"),
        ("/browser/v1/wake", "POST"),
    ):
        fail("validation_error")
    if record.get("name") != names(apps.project_id)[0]:
        fail("chrome_ownership_unconfirmed")
    body = {
        "name": record["name"],
        "projectId": apps.project_id,
        "method": method.lower(),
        "path": path,
    }
    if path != "/healthz":
        body["headers"] = {"Authorization": "Bearer " + token}
    deadline = clock() + timeout
    while True:
        try:
            response = apps.client.request(
                "container_apps",
                "POST",
                f"/v2/containers/{record['name']}:testCall",
                json_body=body,
            )
            break
        except CloudProviderError as exc:
            if exc.http_status not in RETRY_STATUSES or clock() >= deadline:
                raise
            sleep(5)
    if (
        response.get("statusCode") != 200
        or not isinstance(response.get("body"), str)
        or len(response["body"]) > 65536
        or response.get("isBase64Encoded", False) is not False
    ):
        fail("chrome_runtime_failed")
    try:
        result = json.loads(response["body"])
    except ValueError:
        fail("invalid_response")
    if not isinstance(result, dict):
        fail("invalid_response")
    return result


def verify_ingress(record, *, http_get=requests.get):
    # The public origin must reach the worker itself, and the worker must refuse
    # anonymous browser and MCP access. No credentials are sent; redirects are refused.
    origin = application_origin(record)
    try:
        health = http_get(origin + "/healthz", timeout=10, allow_redirects=False)
        value = health.json()
        environment = {
            item["name"]: item["value"] for item in record["template"]["containers"][0]["env"]
        }
        # The public origin must serve the exact revision verified via the control plane.
        healthy = (
            health.status_code == 200
            and value.get("status") == "ok"
            and value.get("state_ready") is True
            and value.get("deployment_sha") == environment.get("BROWSER_DEPLOYMENT_SHA")
            and value.get("oauth_ready") is bool(environment.get("BROWSER_PUBLIC_URL"))
        )
        refused = all(
            http_get(origin + path, timeout=10, allow_redirects=False).status_code == 401
            for path in ("/browser/v1/status", "/browser/v1/mcp")
        )
    except (requests.RequestException, ValueError, AttributeError, KeyError, TypeError):
        fail("chrome_ingress_unconfirmed")
    if not healthy or not refused:
        fail("chrome_ingress_unconfirmed")


def verify_health(apps, record, environment):
    value = request_worker(apps, record, "", "/healthz")
    if (
        not isinstance(value, dict)
        or value.get("status") != "ok"
        or value.get("state_ready") is not True
        or value.get("deployment_sha") != environment.get("BROWSER_DEPLOYMENT_SHA")
        or value.get("oauth_ready") is not bool(environment.get("BROWSER_PUBLIC_URL"))
    ):
        fail("chrome_runtime_failed")


def wait_ready(
    apps, *, identifier=None, image=None, timeout=420, sleep=time.sleep, clock=time.monotonic
):
    readiness_started = time.perf_counter()
    deadline = clock() + timeout
    while clock() < deadline:
        record = owned_record(apps, identifier=identifier)
        if record is not None:
            status = str(record.get("status", "")).lower()
            if status in ("rejected", "deleted", "suspended_product"):
                fail("chrome_runtime_failed")
            if status == "running" and (
                image is None or record["template"]["containers"][0]["image"] == image
            ):
                env = {
                    item["name"]: item["value"]
                    for item in record["template"]["containers"][0]["env"]
                }
                try:
                    verify_health(apps, record, env)
                    health = request_worker(
                        apps, record, env["BROWSER_API_TOKEN"], "/browser/v1/status"
                    )
                    if (
                        health.get("state") in ("sleeping", "awake")
                        and isinstance(health.get("profile"), dict)
                        and health["profile"].get("enabled") is True
                    ):
                        verify_ingress(record)
                        print(
                            json.dumps(
                                {
                                    "stage": "chrome_health_readiness",
                                    "seconds": time.perf_counter() - readiness_started,
                                }
                            ),
                            flush=True,
                        )
                        return record
                except (CloudProviderError, requests.RequestException):
                    pass
        sleep(2)
    fail("chrome_readiness_timeout")


def checkpoint(apps, record):
    environment = {
        item["name"]: item["value"] for item in record["template"]["containers"][0]["env"]
    }
    result = request_worker(
        apps, record, environment["BROWSER_API_TOKEN"], "/browser/v1/sleep", method="POST"
    )
    profile = result.get("profile")
    if (
        result.get("state") != "sleeping"
        or not isinstance(profile, dict)
        or profile.get("enabled") is not True
        or type(profile.get("generation")) is not int
        or profile["generation"] < 1
    ):
        fail("chrome_checkpoint_failed")
    return profile["generation"]


def stop_owned(apps, record, *, timeout=300, sleep=time.sleep, clock=time.monotonic):
    current = owned_record(apps, identifier=record["id"])
    if current is None:
        fail("chrome_ownership_unconfirmed")
    if str(current.get("status", "")).lower() != "suspended":
        provider(apps.stop, current["name"])
    deadline = clock() + timeout
    while clock() < deadline:
        current = owned_record(apps, identifier=record["id"])
        if current and str(current.get("status", "")).lower() == "suspended":
            return current
        sleep(2)
    fail("chrome_stop_timeout")


def verify_restored(apps, record, generation):
    environment = {
        item["name"]: item["value"] for item in record["template"]["containers"][0]["env"]
    }
    status = request_worker(apps, record, environment["BROWSER_API_TOKEN"], "/browser/v1/status")
    profile = status.get("profile")
    if (
        not isinstance(profile, dict)
        or profile.get("restored") is not True
        or type(profile.get("generation")) is not int
        or profile["generation"] < generation
    ):
        fail("chrome_checkpoint_failed")


def summary(record):
    container = record["template"]["containers"][0]
    environment = {item["name"]: item["value"] for item in container["env"]}
    origin = application_origin(record)
    public = public_origin(environment["BROWSER_PUBLIC_URL"])
    return {
        "status": "CHROME_PRIVATE_RUNNING"
        if str(record.get("status", "")).lower() == "running"
        else "CHROME_STOPPED",
        "container_name": record["name"],
        "container_id": uuid(record["id"]),
        "image": container["image"],
        "digest": container["image"].split("@", 1)[1],
        "provider_url": origin,
        "mcp_url": public + "/browser/v1/mcp",
        "ingress": "provider_iam"
        if record["configuration"]["ingress"]["accessSettings"]["enableAuth"]
        else "public_worker_auth",
        "cost_floor": estimate_monthly_cost(CPU, 0),
    }


def deploy(apps, store, credentials, image, environment):
    previous = owned_record(apps)
    if previous:
        # The container keeps its name, so its provider origin stays stable.
        environment = {**environment, "BROWSER_PUBLIC_URL": application_origin(previous)}
    generation = None
    prepare_bucket(store, credentials, apps.project_id)
    if previous and str(previous.get("status", "")).lower() == "running":
        try:
            generation = checkpoint(apps, previous)
        except CloudProviderError as exc:
            # A broken test replica must not block replacing it; production
            # never replaces a worker whose profile could not be saved.
            if lane() != "test":
                raise
            print(
                json.dumps({"stage": "chrome_test_checkpoint_skipped", **safe_error(exc)}),
                flush=True,
            )
    if previous:
        stop_owned(apps, previous)
    attempted = False
    identifier = previous["id"] if previous else None
    try:
        body = creation_body(apps.project_id, image, environment)
        attempted = True
        start_requested = time.perf_counter()
        if previous:
            provider(apps.restore, previous["name"], body)
            provider(apps.start, previous["name"])
        else:
            operation = apps.client.request(
                "container_apps", "POST", "/v2/containers", json_body=body
            )
            if operation.get("resourceId"):
                identifier = uuid(operation["resourceId"])
        print(
            json.dumps(
                {
                    "stage": "chrome_container_start",
                    "seconds": time.perf_counter() - start_requested,
                }
            ),
            flush=True,
        )
        record = wait_ready(apps, identifier=identifier, image=image)
        if generation is not None:
            verify_restored(apps, record, generation)
        else:
            # First boot must prove that Chrome runs and the mounted store can
            # persist and restore a closed profile, not merely that it exists.
            awake = request_worker(
                apps, record, environment["BROWSER_API_TOKEN"], "/browser/v1/wake", method="POST"
            )
            if awake.get("state") != "awake":
                fail("chrome_runtime_failed")
            generation = checkpoint(apps, record)
            stop_owned(apps, record)
            # The origin is assigned on creation; bind OAuth to it on the same
            # container instead of creating another one.
            environment = {**environment, "BROWSER_PUBLIC_URL": application_origin(record)}
            provider(
                apps.restore, record["name"], creation_body(apps.project_id, image, environment)
            )
            provider(apps.start, record["name"])
            record = wait_ready(apps, identifier=record["id"], image=image)
            verify_restored(apps, record, generation)
        return summary(record)
    except Exception as exc:
        # Rollback may raise its own code; keep the original cause visible.
        print(json.dumps({"stage": "chrome_deploy_failed", **safe_error(exc)}), flush=True)
        if attempted:
            try:
                current = owned_record(apps, identifier=identifier)
                if current is not None:
                    stop_owned(apps, current)
                if previous:
                    provider(apps.restore, previous["name"], previous)
                    if str(previous.get("status", "")).lower() == "running":
                        provider(apps.start, previous["name"])
                        wait_ready(
                            apps,
                            identifier=previous["id"],
                            image=previous["template"]["containers"][0]["image"],
                        )
                    print(
                        json.dumps({"stage": "chrome_rollback", "status": "RESTORED"}), flush=True
                    )
                elif current is not None:
                    print(
                        json.dumps(
                            {"stage": "chrome_rollback", "status": "NEW_CONTAINER_SUSPENDED"}
                        ),
                        flush=True,
                    )
                else:
                    fail("chrome_rollback_failed")
            except Exception:
                fail("chrome_rollback_failed")
        raise


def require_reviewed_head(root, sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        fail("validation_error")
    # Production runs only reviewed master commits; the test lane may run a branch.
    for args in (
        ()
        if lane() == "test"
        else (
            ["git", "fetch", "--no-tags", "origin", "master"],
            ["git", "merge-base", "--is-ancestor", sha, "FETCH_HEAD"],
        )
    ):
        result = subprocess.run(args, cwd=root, capture_output=True, check=False)
        if result.returncode:
            fail("validation_error")
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root, text=True
    )
    if actual != sha or dirty:
        fail("validation_error")


def export_public_url(origin, env=os.environ):
    # Live MCP acceptance in the workflow targets the deployed origin.
    origin = public_origin(origin)
    if env.get("GITHUB_ENV"):
        with open(env["GITHUB_ENV"], "a", encoding="utf-8") as stream:
            stream.write("BROWSER_PUBLIC_URL=" + origin + "\n")
    # The owner only needs these two URLs: the ChatGPT connector and the OAuth callback.
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(
                "## Chrome MCP\n\n"
                f"- ChatGPT connector URL: `{origin}/browser/v1/mcp`\n"
            )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "deploy", "status", "restart", "stop"))
    parser.add_argument("--sha", required=True)
    parser.add_argument("--tenant-id", default=os.environ.get("CLOUDRU_STORAGE_TENANT_ID", ""))
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    require_reviewed_head(root, args.sha)
    _load_cloudru_credentials()
    project = uuid(os.environ.get("CLOUDRU_PROJECT_ID", ""))
    if any(not os.environ.get(key) for key in ("CLOUDRU_IAM_KEY_ID", "CLOUDRU_IAM_KEY_SECRET")):
        fail("auth_not_configured")
    apps = CloudRuContainerAppsClient(project_id=project)
    if args.action in ("preflight", "deploy"):
        # The public origin is always the container's own provider origin.
        environment = runtime_env(
            args.sha,
            {key: value for key, value in os.environ.items() if key != "BROWSER_PUBLIC_URL"},
        )
        store, credentials = storage_client(project, args.tenant_id)
        record = owned_record(apps)
        exists = bucket_exists(store, credentials, project)
        result = {
            "status": "PREFLIGHT_PASSED",
            "container_exists": record is not None,
            "bucket_exists": exists,
            "cost_floor": estimate_monthly_cost(CPU, 0),
        }
        if args.action == "deploy":
            image = build_image(root, args.sha)
            result = deploy(apps, store, credentials, image, environment)
            export_public_url(result["provider_url"])
    else:
        record = owned_record(apps)
        if record is None:
            result = {"status": "CHROME_NOT_FOUND", "container_name": names(project)[0]}
        elif args.action == "status":
            result = summary(record)
        else:
            generation = None
            if str(record.get("status", "")).lower() == "running":
                generation = checkpoint(apps, record)
            stop_owned(apps, record)
            if args.action == "restart":
                provider(apps.start, record["name"])
                record = wait_ready(apps, identifier=record["id"])
                if generation is not None:
                    verify_restored(apps, record, generation)
            else:
                record = owned_record(apps, identifier=record["id"])
            result = summary(record)
    print(json.dumps(result, sort_keys=True), flush=True)


def safe_error(exc):
    code = getattr(exc, "code", None)
    status = getattr(exc, "http_status", None)
    return {
        "error": code if code in SAFE_ERRORS else "chrome_operation_failed",
        "http_status": status if type(status) is int and 100 <= status <= 599 else None,
    }


def cli():
    try:
        main()
    except Exception as exc:
        print(json.dumps(safe_error(exc)), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli())

[executed on device: rdc-22706bfa6066-00001-deployment-68b9cf96cc-r22sb (bf88308d-eebc-4fb0-98e4-96542cf47cb8)]