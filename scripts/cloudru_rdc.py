#!/usr/bin/env python3
"""Reviewed, single-writer Cloud.ru RDC deployment. Never prints auth state or logs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
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
}
IMAGE_RE = re.compile(re.escape(f"{REGISTRY}.cr.cloud.ru/{REPOSITORY}@sha256:") + "[0-9a-f]{64}")


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


def owned_record(apps, *, identifier=None, image=None):
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
            and not (set(attributes) - {"bucketName", "entrypoint", "tenantId", "region"})
            and attributes.get("entrypoint", "https://s3.cloud.ru") == "https://s3.cloud.ru"
            and attributes.get("region", "ru-central-1") == "ru-central-1"
        )
        if "tenantId" in attributes:
            project_uuid(attributes["tenantId"])
        mount_ok = (
            len(mounts) == 1
            and mounts[0].get("name") == VOLUME
            and mounts[0].get("mountPath") == MOUNT
            and mounts[0].get("readOnly", False) is False
            and mounts[0].get("subPath", "") == ""
        )
        valid = (
            record.get("projectId") == project
            and record.get("description") == DESCRIPTION
            and (identifier is None or identifier == actual_id)
            and (image is None or image == container["image"])
            and ingress.get("publiclyAccessible") is True
            and ingress.get("accessSettings", {}).get("enableAuth") is True
            and configuration.get("autoDeployments", {}).get("enabled", False) is False
            and configuration.get("privileged", False) is False
            and len(containers) == 1
            and len(variables) == len(environment)
            and environment == runtime_env(project)
            and container.get("name") == names(project)[0]
            and type(container.get("containerPort")) is int
            and container["containerPort"] == 8080
            and container.get("resources") == {"cpu": "1", "memory": "4096Mi"}
            and type(scaling.get("minInstanceCount")) is int
            and type(scaling.get("maxInstanceCount")) is int
            and scaling["minInstanceCount"] == scaling["maxInstanceCount"] == 1
            and volume_ok
            and mount_ok
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


def test_call(apps, path, method="GET"):
    if (path, method) not in {("/healthz", "GET"), ("/checkpoint", "POST")}:
        fail("validation_error")
    name = names(apps.project_id)[0]
    previous_timeout = apps.client.timeout
    try:
        # Closed-state checkpoint can take a minute; only this request gets that budget.
        if path == "/checkpoint":
            apps.client.timeout = max(previous_timeout, 180)
        payload = apps.client.request(
            "container_apps",
            "POST",
            f"/v2/containers/{name}:testCall",
            json_body={"name": name, "projectId": apps.project_id, "method": method, "path": path},
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
    if status in (404, 502, 503, 504):
        return None
    if status != 200:
        fail("rdc_runtime_failed")
    try:
        result = json.loads(body)
    except (ValueError, RecursionError):
        fail("invalid_response")
    if not isinstance(result, dict):
        fail("invalid_response")
    return result


def health_summary(value):
    if not isinstance(value, dict) or set(value) != HEALTH_KEYS or value.get("mode") != "cloud-rdc":
        fail("invalid_response")
    if any(
        type(value.get(key)) is not bool
        for key in ("browser_ready", "rdc_running", "state_ready", "paired")
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
    return value


def wait_ready(apps, *, identifier=None, image=None, timeout=300, sleep=time.sleep):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = owned_record(apps, identifier=identifier, image=image)
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
                if summary["browser_ready"] and summary["rdc_running"] and summary["state_ready"]:
                    return record, summary
        sleep(2)
    fail("rdc_readiness_timeout")


def stop_owned(apps, *, identifier=None, image=None, timeout=300, sleep=time.sleep):
    # Discovery is bounded even after a possibly committed create; never retry creation.
    deadline = time.monotonic() + timeout
    record = None
    while time.monotonic() < deadline:
        record = owned_record(apps, identifier=identifier, image=image)
        if record is not None:
            break
        sleep(2)
    if record is None:
        fail("rdc_cleanup_unconfirmed")
    identifier = record["id"]
    if str(record.get("status", "")).lower() != "suspended":
        apps.stop(record["name"])
    while time.monotonic() < deadline:
        current = owned_record(apps, identifier=identifier, image=image)
        if current and str(current.get("status", "")).lower() == "suspended":
            return current
        sleep(2)
    fail("rdc_cleanup_unconfirmed")


def storage_client(project, tenant, env=os.environ):
    project_uuid(tenant)
    credentials = cloudru_s3_credentials(
        env.get("CLOUDRU_IAM_KEY_ID", ""), env.get("CLOUDRU_IAM_KEY_SECRET", ""), tenant
    )
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


def preflight(apps, store, credentials):
    record = named_record(apps)
    if record is not None:
        owned_record(apps)
    exists = bucket_inventory(store, credentials, apps.project_id)
    return {
        "status": "PREFLIGHT_PASSED",
        "container_exists": record is not None,
        "bucket_exists": exists,
    }


def deployment_summary(record, health):
    return {
        "status": "RDC_RUNNING" if health["paired"] else "PAIRING_REQUIRED",
        "container_name": record["name"],
        "container_id": record["id"],
        "pairing_url": application_origin(record) + "/rdc/pair",
        **health,
    }


def install(apps, store, credentials, image, *, http_get=requests.get):
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
        record, health = wait_ready(apps, identifier=identifier, image=image)
        verify_anonymous_gate(record, http_get=http_get)
        return deployment_summary(record, health)
    except Exception as exc:
        print(json.dumps({"stage": "rdc_install", **safe_error(exc)}), flush=True)
        if attempted:
            try:
                stopped = stop_owned(apps, identifier=identifier, image=image)
                print(
                    json.dumps({"status": "RDC_ROLLBACK_SUSPENDED", "container_id": stopped["id"]}),
                    flush=True,
                )
            except Exception as cleanup:
                print(json.dumps({"stage": "rdc_cleanup", **safe_error(cleanup)}), flush=True)
                fail("rdc_cleanup_unconfirmed")
        raise


def checkpoint(apps):
    record, before = wait_ready(apps)
    result = test_call(apps, "/checkpoint", "POST")
    if (
        not isinstance(result, dict)
        or set(result) != {"status", "generation"}
        or result.get("status") != "CHECKPOINT_COMPLETE"
        or type(result.get("generation")) is not int
        or not before["checkpoint_generation"] < result["generation"] < 2**53
    ):
        fail("rdc_checkpoint_unconfirmed")
    return record, before, result["generation"]


def restart(apps, *, http_get=requests.get):
    record, before, generation = checkpoint(apps)
    identifier = record["id"]
    image = record["template"]["containers"][0]["image"]
    stop_owned(apps, identifier=identifier, image=image)
    # A confirmed suspension fences the old revision before creating another writer.
    try:
        apps.start(record["name"])
        after, health = wait_ready(apps, identifier=identifier, image=image)
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
        stop_owned(apps, identifier=identifier, image=image)
        raise


def resume(apps, *, http_get=requests.get):
    record = owned_record(apps)
    if record is None or str(record.get("status", "")).lower() != "suspended":
        fail("rdc_not_suspended")
    identifier = record["id"]
    image = record["template"]["containers"][0]["image"]
    try:
        apps.start(record["name"])
        after, health = wait_ready(apps, identifier=identifier, image=image)
        verify_anonymous_gate(after, http_get=http_get)
        return deployment_summary(after, health)
    except Exception as exc:
        print(json.dumps({"stage": "rdc_start", **safe_error(exc)}), flush=True)
        stop_owned(apps, identifier=identifier, image=image)
        raise


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
}


def safe_error(exc):
    result = probe_error_details(exc)
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in RDC_ERROR_CODES:
        result["error"] = code
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
    apps = CloudRuContainerAppsClient(project_id=project)
    if args.action in ("preflight", "install"):
        if not args.tenant_id:
            fail("storage_tenant_not_configured")
        store, credentials = storage_client(project, args.tenant_id)
        result = preflight(apps, store, credentials)
        if args.action == "install":
            if result["container_exists"]:
                fail("already_exists")
            image = build_image(root, args.sha)
            creation_body(project, image)
            print(json.dumps({"stage": "rdc_image_ready", "image": image}), flush=True)
            result = install(apps, store, credentials, image)
    elif args.action == "status":
        record = owned_record(apps)
        if record is None:
            result = {"status": "RDC_ABSENT"}
        elif str(record.get("status", "")).lower() == "suspended":
            result = {
                "status": "RDC_SUSPENDED",
                "container_name": record["name"],
                "container_id": record["id"],
            }
        else:
            record, health = wait_ready(apps, identifier=record["id"])
            verify_anonymous_gate(record)
            result = deployment_summary(record, health)
    elif args.action == "restart":
        result = restart(apps)
    elif args.action == "start":
        result = resume(apps)
    else:
        record, _, generation = checkpoint(apps)
        stopped = stop_owned(apps, identifier=record["id"])
        result = {
            "status": "RDC_SUSPENDED",
            "container_id": stopped["id"],
            "checkpoint_generation": generation,
        }
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
