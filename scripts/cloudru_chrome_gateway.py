#!/usr/bin/env python3
"""Inventory and reconcile only the Chrome worker's Shared Gateway and DNS record.

Use only management contracts published in official Cloud.ru documentation.
Gateway and Certificate Manager REST routes have not yet been verified against
that documentation, so their requests stop before acquiring an IAM token.
No certificates are created, exported, replaced, or modified by this script.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit
from uuid import UUID

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.base import CloudProviderError  # noqa: E402
from cloud.cloudru.client import CloudRuClient  # noqa: E402
from cloud.cloudru.container_apps_client import CloudRuContainerAppsClient  # noqa: E402
from cloudru_iam import CloudRuIamClient  # noqa: E402
from scripts.chrome_gateway_spec import gateway_spec  # noqa: E402
from scripts.cloudru_deploy import _load_cloudru_credentials  # noqa: E402

ZONE = "maxxxpavlov.online"
ENDPOINTS = {
    "chrome_gateway": "https://apigw.api.cloud.ru",
    "chrome_certificates": "https://certificatemanager.api.cloud.ru",
    "chrome_dns": "https://dns.api.cloud.ru",
}
DOCUMENTATION = {
    "chrome_gateway": "https://cloud.ru/docs/api-gateway-svp/ug/topics/api-ref",
    "chrome_certificates": "https://cloud.ru/docs/certificate-manager/ug/topics/api-ref",
}
SAFE_ERRORS = frozenset(
    {
        "chrome_gateway_validation_failed",
        "chrome_gateway_preflight_failed",
        "chrome_gateway_invalid_response",
        "chrome_gateway_conflict",
        "chrome_gateway_timeout",
        "chrome_gateway_verification_failed",
        "auth_not_configured",
        "authorization_failed",
        "auth_failed",
        "provider_http_error",
        "invalid_response",
        "chrome_gateway_official_contract_unverified",
    }
)


def fail(code):
    raise CloudProviderError(code, code=code)


def identifier(value):
    try:
        if str(UUID(value)) != value:
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError):
        fail("chrome_gateway_invalid_response")


def settings(project, public_url, container_name):
    identifier(project)
    origin = urlsplit(public_url)
    if (
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", container_name)
        or public_url != f"https://{container_name}.{ZONE}"
        or origin.username
        or origin.password
    ):
        fail("chrome_gateway_validation_failed")
    return origin.hostname


def owner(project, name):
    return f"Alice Chrome Playwright MCP gateway; project={project}; container={name}"


class ChromeGatewayClient(CloudRuClient):
    def __init__(self, **kwargs):
        super().__init__(
            iam_client=kwargs.pop("iam_client", None) or CloudRuIamClient(),
            api_key_auth=False,
            **kwargs,
        )

    def endpoint(self, service):
        # Fixed official hosts avoid forwarding IAM credentials to arbitrary
        # endpoint environment overrides during deployment.
        return ENDPOINTS[service]

    def request(self, service, method, path, *, params=None, json_body=None):
        # A documented hostname does not verify a REST version, path or payload.
        # Never reuse console routes with a production IAM bearer token.
        if service in DOCUMENTATION:
            fail("chrome_gateway_official_contract_unverified")
        return super().request(service, method, path, params=params, json_body=json_body)


def items(client, service, resource, key, params, *, gateway=False):
    result = []
    offset = 0
    seen = set()
    for _ in range(1000):
        paging = (
            {"skip": offset, "pageSize": 100}
            if gateway
            else {
                "pagination.offset": offset,
                "pagination.limit": 100,
            }
        )
        value = client.request(service, "GET", resource, params={**params, **paging})
        page = value.get(key)
        total = value.get("totalSize") if gateway else value.get("pagination", {}).get("total")
        try:
            total = int(total)
        except (ValueError, TypeError):
            fail("chrome_gateway_invalid_response")
        if not isinstance(page, list) or total < 0 or total < offset + len(page):
            fail("chrome_gateway_invalid_response")
        for entry in page:
            if not isinstance(entry, dict):
                fail("chrome_gateway_invalid_response")
            resource_id = identifier(
                entry.get("id") if gateway else entry.get("meta", {}).get("id")
            )
            if resource_id in seen:
                fail("chrome_gateway_invalid_response")
            seen.add(resource_id)
        result.extend(page)
        offset += len(page)
        if offset == total:
            return result
        if not page:
            fail("chrome_gateway_invalid_response")
    fail("chrome_gateway_invalid_response")


def matches(pattern, host):
    if not isinstance(pattern, str):
        return False
    pattern = pattern.lower().rstrip(".")
    return pattern == host or (
        pattern.startswith("*.")
        and host.endswith(pattern[1:])
        and len(host.split(".")) == len(pattern.split("."))
    )


def certificate_for(certificates, host, now=None):
    now = now or datetime.now(timezone.utc)
    candidates = []
    for certificate in certificates:
        if not isinstance(certificate, dict):
            fail("chrome_gateway_invalid_response")
        version = (
            certificate.get("issueState", {}).get("current")
            if certificate.get("type") == "CERTIFICATE_TYPE_LETS_ENCRYPT"
            else certificate.get("primary")
        )
        if not isinstance(version, dict) or version.get("enabled") is not True:
            continue
        try:
            expires = datetime.fromisoformat(version["expiresAt"].replace("Z", "+00:00"))
            if expires.tzinfo is None or expires <= now:
                continue
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        names = version.get("dnsNames") or [version.get("commonName", "")]
        if isinstance(names, list) and any(matches(name, host) for name in names):
            identifier(certificate.get("id"))
            candidates.append((host in names, expires, certificate["id"], certificate))
    return max(candidates, key=lambda item: item[:3])[3] if candidates else None


def record_host(record):
    name = record.get("name")
    if not isinstance(name, str):
        fail("chrome_gateway_invalid_response")
    name = name.lower().rstrip(".")
    return name if name.endswith(f".{ZONE}") or name == ZONE else f"{name}.{ZONE}"


def system_host(gateway):
    domains = gateway.get("domains", [])
    if not isinstance(domains, list):
        fail("chrome_gateway_invalid_response")
    system = [entry.get("host") for entry in domains if entry.get("isSystem") is True]
    if (
        len(system) != 1
        or not isinstance(system[0], str)
        or not re.fullmatch(r"[a-z0-9-]+\.apigw\.cloud\.ru", system[0])
    ):
        fail("chrome_gateway_invalid_response")
    return system[0]


def preflight(client, project, public_url, container_name, env=None):
    """Read all relevant resources before proposing any mutation; never print plan."""
    env = os.environ if env is None else env
    host = settings(project, public_url, container_name)
    blockers = []
    certificates = client.request(
        "chrome_certificates", "GET", "/certificate", params={"projectId": project}
    ).get("certificates")
    if not isinstance(certificates, list):
        fail("chrome_gateway_invalid_response")
    certificate = certificate_for(certificates, host)
    if not certificate:
        blockers.append("No enabled, unexpired existing TLS certificate covers " + host)
    zones = items(client, "chrome_dns", "/v1/zones", "zones", {"projectId": project})
    matching = [
        zone
        for zone in zones
        if str(zone.get("domain", "")).rstrip(".").lower() == ZONE
        and zone.get("role") == "ROLE_PUBLIC"
    ]
    zone = matching[0] if len(matching) == 1 else None
    records = []
    if not zone:
        blockers.append("Exactly one existing public Evolution DNS zone is required: " + ZONE)
    elif zone.get("state") != "STATE_OK" or zone.get("activeState") is False:
        blockers.append("The existing Evolution DNS zone is not active")
    else:
        records = items(
            client, "chrome_dns", "/v1/public/records", "records", {"zoneId": zone["meta"]["id"]}
        )
    gateways = items(
        client, "chrome_gateway", "/api-gws", "apiGws", {"projectId": project}, gateway=True
    )
    matching = [gateway for gateway in gateways if gateway.get("name") == container_name]
    gateway = matching[0] if len(matching) == 1 else None
    if len(matching) > 1:
        blockers.append("Multiple Gateways have the Chrome container name")
    if gateway and gateway.get("description") != owner(project, container_name):
        blockers.append("The existing named Gateway is not owned by this Chrome deployment")
    for entry in gateways:
        detail = (
            entry
            if isinstance(entry.get("domains"), list)
            else client.request(
                "chrome_gateway", "GET", f"/api-gws/{entry['id']}", params={"projectId": project}
            )
        )
        if gateway and entry["id"] == gateway["id"]:
            gateway = detail
        elif any(
            domain.get("host", "").lower().rstrip(".") == host
            for domain in detail.get("domains", [])
        ):
            blockers.append("The requested hostname belongs to another Gateway")
    target = system_host(gateway) if gateway else None
    relevant = [record for record in records if record_host(record) == host]
    cname = None
    if relevant:
        if (
            len(relevant) == 1
            and relevant[0].get("type") == "PUBLIC_RECORD_TYPE_CNAME"
            and target
            and relevant[0].get("values") == [target + "."]
        ):
            cname = relevant[0]
        else:
            blockers.append(
                "Existing DNS records conflict at " + host + "; no records will be overwritten"
            )
    domains = [
        domain
        for domain in (gateway or {}).get("domains", [])
        if domain.get("host", "").lower().rstrip(".") == host
    ]
    # Reconciliation keeps the already bound certificate when still valid.
    # Discovering an additional valid certificate does not rotate existing TLS.
    if len(domains) == 1:
        bound = certificate_for(
            [entry for entry in certificates if entry.get("id") == domains[0].get("certId")], host
        )
        if bound:
            certificate = bound
    if len(domains) > 1 or any(
        domain.get("isSystem") or (certificate and domain.get("certId") != certificate["id"])
        for domain in domains
    ):
        blockers.append("The existing Gateway domain has a conflicting TLS certificate or identity")
    missing = [
        key
        for key in (
            "ALICE_GITHUB_CLIENT_ID",
            "ALICE_GITHUB_CLIENT_SECRET",
        )
        if not env.get(key)
    ]
    # Match the worker's established owner default, never a broad login policy.
    allowed_ids = env.get("ALICE_GITHUB_ALLOWED_IDS") or "293531601"
    if not re.fullmatch(r"[1-9][0-9]*", allowed_ids):
        blockers.append("ALICE_GITHUB_ALLOWED_IDS must contain one immutable numeric owner ID")
    if missing:
        blockers.append("Missing OAuth runtime configuration: " + ", ".join(missing))
    callback = public_url + "/browser/oauth/github/callback"
    summary = {
        "status": "GATEWAY_PREFLIGHT_BLOCKED" if blockers else "GATEWAY_PREFLIGHT_PASSED",
        "blockers": blockers,
        "public_url": public_url,
        "certificate_id": certificate["id"] if certificate else None,
        "gateway_exists": gateway is not None,
        "dns_record_exists": cname is not None,
        "github_callback_url": callback,
        "github_callback_verified": False,
    }
    return {
        "blockers": blockers,
        "safe_summary": summary,
        "gateway": gateway,
        "certificate": certificate,
        "zone": zone,
        "cname": cname,
        "domain": domains[0] if len(domains) == 1 else None,
    }


def verify_public(public_url, expected_sha=None, *, http_get=requests.get):
    """Default requests TLS verification is mandatory; no bearer secrets sent."""

    def get(route):
        return http_get(public_url + route, timeout=20, allow_redirects=False)

    try:
        health = get("/healthz")
        value = health.json()
        if (
            health.status_code != 200
            or value.get("status") != "ok"
            or value.get("state_ready") is not True
            or value.get("oauth_ready") is not True
            or (expected_sha and value.get("deployment_sha") != expected_sha)
        ):
            return False
        mcp = get("/browser/v1/mcp")
        if (
            mcp.status_code != 401
            or f"{public_url}/.well-known/oauth-protected-resource"
            not in mcp.headers.get("WWW-Authenticate", "")
        ):
            return False
        if get("/browser/v1/status").status_code != 401:
            return False
        resource = get("/.well-known/oauth-protected-resource/browser/v1/mcp")
        if (
            resource.status_code != 200
            or resource.json().get("resource") != public_url + "/browser/v1/mcp"
            or resource.json().get("authorization_servers") != [public_url + "/browser/oauth"]
        ):
            return False
        metadata = get("/.well-known/oauth-authorization-server/browser/oauth")
        expected = {
            "issuer": public_url + "/browser/oauth",
            "authorization_endpoint": public_url + "/browser/oauth/authorize",
            "token_endpoint": public_url + "/browser/oauth/token",
            "registration_endpoint": public_url + "/browser/oauth/register",
        }
        return metadata.status_code == 200 and all(
            metadata.json().get(key) == value for key, value in expected.items()
        )
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        return False


def validate_target(project, container_id, container_name, public_url, expected_sha):
    from scripts.cloudru_chrome import owned_record

    if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        fail("chrome_gateway_validation_failed")
    apps = CloudRuContainerAppsClient(project_id=project)
    record = owned_record(apps, identifier=container_id)
    if (
        record is None
        or record.get("name") != container_name
        or str(record.get("status", "")).lower() != "running"
    ):
        fail("chrome_gateway_validation_failed")
    # owned_record checks project/name/id, pinned image, provider ingress auth,
    # private state mount and singleton scale independently of the Gateway.
    environment = {
        item["name"]: item["value"] for item in record["template"]["containers"][0]["env"]
    }
    if (
        environment.get("BROWSER_PUBLIC_URL") != public_url
        or environment.get("BROWSER_DEPLOYMENT_SHA") != expected_sha
    ):
        fail("chrome_gateway_validation_failed")


def deploy(
    client,
    project,
    container_id,
    public_url,
    container_name,
    plan=None,
    *,
    timeout=300,
    sleep=time.sleep,
    http_get=requests.get,
    expected_sha=None,
):
    identifier(container_id)
    settings(project, public_url, container_name)
    expected_sha = expected_sha or os.environ.get("BROWSER_DEPLOYMENT_SHA")
    validate_target(project, container_id, container_name, public_url, expected_sha)
    # A plan is informational only. Inventory again to detect changes since the
    # worker build; stale plans never authorize overwriting other resources.
    plan = preflight(client, project, public_url, container_name)
    if plan["blockers"]:
        fail("chrome_gateway_preflight_failed")
    specification = gateway_spec(container_id, public_url)
    gateway = plan["gateway"]
    deadline = time.monotonic() + timeout
    if gateway is None:
        client.request(
            "chrome_gateway",
            "POST",
            "/api-gws",
            params={"projectId": project},
            json_body={
                "projectId": project,
                "name": container_name,
                "description": owner(project, container_name),
                "domains": [],
                "openapiSpec": json.dumps(specification),
                "routes": [],
            },
        )
        # Creation may return an operation instead of the Gateway object.
        # Resolve its actual ID through the verified list API, never synthesize it.
        while gateway is None:
            candidates = [
                entry
                for entry in items(
                    client,
                    "chrome_gateway",
                    "/api-gws",
                    "apiGws",
                    {"projectId": project},
                    gateway=True,
                )
                if entry.get("name") == container_name
            ]
            if candidates:
                if len(candidates) != 1 or candidates[0].get("description") != owner(
                    project, container_name
                ):
                    fail("chrome_gateway_conflict")
                gateway = candidates[0]
            elif time.monotonic() >= deadline:
                fail("chrome_gateway_timeout")
            else:
                sleep(5)
    else:
        current = client.request(
            "chrome_gateway",
            "GET",
            f"/api-gws/{gateway['id']}/openapi",
            params={"projectId": project},
        ).get("openapiSpec")
        try:
            equal = json.loads(current) == specification
        except (ValueError, TypeError):
            equal = False
        if not equal:
            client.request(
                "chrome_gateway",
                "PATCH",
                f"/api-gws/{gateway['id']}",
                params={"projectId": project},
                json_body={"openapiSpec": json.dumps(specification)},
            )
    gateway_id = identifier(gateway.get("id"))
    while True:
        gateway = client.request(
            "chrome_gateway", "GET", f"/api-gws/{gateway_id}", params={"projectId": project}
        )
        if gateway.get("description") != owner(project, container_name):
            fail("chrome_gateway_conflict")
        if gateway.get("status") == "API_GW_STATUS_PUBLISHED":
            break
        if time.monotonic() >= deadline:
            fail("chrome_gateway_timeout")
        sleep(5)
    target = system_host(gateway)
    # Recheck DNS immediately before adding the single record.
    records = items(
        client,
        "chrome_dns",
        "/v1/public/records",
        "records",
        {"zoneId": plan["zone"]["meta"]["id"]},
    )
    relevant = [
        record for record in records if record_host(record) == urlsplit(public_url).hostname
    ]
    if relevant:
        if (
            len(relevant) != 1
            or relevant[0].get("type") != "PUBLIC_RECORD_TYPE_CNAME"
            or relevant[0].get("values") != [target + "."]
        ):
            fail("chrome_gateway_conflict")
    else:
        client.request(
            "chrome_dns",
            "POST",
            "/v1/public/records",
            json_body={
                "zoneId": plan["zone"]["meta"]["id"],
                "name": container_name,
                "type": "PUBLIC_RECORD_MANAGED_TYPE_CNAME",
                "ttl": 300,
                "values": [target + "."],
                "wildcard": False,
            },
        )
    if plan["domain"] is None:
        client.request(
            "chrome_gateway",
            "POST",
            f"/api-gws/{gateway_id}/domains",
            params={"projectId": project},
            json_body={"host": urlsplit(public_url).hostname, "certId": plan["certificate"]["id"]},
        )
    while True:
        if verify_public(public_url, expected_sha, http_get=http_get):
            break
        if time.monotonic() >= deadline:
            fail("chrome_gateway_verification_failed")
        sleep(5)
    final = preflight(client, project, public_url, container_name)
    if (
        final["blockers"]
        or not final["cname"]
        or not final["domain"]
        or final["domain"].get("status") != "DOMAIN_STATUS_ACTIVATED"
    ):
        fail("chrome_gateway_verification_failed")
    return {
        "status": "GATEWAY_DEPLOYED",
        "gateway_id": gateway_id,
        "gateway_url": public_url,
        "mcp_url": public_url + "/browser/v1/mcp",
        "gateway_deployed": True,
        "certificate_id": plan["certificate"]["id"],
        "dns_record_id": final["cname"]["meta"]["id"],
        "github_callback_url": public_url + "/browser/oauth/github/callback",
        "github_callback_verified": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "deploy"))
    parser.add_argument("--container-name", required=True)
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--container-id")
    parser.add_argument("--sha")
    args = parser.parse_args(argv)
    if args.action == "deploy":
        from scripts.cloudru_chrome import require_reviewed_head

        if not args.sha or not args.container_id:
            fail("chrome_gateway_validation_failed")
        require_reviewed_head(Path(__file__).resolve().parents[1], args.sha)
    _load_cloudru_credentials()
    project = os.environ.get("CLOUDRU_PROJECT_ID", "")
    client = ChromeGatewayClient()
    plan = preflight(client, project, args.public_url, args.container_name)
    print(json.dumps(plan["safe_summary"], sort_keys=True), flush=True)
    if args.action == "deploy":
        if plan["blockers"]:
            fail("chrome_gateway_preflight_failed")
        print(
            json.dumps(
                deploy(
                    client,
                    project,
                    args.container_id,
                    args.public_url,
                    args.container_name,
                    expected_sha=args.sha,
                ),
                sort_keys=True,
            ),
            flush=True,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        code = getattr(error, "code", "")
        print(
            json.dumps(
                {
                    "error": code if code in SAFE_ERRORS else "chrome_gateway_operation_failed",
                    "http_status": getattr(error, "http_status", None),
                }
            ),
            flush=True,
        )
        sys.exit(1)
