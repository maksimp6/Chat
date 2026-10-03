#!/usr/bin/env python3
"""Reviewed, single-writer Cloud.ru RDC deployment. Never prints auth state or logs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit
from uuid import UUID
import xml.etree.ElementTree as ET

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.base import CloudProviderError  # noqa: E402
from cloud.cloudru.container_apps_client import CloudRuContainerAppsClient, ContainerSpec  # noqa: E402
from cloud.cloudru.object_storage import CloudRuObjectStorage, cloudru_s3_credentials  # noqa: E402
from scripts.cloudru_browser_probe import (  # noqa: E402
    REGISTRY,
    REPOSITORY,
    build_image,
    probe_error_details,
)
from storage import StorageObjectNotFound  # noqa: E402

DESCRIPTION = "Alice persistent RDC + Chromium; issue 409"
VOLUME = "rdc-state"
MOUNT = "/rdc-state"
HEALTH_KEYS = {
    "mode",
    "browser_ready",
    "rdc_running",
    "state_ready",
    "paired",
    "device_id",
    "checkpoint_generation",
    "quiesced",
}
IMAGE_RE = re.compile(re.escape(f"{REGISTRY}.cr.cloud.ru/{REPOSITORY}@sha256:") + "[0-9a-f]{64}")
CONTROL_HEADER = "X-Alice-Rdc-Control"
CONTROL_FILE = "control-permit.json"
CHECKPOINT_BUDGET = 300


def fail(code):
    raise CloudProviderError("RDC operation could not be verified", code=code)


def project_uuid(value):
    try:
        identifier = str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        fail("validation_error")
    if value != identifier:
        fail("validation_error")
    return identifier


def configured_tenant(value):
    if not value:
        fail("storage_tenant_not_configured")
    return project_uuid(value)


def names(project):
    suffix = project_uuid(project).replace("-", "")[:12]
    return "rdc-" + suffix, "alice-rdc-state-" + suffix


def owner_marker(project):
    return {
        "schema": 1,
        "purpose": "alice-rdc",
        "project_id": project,
        "container_name": names(project)[0],
    }


def runtime_env(project):
    return {
        "ALICE_RDC_MODE": "cloud-rdc",
        "ALICE_RDC_STATE_PATH": MOUNT,
        "ALICE_RDC_PAIRING_FILE": "/home/node/.rdc-pairing/handoff.json",
        "ALICE_RDC_PROJECT_ID": project,
    }


def creation_body(project, image):
    if not isinstance(image, str) or not IMAGE_RE.fullmatch(image):
        fail("validation_error")
    name, bucket = names(project)
    spec = ContainerSpec(
        name=name,
        image=image,
        cpu="1",
        min_instances=1,
        max_instances=1,
        public=True,
        description=DESCRIPTION,
        env=runtime_env(project),
    )
    spec.validate()
    return {
        "name": name,
        "projectId": project,
        "description": DESCRIPTION,
        "configuration": {
            "ingress": {"publiclyAccessible": True, "accessSettings": {"enableAuth": True}},
            "autoDeployments": {"enabled": False},
        },
        "template": {
            "timeout": "300s",
            "idleTimeout": "3600s",
            "protocol": "http_1",
            "scaling": {"minInstanceCount": 1, "maxInstanceCount": 1},
            "volumes": [{"name": VOLUME, "type": "s3", "volumeAttributes": {"bucketName": bucket}}],
            "containers": [
                {
                    **spec.container_body(),
                    "volumeMounts": [{"name": VOLUME, "mountPath": MOUNT, "readOnly": False}],
                }
            ],
        },
    }


def named_record(apps):
    name = names(apps.project_id)[0]
    records = [item for item in apps.list(require_total=True) if item.get("name") == name]
    if len(records) > 1:
        fail("invalid_response")
    return records[0] if records else None


def same_form(value, expected):
    """Compare known encodings including nested JSON scalar types."""
    if type(value) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(value) == set(expected) and all(
            same_form(value[key], item) for key, item in expected.items()
        )
    return value == expected


def safe_form(value, choices):
    """Report only fixed known encodings, never an unknown provider value."""
    for label, expected in choices:
        if same_form(value, expected):
            return label
    return "other"


def owned_record(apps, *, tenant, identifier=None, image=None):
    tenant = configured_tenant(tenant)
    record = named_record(apps)
    if record is None:
        return None
    project = project_uuid(apps.project_id)
    try:
        actual_id = str(UUID(record["id"]))
        template = record["template"]
        configuration = record["configuration"]
        ingress = configuration["ingress"]
        containers = template["containers"]
        container = containers[0]
        variables = container.get("env", [])
        environment = {v["name"]: v["value"] for v in variables}
        scaling = template["scaling"]
        creation_body(project, container["image"])
        volumes = template.get("volumes", [])
        mounts = container.get("volumeMounts", [])
        attributes = volumes[0].get("volumeAttributes", {}) if len(volumes) == 1 else {}
        volume_ok = (
            len(volumes) == 1
            and volumes[0].get("name") == VOLUME
            and volumes[0].get("type") == "s3"
            and attributes.get("bucketName") == names(project)[1]
            and not (
                set(attributes) - {"bucketName", "entrypoint", "tenantId", "region", "readOnly"}
            )
            and (
                "readOnly" not in attributes
                or safe_form(
                    attributes["readOnly"],
                    (
                        ("false_boolean", False),
                        ("false_string", "false"),
                        ("false_title", "False"),
                        ("false_upper", "FALSE"),
                    ),
                )
                != "other"
            )
            and attributes.get("entrypoint", "https://s3.cloud.ru") == "https://s3.cloud.ru"
            and attributes.get("region", "ru-central-1") == "ru-central-1"
        )
        if "tenantId" in attributes:
            volume_ok = volume_ok and project_uuid(attributes["tenantId"]) == tenant
        mount_ok = (
            len(mounts) == 1
            and mounts[0].get("name") == VOLUME
            and mounts[0].get("mountPath") == MOUNT
            and mounts[0].get("readOnly", False) is False
            and mounts[0].get("subPath", "") == ""
        )
        resources = container.get("resources")
        resource_map = resources if isinstance(resources, dict) else {}
        expected_env = runtime_env(project)
        checks = {
            "project": record.get("projectId") == project,
            "description": record.get("description") == DESCRIPTION,
            "identifier": identifier is None or identifier == actual_id,
            "image": image is None or image == container["image"],
            "public_ingress": ingress.get("publiclyAccessible") is True,
            "native_auth": ingress.get("accessSettings", {}).get("enableAuth") is True,
            "auto_deploy": configuration.get("autoDeployments", {}).get("enabled", False) is False,
            "privileged": configuration.get("privileged", False) is False,
            "container_count": len(containers) == 1,
            "env_duplicates": len(variables) == len(environment),
            "env_names": set(environment) == set(expected_env),
            "env_values": all(environment.get(key) == value for key, value in expected_env.items()),
            "container_name": container.get("name") == names(project)[0],
            "port": type(container.get("containerPort")) is int
            and container["containerPort"] == 8080,
            "resource_keys": isinstance(resources, dict) and set(resources) == {"cpu", "memory"},
            "cpu": resource_map.get("cpu") == "1",
            "memory": resource_map.get("memory") == "4096Mi",
            "scaling_types": type(scaling.get("minInstanceCount")) is int
            and type(scaling.get("maxInstanceCount")) is int,
            "scaling_min": scaling.get("minInstanceCount") == 1,
            "scaling_max": scaling.get("maxInstanceCount") == 1,
            "volume": volume_ok,
            "mount": mount_ok,
        }
        valid = all(checks.values())
        if not valid:
            print(
                json.dumps(
                    {
                        "stage": "rdc_ownership_mismatch",
                        "fields": sorted(key for key, passed in checks.items() if not passed),
                        "cpu_form": safe_form(
                            resource_map.get("cpu"),
                            (
                                ("one_string", "1"),
                                ("one_number", 1),
                                ("one_float", 1.0),
                                ("one_decimal_string", "1.0"),
                                ("thousand_millicores", "1000m"),
                            ),
                        ),
                        "memory_form": safe_form(
                            resource_map.get("memory"),
                            (
                                ("4096_mib", "4096Mi"),
                                ("4_gib", "4Gi"),
                                ("bytes_number", 4294967296),
                                ("bytes_string", "4294967296"),
                            ),
                        ),
                        "gpu_form": safe_form(
                            resource_map.get("gpu"),
                            (
                                ("omitted_or_null", None),
                                ("empty", {}),
                                ("zero_count", {"count": 0}),
                                ("zero_count_empty_sku", {"count": 0, "sku": ""}),
                            ),
                        ),
                        "known_resource_keys": sorted(
                            set(resource_map)
                            & {
                                "cpu",
                                "memory",
                                "gpu",
                                "ephemeralStorage",
                                "ephemeral-storage",
                                "ephemeral_storage",
                            }
                        ),
                        "known_platform_envs": sorted(
                            set(environment)
                            & {
                                "PORT",
                                "HOME",
                                "HOSTNAME",
                                "PATH",
                                "NODE_ENV",
                                "PUPPETEER_SKIP_DOWNLOAD",
                            }
                        ),
                        "volume_bucket_match": attributes.get("bucketName") == names(project)[1],
                        "volume_entrypoint_form": "omitted"
                        if "entrypoint" not in attributes
                        else safe_form(
                            attributes.get("entrypoint"),
                            (
                                ("null", None),
                                ("https_s3", "https://s3.cloud.ru"),
                                ("https_s3_slash", "https://s3.cloud.ru/"),
                                ("bare_s3", "s3.cloud.ru"),
                            ),
                        ),
                        "volume_region_match": attributes.get("region", "ru-central-1")
                        == "ru-central-1",
                        "volume_tenant_match": attributes.get("tenantId", tenant) == tenant,
                        "known_volume_attributes": sorted(
                            set(attributes)
                            & {
                                "readOnly",
                                "read_only",
                                "bucketId",
                                "bucket_id",
                                "projectId",
                                "project_id",
                                "logGroupId",
                                "logGroupName",
                                "unlimited",
                                "isPublic",
                                "accessKeyId",
                                "secretAccessKey",
                                "accessKey",
                                "secretKey",
                                "forcePathStyle",
                                "mountOptions",
                                "protocol",
                                "mounter",
                                "storageType",
                                "capacity",
                                "type",
                                "cache",
                                "endpoint",
                                "region",
                                "tenantId",
                                "bucketName",
                                "entrypoint",
                            }
                        ),
                        "volume_read_only_form": safe_form(
                            attributes.get("readOnly"),
                            (
                                ("omitted_or_null", None),
                                ("false_string", "false"),
                                ("false_title", "False"),
                                ("false_upper", "FALSE"),
                                ("empty_string", ""),
                                ("zero_string", "0"),
                                ("one_string", "1"),
                                ("zero_number", 0),
                                ("zero_float", 0.0),
                                ("true_title", "True"),
                                ("true_upper", "TRUE"),
                                ("false_boolean", False),
                                ("true_string", "true"),
                                ("true_boolean", True),
                            ),
                        ),
                        "volume_attributes_extra": bool(
                            set(attributes)
                            - {"bucketName", "entrypoint", "tenantId", "region", "readOnly"}
                        ),
                        "volume_type_form": safe_form(
                            volumes[0].get("type") if len(volumes) == 1 else None,
                            (("lower_s3", "s3"), ("upper_s3", "S3")),
                        ),
                    }
                ),
                flush=True,
            )

    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        fail("invalid_response")
    if not valid:
        fail("rdc_ownership_unconfirmed")
    return {**record, "id": actual_id}


def application_origin(record):
    uri = record["configuration"]["ingress"].get("publicUri")
    if not isinstance(uri, str):
        fail("invalid_response")
    try:
        parts = urlsplit(uri if "://" in uri else "https://" + uri)
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
    except ValueError:
        fail("invalid_response")
    if not valid:
        fail("invalid_response")
    return "https://" + parts.hostname


def verify_anonymous_gate(record, *, http_get=requests.get):
    # No IAM headers or redirect following. Never fetch the sensitive pairing path.
    response = http_get(application_origin(record) + "/healthz", timeout=5, allow_redirects=False)
    if response.status_code in (401, 403):
        return
    if response.status_code in (302, 303, 307, 308):
        try:
            target = urlsplit(response.headers.get("Location", ""))
            if (
                target.scheme == "https"
                and target.hostname
                and (target.hostname == "cloud.ru" or target.hostname.endswith(".cloud.ru"))
                and not target.username
                and not target.password
                and target.port in (None, 443)
            ):
                return
        except ValueError:
            pass
    fail("rdc_ingress_unconfirmed")


def json_object(body):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                fail("invalid_response")
            result[key] = value
        return result

    try:
        result = json.loads(body, object_pairs_hook=unique)
    except (ValueError, RecursionError):
        fail("invalid_response")
    if not isinstance(result, dict):
        fail("invalid_response")
    return result


def test_call(apps, path, method="GET", *, nonce=None, timeout=None):
    if (path, method) not in {("/healthz", "GET"), ("/checkpoint", "POST")}:
        fail("validation_error")
    if path == "/checkpoint":
        if not isinstance(nonce, str) or not re.fullmatch("[0-9a-f]{64}", nonce):
            fail("validation_error")
    elif nonce is not None:
        fail("validation_error")
    name = names(apps.project_id)[0]
    body = {"name": name, "projectId": apps.project_id, "method": method.lower(), "path": path}
    if nonce is not None:
        body["headers"] = {CONTROL_HEADER: nonce}
    previous_timeout = apps.client.timeout
    try:
        if timeout is not None:
            if timeout <= 0:
                fail("rdc_checkpoint_unconfirmed")
            apps.client.timeout = timeout
        deadline = time.monotonic() + apps.client.timeout
        try:
            payload = apps.client.request(
                "container_apps", "POST", f"/v2/containers/{name}:testCall", json_body=body
            )
        except CloudProviderError as exc:
            if (
                path != "/healthz"
                or method != "GET"
                or exc.code != "provider_http_error"
                or exc.http_status != 400
            ):
                raise
            diagnostic = safe_error(exc)
            if diagnostic.get("provider_status_code") != 3:
                raise
            print(json.dumps({"stage": "rdc_health_compatibility", **diagnostic}), flush=True)
            remaining_timeout = deadline - time.monotonic()
            if remaining_timeout <= 0:
                raise
            apps.client.timeout = remaining_timeout
            payload = apps.client.request(
                "container_apps",
                "POST",
                f"/v2/containers/{name}/testCall",
                params={"projectId": apps.project_id, "method": "get", "path": "/healthz"},
            )
    finally:
        apps.client.timeout = previous_timeout
    status = payload.get("statusCode")
    body = payload.get("body", "")
    if (
        type(status) is not int
        or not 100 <= status <= 599
        or not isinstance(body, str)
        or len(body) > 8192
        or payload.get("isBase64Encoded", False) is not False
    ):
        fail("invalid_response")
    if status in (404, 502, 504) or (status == 503 and path != "/healthz"):
        return None
    if status == 403 and path == "/checkpoint":
        if json_object(body) != {"status": "control_denied"}:
            fail("invalid_response")
        fail("rdc_control_unconfirmed")
    if status == 503 and path == "/healthz":
        # The proxy may answer before Node is listening. Only a claimed runtime
        # health object can prove a completed, closed checkpoint.
        try:
            candidate = json.loads(body)
        except (ValueError, RecursionError):
            return None
        if not isinstance(candidate, dict) or candidate.get("mode") != "cloud-rdc":
            return None
        summary = health_summary(json_object(body))
        return summary if summary["quiesced"] else None
    if status != 200:
        fail("rdc_runtime_failed")
    result = json_object(body)
    if path == "/healthz":
        health_summary(result)
        if result["quiesced"]:
            fail("invalid_response")
    return result


def health_summary(value):
    if not isinstance(value, dict) or set(value) != HEALTH_KEYS or value.get("mode") != "cloud-rdc":
        fail("invalid_response")
    if any(
        type(value.get(key)) is not bool
        for key in ("browser_ready", "rdc_running", "state_ready", "paired", "quiesced")
    ):
        fail("invalid_response")
    generation = value.get("checkpoint_generation")
    if type(generation) is not int or not 0 <= generation < 2**53:
        fail("invalid_response")
    identifier = value.get("device_id")
    if identifier is not None:
        project_uuid(identifier)
    if value["paired"] and identifier is None:
        fail("invalid_response")
    if value["quiesced"] and (
        not value["state_ready"] or value["browser_ready"] or value["rdc_running"] or generation < 1
    ):
        fail("invalid_response")
    return value


def wait_ready(
    apps,
    *,
    tenant,
    identifier=None,
    image=None,
    timeout=300,
    sleep=time.sleep,
    allow_quiesced=False,
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = owned_record(apps, tenant=tenant, identifier=identifier, image=image)
        if record is not None:
            if str(record.get("status", "")).lower() in {
                "rejected",
                "deleted",
                "suspended_product",
            }:
                fail("rdc_runtime_failed")
            try:
                result = test_call(apps, "/healthz")
            except CloudProviderError as exc:
                if exc.http_status not in (404, 502, 503, 504):
                    raise
                result = None
            if result is not None:
                summary = health_summary(result)
                if (allow_quiesced and summary["quiesced"]) or (
                    summary["browser_ready"] and summary["rdc_running"] and summary["state_ready"]
                ):
                    return record, summary
        sleep(2)
    fail("rdc_readiness_timeout")


def stop_owned(apps, *, tenant, identifier=None, image=None, timeout=300, sleep=time.sleep):
    # Discovery is bounded even after a possibly committed create; never retry creation.
    deadline = time.monotonic() + timeout
    record = None
    while time.monotonic() < deadline:
        record = owned_record(apps, tenant=tenant, identifier=identifier, image=image)
        if record is not None:
            break
        sleep(2)
    if record is None:
        fail("rdc_cleanup_unconfirmed")
    identifier = record["id"]
    if str(record.get("status", "")).lower() != "suspended":
        apps.stop(record["name"])
    while time.monotonic() < deadline:
        current = owned_record(apps, tenant=tenant, identifier=identifier, image=image)
        if current and str(current.get("status", "")).lower() == "suspended":
            return current
        sleep(2)
    fail("rdc_cleanup_unconfirmed")


def storage_client(project, tenant, env=os.environ):
    tenant = configured_tenant(tenant)
    key_id = env.get("CLOUDRU_IAM_KEY_ID", "")
    if ":" in key_id and (not key_id.startswith(tenant + ":") or key_id.count(":") != 1):
        fail("validation_error")
    credentials = cloudru_s3_credentials(key_id, env.get("CLOUDRU_IAM_KEY_SECRET", ""), tenant)
    if credentials is None:
        fail("auth_not_configured")
    return CloudRuObjectStorage(names(project)[1]), credentials


def verify_private_acl(store, credentials):
    content = store._request("GET", query={"acl": ""}, credentials=credentials).content
    if len(content) > 65536 or b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        fail("invalid_response")
    try:
        root = ET.fromstring(content)
        owner = root.find("{*}Owner/{*}ID")
        grants = root.findall("{*}AccessControlList/{*}Grant")
        private = owner is not None and bool(owner.text) and bool(grants)
        for grant in grants:
            grantee = grant.find("{*}Grantee")
            if grantee is None:
                private = False
                continue
            kind = grantee.get("{http://www.w3.org/2001/XMLSchema-instance}type")
            identifier = grantee.find("{*}ID")
            permission = grant.find("{*}Permission")
            private = private and kind == "CanonicalUser" and identifier is not None
            private = private and identifier.text == owner.text and permission is not None
            private = private and permission.text == "FULL_CONTROL"
    except (ET.ParseError, AttributeError):
        fail("invalid_response")
    if not private:
        fail("rdc_bucket_not_private")


def bucket_inventory(store, credentials, project):
    try:
        store._request("HEAD", credentials=credentials)
    except StorageObjectNotFound:
        return False
    verify_private_acl(store, credentials)
    try:
        body = store.download("owner.json", credentials=credentials)
        if len(body) > 4096:
            fail("invalid_response")
        marker = json.loads(body)
    except (StorageObjectNotFound, ValueError, RecursionError):
        fail("rdc_bucket_ownership_unconfirmed")
    if marker != owner_marker(project):
        fail("rdc_bucket_ownership_unconfirmed")
    return True


def require_owned_bucket(store, credentials, project):
    if not bucket_inventory(store, credentials, project):
        fail("rdc_bucket_ownership_unconfirmed")


def prepare_bucket(store, credentials, project):
    if bucket_inventory(store, credentials, project):
        return
    # Official S3 CreateBucket: empty signed PUT, default ACL private. Create once.
    store._request("PUT", credentials=credentials)
    verify_private_acl(store, credentials)
    store.upload(
        "owner.json",
        json.dumps(owner_marker(project), sort_keys=True).encode(),
        credentials=credentials,
    )
    if not bucket_inventory(store, credentials, project):
        fail("rdc_bucket_ownership_unconfirmed")


def preflight(apps, store, credentials, *, tenant):
    configured_tenant(tenant)
    record = named_record(apps)
    if record is not None:
        owned_record(apps, tenant=tenant)
    exists = bucket_inventory(store, credentials, apps.project_id)
    return {
        "status": "PREFLIGHT_PASSED",
        "container_exists": record is not None,
        "bucket_exists": exists,
    }


def deployment_summary(record, health):
    if health["quiesced"]:
        return {
            "status": "RDC_QUIESCED",
            "container_name": record["name"],
            "container_id": record["id"],
            **health,
        }
    return {
        "status": "RDC_RUNNING" if health["paired"] else "PAIRING_REQUIRED",
        "container_name": record["name"],
        "container_id": record["id"],
        "pairing_url": application_origin(record) + "/rdc/pair",
        **health,
    }


def install(apps, store, credentials, image, *, tenant, http_get=requests.get):
    configured_tenant(tenant)
    if named_record(apps) is not None:
        fail("already_exists")
    prepare_bucket(store, credentials, apps.project_id)
    attempted = False
    identifier = None
    try:
        attempted = True
        operation = apps.client.request(
            "container_apps",
            "POST",
            "/v2/containers",
            json_body=creation_body(apps.project_id, image),
        )
        resource = operation.get("resourceId")
        if resource is not None:
            identifier = project_uuid(resource)
        record, health = wait_ready(apps, tenant=tenant, identifier=identifier, image=image)
        verify_anonymous_gate(record, http_get=http_get)
        return deployment_summary(record, health)
    except Exception as exc:
        print(json.dumps({"stage": "rdc_install", **safe_error(exc)}), flush=True)
        if attempted:
            try:
                stopped = stop_owned(apps, tenant=tenant, identifier=identifier, image=image)
                print(
                    json.dumps({"status": "RDC_ROLLBACK_SUSPENDED", "container_id": stopped["id"]}),
                    flush=True,
                )
            except Exception as cleanup:
                print(json.dumps({"stage": "rdc_cleanup", **safe_error(cleanup)}), flush=True)
                fail("rdc_cleanup_unconfirmed")
        raise


def remaining(deadline, clock):
    value = deadline - clock()
    if value <= 0:
        fail("rdc_checkpoint_unconfirmed")
    return value


class CheckpointBudget:
    """One wall-clock bound includes IAM, inventory, S3, and runtime calls."""

    def __init__(self, apps, store, timeout, clock):
        self.apps = apps
        self.store = store
        self.timeout = timeout
        self.clock = clock

    def __enter__(self):
        self.previous_client = self.apps.client.timeout
        self.previous_store = self.store._timeout
        self.previous_handler = signal.getsignal(signal.SIGALRM)
        self.started = self.clock()
        signal.signal(signal.SIGALRM, self.expired)
        self.previous_timer = signal.setitimer(signal.ITIMER_REAL, self.timeout)
        return self.started + self.timeout

    @staticmethod
    def expired(*_args):
        fail("rdc_checkpoint_unconfirmed")

    def __exit__(self, *_args):
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, self.previous_handler)
        if self.previous_timer[0]:
            signal.setitimer(
                signal.ITIMER_REAL,
                max(0.000001, self.previous_timer[0] - (self.clock() - self.started)),
                self.previous_timer[1],
            )
        self.apps.client.timeout = self.previous_client
        self.store._timeout = self.previous_store
        return False


def cap_timeouts(apps, store, deadline, clock):
    budget = remaining(deadline, clock)
    apps.client.timeout = min(apps.client.timeout, budget)
    store._timeout = min(store._timeout, budget)


def issue_control_permit(apps, store, credentials, deadline, *, clock, sleep):
    nonce = secrets.token_hex(32)
    issued = int(time.time())
    permit = {
        "schema": 1,
        "action": "checkpoint",
        "project_id": project_uuid(apps.project_id),
        "container_name": names(apps.project_id)[0],
        "issued_at": issued,
        "expires_at": issued + 300,
        "sha256": hashlib.sha256(nonce.encode("ascii")).hexdigest(),
    }
    content = (json.dumps(permit, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    cap_timeouts(apps, store, deadline, clock)
    store.upload(CONTROL_FILE, content, credentials=credentials)
    visibility_deadline = min(deadline, clock() + 10)
    while clock() < visibility_deadline:
        cap_timeouts(apps, store, visibility_deadline, clock)
        try:
            actual = store.download(CONTROL_FILE, credentials=credentials)
        except StorageObjectNotFound:
            actual = None
        if clock() >= visibility_deadline:
            break
        if isinstance(actual, bytes) and len(actual) <= 4096 and actual == content:
            return nonce
        sleep(min(1, visibility_deadline - clock()))
    fail("rdc_control_unconfirmed")


def transient_runtime_error(exc):
    return isinstance(exc, requests.RequestException) or (
        isinstance(exc, CloudProviderError)
        and (
            exc.http_status in (404, 502, 503, 504)
            or (exc.code == "provider_http_error" and exc.http_status is None)
        )
    )


def checkpoint_identity(before, after):
    if (before["device_id"] is not None and before["device_id"] != after["device_id"]) or (
        before["paired"] and not after["paired"]
    ):
        fail("rdc_checkpoint_unconfirmed")


def checkpoint(
    apps,
    store,
    credentials,
    *,
    tenant,
    timeout=CHECKPOINT_BUDGET,
    clock=time.monotonic,
    sleep=time.sleep,
):
    configured_tenant(tenant)
    if not 0 < timeout <= CHECKPOINT_BUDGET:
        fail("validation_error")
    with CheckpointBudget(apps, store, timeout, clock) as deadline:
        cap_timeouts(apps, store, deadline, clock)
        require_owned_bucket(store, credentials, apps.project_id)
        record = None
        before = None
        while clock() < deadline:
            cap_timeouts(apps, store, deadline, clock)
            current = owned_record(
                apps,
                tenant=tenant,
                identifier=record["id"] if record else None,
                image=record["template"]["containers"][0]["image"] if record else None,
            )
            if current is None:
                fail("rdc_ownership_unconfirmed")
            record = current
            if str(record.get("status", "")).lower() in {
                "rejected",
                "deleted",
                "suspended",
                "suspended_product",
            }:
                fail("rdc_runtime_failed")
            try:
                value = test_call(apps, "/healthz", timeout=min(20, remaining(deadline, clock)))
            except Exception as exc:
                if not transient_runtime_error(exc):
                    raise
                value = None
            if value is not None:
                summary = health_summary(value)
                if summary["quiesced"] or all(
                    summary[key] for key in ("browser_ready", "rdc_running", "state_ready")
                ):
                    before = summary
                    break
            sleep(min(2, remaining(deadline, clock)))
        if before is None:
            fail("rdc_checkpoint_unconfirmed")
        nonce = issue_control_permit(apps, store, credentials, deadline, clock=clock, sleep=sleep)
        control_deadline = min(deadline, clock() + 10)
        receipt = None
        current_health = before
        while clock() < deadline:
            cap_timeouts(apps, store, deadline, clock)
            current = owned_record(
                apps,
                tenant=tenant,
                identifier=record["id"],
                image=record["template"]["containers"][0]["image"],
            )
            if current is None:
                fail("rdc_ownership_unconfirmed")
            checkpoint_identity(before, current_health)
            if receipt is None:
                try:
                    result = test_call(
                        apps,
                        "/checkpoint",
                        "POST",
                        nonce=nonce,
                        timeout=remaining(deadline, clock),
                    )
                except Exception as exc:
                    if (
                        isinstance(exc, CloudProviderError)
                        and exc.code == "rdc_control_unconfirmed"
                    ):
                        if clock() >= control_deadline:
                            raise
                        sleep(min(1, remaining(control_deadline, clock)))
                        continue
                    if not transient_runtime_error(exc):
                        raise
                    result = None
                if result is not None:
                    generation = result.get("generation")
                    if (
                        set(result) != {"status", "generation"}
                        or result.get("status") != "CHECKPOINT_COMPLETE"
                        or type(generation) is not int
                        or not 1 <= generation < 2**53
                        or (before["quiesced"] and generation != before["checkpoint_generation"])
                        or (
                            not before["quiesced"] and generation <= before["checkpoint_generation"]
                        )
                    ):
                        fail("rdc_checkpoint_unconfirmed")
                    receipt = generation
            try:
                value = test_call(apps, "/healthz", timeout=min(20, remaining(deadline, clock)))
            except Exception as exc:
                if not transient_runtime_error(exc):
                    raise
                value = None
            if value is not None:
                current_health = health_summary(value)
                checkpoint_identity(before, current_health)
                if receipt is not None and current_health["quiesced"]:
                    if current_health["checkpoint_generation"] != receipt:
                        fail("rdc_checkpoint_unconfirmed")
                    return current, current_health, receipt
            sleep(min(2, remaining(deadline, clock)))
        fail("rdc_checkpoint_unconfirmed")


def restart(apps, store, credentials, *, tenant, http_get=requests.get):
    record, before, generation = checkpoint(apps, store, credentials, tenant=tenant)
    identifier = record["id"]
    image = record["template"]["containers"][0]["image"]
    stop_owned(apps, tenant=tenant, identifier=identifier, image=image)
    # A confirmed suspension fences the old revision before creating another writer.
    try:
        apps.start(record["name"])
        after, health = wait_ready(apps, tenant=tenant, identifier=identifier, image=image)
        if (
            health["checkpoint_generation"] < generation
            or health["device_id"] != before["device_id"]
            or (before["paired"] and not health["paired"])
        ):
            fail("rdc_restore_unconfirmed")
        verify_anonymous_gate(after, http_get=http_get)
        return {**deployment_summary(after, health), "restart_verified": True}
    except Exception as exc:
        print(json.dumps({"stage": "rdc_restart", **safe_error(exc)}), flush=True)
        stop_owned(apps, tenant=tenant, identifier=identifier, image=image)
        raise


def resume(apps, store, credentials, *, tenant, http_get=requests.get):
    configured_tenant(tenant)
    require_owned_bucket(store, credentials, apps.project_id)
    record = owned_record(apps, tenant=tenant)
    if record is None or str(record.get("status", "")).lower() != "suspended":
        fail("rdc_not_suspended")
    identifier = record["id"]
    image = record["template"]["containers"][0]["image"]
    try:
        apps.start(record["name"])
        after, health = wait_ready(apps, tenant=tenant, identifier=identifier, image=image)
        verify_anonymous_gate(after, http_get=http_get)
        return deployment_summary(after, health)
    except Exception as exc:
        print(json.dumps({"stage": "rdc_start", **safe_error(exc)}), flush=True)
        stop_owned(apps, tenant=tenant, identifier=identifier, image=image)
        raise


DIAGNOSTIC_TERMS = {
    "container",
    "image",
    "pull",
    "error",
    "failed",
    "permission",
    "denied",
    "cannot",
    "mount",
    "volume",
    "bucket",
    "s3",
    "state",
    "read",
    "write",
    "readonly",
    "only",
    "not",
    "found",
    "missing",
    "invalid",
    "empty",
    "mismatch",
    "unavailable",
    "timeout",
    "memory",
    "cpu",
    "scheduling",
    "insufficient",
    "replica",
    "revision",
    "ready",
    "running",
    "probe",
    "liveness",
    "readiness",
    "port",
    "connection",
    "refused",
    "registry",
    "unauthorized",
    "forbidden",
    "secret",
    "credential",
    "credentials",
    "token",
    "authentication",
    "account",
    "certificate",
    "x509",
    "tls",
    "ssl",
    "namespace",
    "sandbox",
    "operation",
    "permitted",
    "user",
    "uid",
    "gid",
    "root",
    "node",
    "exit",
    "exited",
    "termination",
    "terminated",
    "backoff",
    "config",
    "configuration",
    "storage",
    "quota",
    "exceeded",
    "access",
    "argument",
    "arguments",
}


def diagnostic_terms(value):
    if not isinstance(value, str) or len(value) > 8192:
        return []
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    return sorted(set(re.findall(r"[a-z0-9]+", value.lower())) & DIAGNOSTIC_TERMS)


def revision_diagnostics(apps, record, *, clock=time.monotonic):
    """Bounded provider revision metadata only; never application logs or raw reasons."""
    deadline = clock() + 60
    previous_timeout = apps.client.timeout
    name = names(apps.project_id)[0]

    def read(path, params):
        budget = deadline - clock()
        if budget <= 0:
            fail("rdc_readiness_timeout")
        apps.client.timeout = min(previous_timeout, budget)
        payload = apps.client.request("container_apps", "GET", path, params=params)
        if clock() >= deadline:
            fail("rdc_readiness_timeout")
        return payload

    try:
        payload = read(
            f"/v2/containers/{name}/revisions", {"projectId": apps.project_id, "pageSize": "3"}
        )
        data = payload.get("data")
        if (
            not isinstance(data, list)
            or len(data) > 3
            or any(not isinstance(item, dict) for item in data)
        ):
            fail("invalid_response")
        for item in data:
            identifier = project_uuid(item.get("id"))
            detail = read(
                f"/v2/containers/{name}/revisions/{identifier}", {"projectId": apps.project_id}
            )
            if (
                detail.get("id") != identifier
                or detail.get("projectId") != apps.project_id
                or detail.get("serverlessId") != record["id"]
            ):
                fail("invalid_response")
            state = detail.get("status")
            state = state.lower() if isinstance(state, str) else "other"
            if state not in {
                "running",
                "pending",
                "creating",
                "starting",
                "stopping",
                "stopped",
                "failed",
                "error",
                "ready",
                "rejected",
                "deleted",
                "suspended",
            }:
                state = "other"
            reason = detail.get("statusReason")
            if "statusReason" not in detail:
                reason_form = "missing"
            elif reason is None:
                reason_form = "null"
            elif not isinstance(reason, str):
                fail("invalid_response")
            elif len(reason) > 8192:
                reason_form = "oversize"
            else:
                reason_form = "text" if reason else "empty"
            reason = reason if reason_form == "text" else ""
            terms = diagnostic_terms(reason)
            reason = reason.lower()
            patterns = {
                "image_pull": ("imagepullbackoff", "errimagepull", "image pull", "pull image"),
                "process_exit": ("crashloopbackoff", "process exited", "container terminated"),
                "volume_mount": ("failedmount", "mountvolume", "volume mount", "state_not_mounted"),
                "memory": ("oomkilled", "out of memory"),
                "scheduling": ("failedscheduling", "insufficient"),
                "permission": ("permission denied", "access denied", "unauthorized", "forbidden"),
                "state_restore": (
                    "state_corrupt",
                    "state_write_failed",
                    "ownership_mismatch",
                    "local_state_not_empty",
                    "state_rollback_failed",
                ),
                "health_probe": ("unhealthy", "health probe", "readiness probe", "liveness probe"),
            }
            categories = sorted(
                key for key, tokens in patterns.items() if any(token in reason for token in tokens)
            )
            print(
                json.dumps(
                    {
                        "stage": "rdc_revision_diagnostic",
                        "revision_id": identifier,
                        "resource_state": state,
                        "reason_categories": categories or ["other"],
                        "reason_form": reason_form,
                        "reason_terms": terms,
                    }
                ),
                flush=True,
            )
        payload = read(
            f"/v2/containers/{name}/systemLogs",
            {"projectId": apps.project_id, "serverlessId": record["id"]},
        )
        data = payload.get("data")
        if not isinstance(data, list):
            fail("invalid_response")
        events = data[:100]
        if any(
            not isinstance(event, dict) or event.get("serverlessId") != record["id"]
            for event in events
        ):
            fail("invalid_response")
        known_reasons = {
            "FailedMount",
            "Failed",
            "BackOff",
            "ImagePullBackOff",
            "ErrImagePull",
            "CrashLoopBackOff",
            "CreateContainerConfigError",
            "RunContainerError",
            "FailedScheduling",
            "FailedCreate",
            "SandboxChanged",
            "FailedKillPod",
            "FailedCreatePodSandBox",
            "Unhealthy",
            "OOMKilled",
            "Evicted",
            "ProgressDeadlineExceeded",
            "ContainerCreating",
            "Ready",
            "Pulling",
            "Pulled",
            "Created",
            "Started",
            "Killing",
            "Scheduled",
            "SuccessfulCreate",
            "SuccessfulDelete",
            "ScalingReplicaSet",
            "FailedAttachVolume",
            "FailedMapVolume",
            "FailedBinding",
            "FailedProvisioning",
            "ProvisioningFailed",
            "VolumeMountError",
            "ImagePullError",
            "ContainerCreationFailed",
            "RevisionFailed",
            "DeploymentError",
        }
        reasons = set()
        terms = set()
        for event in events:
            reason = event.get("reason")
            reasons.add(reason if isinstance(reason, str) and reason in known_reasons else "other")
            terms.update(diagnostic_terms(reason))
            terms.update(diagnostic_terms(event.get("message")))
        print(
            json.dumps(
                {
                    "stage": "rdc_system_event_diagnostic",
                    "events_examined": len(events),
                    "response_truncated": len(data) > 100,
                    "known_reasons": sorted(reasons),
                    "event_terms": sorted(terms),
                }
            ),
            flush=True,
        )
    finally:
        apps.client.timeout = previous_timeout


def status(apps, *, tenant, http_get=requests.get):
    configured_tenant(tenant)
    record = owned_record(apps, tenant=tenant)
    if record is None:
        return {"status": "RDC_ABSENT"}
    resource_state = str(record.get("status", "")).lower()
    known_states = {
        "running",
        "suspended",
        "pending",
        "created",
        "creating",
        "deploying",
        "starting",
        "stopping",
        "stopped",
        "failed",
        "error",
        "ready",
        "rejected",
        "deleted",
        "suspended_product",
    }
    print(
        json.dumps(
            {
                "stage": "rdc_owned_service",
                "container_name": names(apps.project_id)[0],
                "container_id": str(UUID(record["id"])),
                "resource_state": resource_state if resource_state in known_states else "other",
            }
        ),
        flush=True,
    )
    if resource_state in {"error", "failed"}:
        try:
            revision_diagnostics(apps, record)
        except CloudProviderError as exc:
            print(json.dumps({"stage": "rdc_revision_inspection", **safe_error(exc)}), flush=True)
    if resource_state == "suspended":
        return {
            "status": "RDC_SUSPENDED",
            "container_name": record["name"],
            "container_id": record["id"],
        }
    record, health = wait_ready(
        apps,
        tenant=tenant,
        identifier=record["id"],
        image=record["template"]["containers"][0]["image"],
        allow_quiesced=True,
    )
    verify_anonymous_gate(record, http_get=http_get)
    return deployment_summary(record, health)


def suspend(apps, store, credentials, *, tenant):
    configured_tenant(tenant)
    require_owned_bucket(store, credentials, apps.project_id)
    record = owned_record(apps, tenant=tenant)
    if record is None:
        fail("rdc_ownership_unconfirmed")
    if str(record.get("status", "")).lower() == "suspended":
        return {
            "status": "RDC_SUSPENDED",
            "container_name": record["name"],
            "container_id": record["id"],
        }
    record, _, generation = checkpoint(apps, store, credentials, tenant=tenant)
    stopped = stop_owned(
        apps,
        tenant=tenant,
        identifier=record["id"],
        image=record["template"]["containers"][0]["image"],
    )
    return {
        "status": "RDC_SUSPENDED",
        "container_name": stopped["name"],
        "container_id": stopped["id"],
        "checkpoint_generation": generation,
    }


RDC_ERROR_CODES = {
    "rdc_ownership_unconfirmed",
    "rdc_ingress_unconfirmed",
    "rdc_runtime_failed",
    "rdc_readiness_timeout",
    "rdc_cleanup_unconfirmed",
    "rdc_bucket_not_private",
    "rdc_bucket_ownership_unconfirmed",
    "rdc_checkpoint_unconfirmed",
    "rdc_restore_unconfirmed",
    "storage_tenant_not_configured",
    "rdc_storage_unavailable",
    "rdc_not_suspended",
    "rdc_control_unconfirmed",
}


def safe_error(exc):
    result = probe_error_details(exc)
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in RDC_ERROR_CODES:
        result["error"] = code
    current = exc
    fields_allowed = {
        "method",
        "path",
        "headers",
        "queryStringParams",
        "body",
        "isBase64Encoded",
        "name",
        "projectId",
    }
    words_allowed = {
        "method",
        "unsupported",
        "must",
        "get",
        "post",
        "only",
        "disabled",
        "enabled",
        "auth",
        "authorization",
        "authentication",
        "not",
        "cannot",
        "call",
        "container",
        "suspended",
        "started",
        "running",
        "headers",
        "header",
        "body",
        "path",
        "request",
        "json",
        "invalid",
        "empty",
        "missing",
        "test",
        "allowed",
        "access",
        "public",
        "private",
        "protocol",
        "http",
        "https",
        "deployment",
        "ready",
        "id",
        "project",
        "service",
    }
    for _ in range(4):
        response = current.response if isinstance(current, requests.RequestException) else None
        if response is not None and len(response.content) <= 65536:
            try:
                payload = response.json()
            except (ValueError, RecursionError):
                payload = None
            if isinstance(payload, dict):
                message = payload.get("message")
                if isinstance(message, str):
                    words = sorted(set(re.findall(r"[a-z]+", message.lower())) & words_allowed)
                    if words:
                        result["provider_error_terms"] = words
                fields = set(result.get("provider_validation_fields", []))
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
                        if isinstance(field, str) and field in fields_allowed:
                            fields.add(field)
                if fields:
                    result["provider_validation_fields"] = sorted(fields)
            break
        current = current.__cause__
        if current is None:
            break
    if exc.__class__.__module__ == "storage":
        result["error"] = "rdc_storage_unavailable"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("preflight", "install", "status", "start", "restart", "stop")
    )
    parser.add_argument("--sha", required=True)
    parser.add_argument("--tenant-id", default=os.environ.get("CLOUDRU_STORAGE_TENANT_ID", ""))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root, text=True
    )
    if not re.fullmatch("[0-9a-f]{40}", args.sha) or actual != args.sha or dirty:
        fail("validation_error")
    if any(
        not os.environ.get(key)
        for key in ("CLOUDRU_PROJECT_ID", "CLOUDRU_IAM_KEY_ID", "CLOUDRU_IAM_KEY_SECRET")
    ):
        fail("auth_not_configured")
    project = project_uuid(os.environ["CLOUDRU_PROJECT_ID"])
    tenant = configured_tenant(args.tenant_id)
    store, credentials = storage_client(project, tenant)
    apps = CloudRuContainerAppsClient(project_id=project)
    if args.action in ("preflight", "install"):
        result = preflight(apps, store, credentials, tenant=tenant)
        if args.action == "install":
            if result["container_exists"]:
                fail("already_exists")
            image = build_image(root, args.sha)
            creation_body(project, image)
            print(json.dumps({"stage": "rdc_image_ready", "image": image}), flush=True)
            result = install(apps, store, credentials, image, tenant=tenant)
    elif args.action == "status":
        result = status(apps, tenant=tenant)
    elif args.action == "restart":
        result = restart(apps, store, credentials, tenant=tenant)
    elif args.action == "start":
        result = resume(apps, store, credentials, tenant=tenant)
    else:
        result = suspend(apps, store, credentials, tenant=tenant)
    print(json.dumps(result), flush=True)


def cli():
    try:
        main()
    except Exception as exc:
        print(json.dumps(safe_error(exc)), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli())
