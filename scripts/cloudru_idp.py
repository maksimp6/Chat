#!/usr/bin/env python3
"""Deploy the central Alice OAuth identity provider to its own Container Apps service.

The IdP is stateless (sealed tokens, keys derived from one secret), so the container
has no volume and scales to zero. Two lanes keep experiments away from production:

* ``production`` signs the owner in with GitHub and can never carry the test passphrase.
* ``test`` signs the owner in with a passphrase and never receives GitHub credentials.

Each lane trusts only the Chrome worker of the same lane (its MCP URL becomes the
token audience), so a test token cannot open the production browser.
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
from cloud.cloudru.registry_client import CloudRuRegistryClient  # noqa: E402
from scripts.cloudru_chrome import REGISTRY, prepare_registry, provider  # noqa: E402
from scripts.cloudru_deploy import _export_commit, _load_cloudru_credentials  # noqa: E402
from scripts.cloudru_rdc import application_origin  # noqa: E402

REPOSITORY = "oauth-idp"
DESCRIPTION = "Alice central OAuth identity provider"
CPU = "0.1"
MEMORY = "256Mi"
IMAGE_RE = re.compile(re.escape(f"{REGISTRY}.cr.cloud.ru/{REPOSITORY}@sha256:") + "[0-9a-f]{64}")
SCOPES = "browser.read,browser.control"
DEFAULT_OWNER = "293531601"
# Provider origins plus the owner's own domain (set up in the Cloud.ru console).
PROVIDER_SUFFIXES = (".containerapps.ru", ".containers.cloud.ru")
OWNER_DOMAIN = "maxxxpavlov.online"
MIN_SECRET = 32
MIN_PASSPHRASE = 16
APP_ENV = frozenset(
    {
        "IDP_SECRET",
        "IDP_PUBLIC_URL",
        "IDP_DEPLOYMENT_SHA",
        "IDP_ALLOWED_GITHUB_IDS",
        "IDP_ALLOWED_RESOURCES",
        "IDP_SCOPES",
        "IDP_GITHUB_CLIENT_ID",
        "IDP_GITHUB_CLIENT_SECRET",
        "IDP_OWNER_PASSPHRASE",
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
        "idp_ownership_unconfirmed",
        "idp_readiness_timeout",
        "idp_stop_timeout",
        "idp_rollback_failed",
        "idp_ingress_unconfirmed",
        "idp_resource_unconfirmed",
    }
)


def fail(code):
    raise CloudProviderError("IdP operation could not be verified", code=code)


def uuid(value):
    try:
        result = str(UUID(value))
    except (TypeError, ValueError, AttributeError):
        fail("validation_error")
    if result != value:
        fail("validation_error")
    return result


def lane(env=os.environ):
    value = env.get("IDP_LANE") or "production"
    if value not in ("production", "test"):
        fail("validation_error")
    return value


def suffix(project):
    return uuid(project).replace("-", "")[:12]


def container_name(project, lane_name):
    return ("idp-test-" if lane_name == "test" else "idp-") + suffix(project)


def chrome_name(project, lane_name):
    return ("chrome-test-" if lane_name == "test" else "chrome-") + suffix(project)


def trusted_host(host):
    return bool(host) and (
        host.endswith(PROVIDER_SUFFIXES)
        or host == OWNER_DOMAIN
        or host.endswith("." + OWNER_DOMAIN)
    )


def https_origin(value):
    try:
        parts = urlsplit(value)
        valid = (
            parts.scheme == "https"
            and trusted_host(parts.hostname)
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
    return "https://" + parts.hostname


def resource_url(value):
    try:
        parts = urlsplit(value)
        valid = (
            parts.scheme == "https"
            and trusted_host(parts.hostname)
            and not parts.username
            and not parts.password
            and parts.port in (None, 443)
            and parts.path == "/browser/v1/mcp"
            and not parts.query
            and not parts.fragment
        )
    except (TypeError, ValueError, AttributeError):
        fail("validation_error")
    if not valid:
        fail("validation_error")
    return "https://" + parts.hostname + parts.path


def runtime_env(sha, lane_name, env, *, public_url, resources):
    if lane_name not in ("production", "test") or not re.fullmatch(r"[0-9a-f]{40}", sha):
        fail("validation_error")
    secret = env.get("IDP_SECRET", "")
    owner = env.get("ALICE_GITHUB_ALLOWED_IDS") or DEFAULT_OWNER
    if not re.fullmatch(r"[1-9][0-9]*", owner) or not resources:
        fail("validation_error")
    if len(secret) < MIN_SECRET:
        fail("auth_not_configured")
    result = {
        "IDP_SECRET": secret,
        "IDP_DEPLOYMENT_SHA": sha,
        "IDP_ALLOWED_GITHUB_IDS": owner,
        "IDP_ALLOWED_RESOURCES": ",".join(resource_url(item) for item in resources),
        "IDP_SCOPES": SCOPES,
    }
    if lane_name == "test":
        # Test never gets GitHub credentials, even if the caller supplied them.
        passphrase = env.get("IDP_OWNER_PASSPHRASE", "")
        if len(passphrase) < MIN_PASSPHRASE:
            fail("auth_not_configured")
        result["IDP_OWNER_PASSPHRASE"] = passphrase
    else:
        # Production never gets the passphrase, even if the caller supplied it.
        for key in ("IDP_GITHUB_CLIENT_ID", "IDP_GITHUB_CLIENT_SECRET"):
            if not env.get(key):
                fail("auth_not_configured")
            result[key] = env[key]
    if public_url:
        result["IDP_PUBLIC_URL"] = https_origin(public_url)
    return result


def creation_body(project, lane_name, image, environment):
    if not isinstance(image, str) or not IMAGE_RE.fullmatch(image):
        fail("validation_error")
    if set(environment) - APP_ENV or not environment.get("IDP_SECRET"):
        fail("validation_error")
    name = container_name(project, lane_name)
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
            "timeout": "60s",
            "idleTimeout": "300s",
            "protocol": "http_1",
            "scaling": {"minInstanceCount": 0, "maxInstanceCount": 1},
            "containers": [spec.container_body()],
        },
    }


def record_env(record):
    return {item["name"]: item["value"] for item in record["template"]["containers"][0]["env"]}


def owned_record(apps, lane_name, *, identifier=None):
    name = container_name(apps.project_id, lane_name)
    matches = [item for item in provider(apps.list, require_total=True) if item.get("name") == name]
    if not matches:
        return None
    if len(matches) != 1:
        fail("invalid_response")
    record = matches[0]
    try:
        template = record["template"]
        containers = template["containers"]
        container = containers[0]
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
            and container.get("resources") == {"cpu": CPU, "memory": MEMORY}
            and len(variables) == len(environment)
            and not (set(environment) - APP_ENV)
            and bool(environment.get("IDP_SECRET"))
            and template["scaling"].get("minInstanceCount", 0) in (0, 1)
            and template["scaling"].get("maxInstanceCount") == 1
            and not template.get("volumes")
            and ingress.get("publiclyAccessible") is True
            and isinstance(ingress.get("accessSettings", {}).get("enableAuth"), bool)
            and record["configuration"].get("privileged", False) is False
            and record["configuration"].get("autoDeployments", {}).get("enabled", False) is False
        )
    except (KeyError, IndexError, TypeError, AttributeError):
        fail("idp_ownership_unconfirmed")
    if not valid:
        fail("idp_ownership_unconfirmed")
    return record


def chrome_resource(apps, lane_name):
    # The IdP issues tokens only for the Chrome worker of the same lane.
    name = chrome_name(apps.project_id, lane_name)
    matches = [item for item in provider(apps.list, require_total=True) if item.get("name") == name]
    if len(matches) != 1:
        fail("idp_resource_unconfirmed")
    return application_origin(matches[0]) + "/browser/v1/mcp"


def verify_ingress(record, lane_name, *, http_get=requests.get):
    # The provider origin must serve this exact revision in this lane's sign-in mode,
    # advertise the configured issuer, and publish exactly one Ed25519 signing key.
    origin = application_origin(record)
    environment = record_env(record)
    try:
        health = http_get(origin + "/healthz", timeout=10, allow_redirects=False)
        value = health.json()
        expected_mode = "passphrase" if lane_name == "test" else None
        healthy = (
            health.status_code == 200
            and value.get("status") == "ok"
            and value.get("deployment_sha") == environment.get("IDP_DEPLOYMENT_SHA")
            and value.get("sign_in") == expected_mode
        )
        metadata = http_get(
            origin + "/.well-known/oauth-authorization-server", timeout=10, allow_redirects=False
        )
        document = metadata.json()
        advertised = (
            metadata.status_code == 200
            and document.get("issuer") == environment.get("IDP_PUBLIC_URL")
            and "S256" in document.get("code_challenge_methods_supported", [])
        )
        jwks = http_get(origin + "/jwks.json", timeout=10, allow_redirects=False)
        keys = jwks.json().get("keys", [])
        signing = (
            jwks.status_code == 200
            and len(keys) == 1
            and keys[0].get("kty") == "OKP"
            and keys[0].get("crv") == "Ed25519"
        )
    except (requests.RequestException, ValueError, AttributeError, KeyError, TypeError):
        fail("idp_ingress_unconfirmed")
    if not (healthy and advertised and signing):
        fail("idp_ingress_unconfirmed")


def wait_ready(
    apps,
    lane_name,
    *,
    identifier=None,
    image=None,
    timeout=300,
    sleep=time.sleep,
    clock=time.monotonic,
):
    deadline = clock() + timeout
    while clock() < deadline:
        record = owned_record(apps, lane_name, identifier=identifier)
        if record is not None:
            status = str(record.get("status", "")).lower()
            if status in ("rejected", "deleted", "suspended_product"):
                fail("idp_readiness_timeout")
            if status == "running" and (
                image is None or record["template"]["containers"][0]["image"] == image
            ):
                try:
                    verify_ingress(record, lane_name)
                    return record
                except (CloudProviderError, requests.RequestException):
                    pass
        sleep(2)
    fail("idp_readiness_timeout")


def wait_origin(
    apps, lane_name, identifier, *, timeout=120, sleep=time.sleep, clock=time.monotonic
):
    # A new container gets its stable origin on creation; read it to bind the issuer.
    deadline = clock() + timeout
    while clock() < deadline:
        record = owned_record(apps, lane_name, identifier=identifier)
        if record is not None:
            try:
                return record, application_origin(record)
            except CloudProviderError:
                pass
        sleep(2)
    fail("idp_readiness_timeout")


def stop_owned(apps, lane_name, record, *, timeout=300, sleep=time.sleep, clock=time.monotonic):
    current = owned_record(apps, lane_name, identifier=record["id"])
    if current is None:
        fail("idp_ownership_unconfirmed")
    if str(current.get("status", "")).lower() != "suspended":
        provider(apps.stop, current["name"])
    deadline = clock() + timeout
    while clock() < deadline:
        current = owned_record(apps, lane_name, identifier=record["id"])
        if current and str(current.get("status", "")).lower() == "suspended":
            return current
        sleep(2)
    fail("idp_stop_timeout")


def summary(record, lane_name):
    container = record["template"]["containers"][0]
    environment = record_env(record)
    issuer = environment.get("IDP_PUBLIC_URL") or application_origin(record)
    return {
        "status": "IDP_RUNNING"
        if str(record.get("status", "")).lower() == "running"
        else "IDP_STOPPED",
        "lane": lane_name,
        "container_name": record["name"],
        "container_id": uuid(record["id"]),
        "image": container["image"],
        "digest": container["image"].split("@", 1)[1],
        "provider_url": application_origin(record),
        "issuer": issuer,
        "jwks_url": issuer + "/jwks.json",
        "github_callback_url": issuer + "/github/callback" if lane_name == "production" else None,
        "cost_floor": estimate_monthly_cost(CPU, 0),
    }


def deploy(apps, lane_name, image, environment, *, pinned_url=False):
    previous = owned_record(apps, lane_name)
    if previous:
        # The container keeps its name, so its provider origin stays stable.
        previous = stop_owned(apps, lane_name, previous)
        if not pinned_url:
            environment = {**environment, "IDP_PUBLIC_URL": application_origin(previous)}
    attempted = False
    identifier = previous["id"] if previous else None
    try:
        body = creation_body(apps.project_id, lane_name, image, environment)
        attempted = True
        if previous:
            provider(apps.restore, previous["name"], body)
            provider(apps.start, previous["name"])
        else:
            operation = apps.client.request(
                "container_apps", "POST", "/v2/containers", json_body=body
            )
            if operation.get("resourceId"):
                identifier = uuid(operation["resourceId"])
            if not pinned_url:
                # Bind the issuer to the origin assigned on creation, on the same container.
                record, origin = wait_origin(apps, lane_name, identifier)
                record = stop_owned(apps, lane_name, record)
                environment = {**environment, "IDP_PUBLIC_URL": origin}
                provider(
                    apps.restore,
                    record["name"],
                    creation_body(apps.project_id, lane_name, image, environment),
                )
                provider(apps.start, record["name"])
        record = wait_ready(apps, lane_name, identifier=identifier, image=image)
        return summary(record, lane_name)
    except Exception as exc:
        print(json.dumps({"stage": "idp_deploy_failed", **safe_error(exc)}), flush=True)
        if attempted:
            try:
                current = owned_record(apps, lane_name, identifier=identifier)
                if current is not None:
                    stop_owned(apps, lane_name, current)
                if previous:
                    provider(apps.restore, previous["name"], previous)
                    if str(previous.get("status", "")).lower() == "running":
                        provider(apps.start, previous["name"])
                        wait_ready(
                            apps,
                            lane_name,
                            identifier=previous["id"],
                            image=previous["template"]["containers"][0]["image"],
                        )
                    print(json.dumps({"stage": "idp_rollback", "status": "RESTORED"}), flush=True)
                elif current is not None:
                    print(
                        json.dumps({"stage": "idp_rollback", "status": "NEW_CONTAINER_SUSPENDED"}),
                        flush=True,
                    )
                else:
                    fail("idp_rollback_failed")
            except Exception:
                fail("idp_rollback_failed")
        raise


def export_source(sha, root, dest, lane_name):
    if lane_name != "test":
        _export_commit(sha, str(root), dest)
        return
    # The test lane builds the checked-out branch commit; require_reviewed_head
    # already proved HEAD is exactly this clean commit.
    archive = subprocess.run(
        ["git", "-C", str(root), "archive", "--format=tar", sha], capture_output=True, check=False
    )
    if archive.returncode:
        fail("validation_error")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as stream:
        stream.extractall(dest, filter="data")


def build_image(root, sha, lane_name):
    registry = CloudRuRegistryClient()
    with tempfile.TemporaryDirectory(prefix="idp-build-") as exported:
        export_source(sha, root, exported, lane_name)
        prepare_registry(registry)
        context = Path(exported) / "deploy" / "oauth-idp"
        print(json.dumps({"stage": "idp_build_push"}), flush=True)
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


def require_reviewed_head(root, sha, lane_name):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        fail("validation_error")
    # Production runs only reviewed master commits; the test lane may run a branch.
    commands = (
        []
        if lane_name == "test"
        else [
            ["git", "fetch", "--no-tags", "origin", "master"],
            ["git", "merge-base", "--is-ancestor", sha, "FETCH_HEAD"],
        ]
    )
    for args in commands:
        if subprocess.run(args, cwd=root, capture_output=True, check=False).returncode:
            fail("validation_error")
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root, text=True
    )
    if actual != sha or dirty:
        fail("validation_error")


def export_result(result, env=os.environ):
    if env.get("GITHUB_ENV"):
        with open(env["GITHUB_ENV"], "a", encoding="utf-8") as stream:
            stream.write("IDP_PUBLIC_URL=" + result["issuer"] + "\n")
    if env.get("GITHUB_STEP_SUMMARY"):
        lines = [
            f"## Alice IdP ({result['lane']})\n",
            f"- Issuer: `{result['issuer']}`",
            f"- Set on the Chrome worker of this lane: `BROWSER_IDP_ISSUER={result['issuer']}`",
        ]
        if result["github_callback_url"]:
            lines.append(f"- GitHub OAuth callback: `{result['github_callback_url']}`")
        else:
            lines.append("- Sign-in: owner passphrase (test lane, no GitHub)")
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "deploy", "status", "stop"))
    parser.add_argument("--sha", required=True)
    args = parser.parse_args(argv)
    lane_name = lane()
    root = Path(__file__).resolve().parents[1]
    require_reviewed_head(root, args.sha, lane_name)
    _load_cloudru_credentials()
    project = uuid(os.environ.get("CLOUDRU_PROJECT_ID", ""))
    if any(not os.environ.get(key) for key in ("CLOUDRU_IAM_KEY_ID", "CLOUDRU_IAM_KEY_SECRET")):
        fail("auth_not_configured")
    apps = CloudRuContainerAppsClient(project_id=project)
    if args.action in ("preflight", "deploy"):
        override = os.environ.get("IDP_PUBLIC_URL", "")
        resources = [
            item for item in os.environ.get("IDP_ALLOWED_RESOURCES", "").split(",") if item
        ] or [chrome_resource(apps, lane_name)]
        environment = runtime_env(
            args.sha,
            lane_name,
            os.environ,
            public_url=override or None,
            resources=resources,
        )
        result = {
            "status": "PREFLIGHT_PASSED",
            "lane": lane_name,
            "container_exists": owned_record(apps, lane_name) is not None,
            "resources": resources,
            "cost_floor": estimate_monthly_cost(CPU, 0),
        }
        if args.action == "deploy":
            image = build_image(root, args.sha, lane_name)
            result = deploy(apps, lane_name, image, environment, pinned_url=bool(override))
            export_result(result)
    else:
        record = owned_record(apps, lane_name)
        if record is None:
            result = {
                "status": "IDP_NOT_FOUND",
                "container_name": container_name(project, lane_name),
            }
        elif args.action == "status":
            result = summary(record, lane_name)
        else:
            stop_owned(apps, lane_name, record)
            result = summary(owned_record(apps, lane_name, identifier=record["id"]), lane_name)
    print(json.dumps(result, sort_keys=True), flush=True)


def safe_error(exc):
    code = getattr(exc, "code", None)
    status = getattr(exc, "http_status", None)
    return {
        "error": code if code in SAFE_ERRORS else "idp_operation_failed",
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
