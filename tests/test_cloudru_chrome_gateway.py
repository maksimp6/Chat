"""Gateway reconciliation must never overwrite unrelated DNS/TLS resources."""

from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from cloud.base import CloudProviderError
from scripts import cloudru_chrome_gateway as gateway
from scripts.chrome_gateway_spec import gateway_spec

PROJECT = "22706bfa-6066-4047-90d9-9ad15f242b1f"
CONTAINER = "10000000-0000-4000-8000-000000000001"
GATEWAY = "20000000-0000-4000-8000-000000000001"
CERTIFICATE = "30000000-0000-4000-8000-000000000001"
ZONE = "40000000-0000-4000-8000-000000000001"
RECORD = "50000000-0000-4000-8000-000000000001"
DOMAIN = "60000000-0000-4000-8000-000000000001"
NAME = "chrome-22706bfa6066"
PUBLIC = f"https://{NAME}.maxxxpavlov.online"
SYSTEM = "20000000000040008000000000000001.apigw.cloud.ru"
SHA = "f" * 40
OAUTH = {
    "ALICE_GITHUB_CLIENT_ID": "test-client-id",
    "ALICE_GITHUB_CLIENT_SECRET": "test-secret-never-log",
    "ALICE_GITHUB_ALLOWED_IDS": "12345",
}


def cert(**overrides):
    return {
        "id": CERTIFICATE,
        "type": "CERTIFICATE_TYPE_IMPORTED",
        "primary": {
            "enabled": True,
            "expiresAt": "2099-01-01T00:00:00Z",
            "dnsNames": ["*.maxxxpavlov.online"],
        },
        **overrides,
    }


class Client:
    def __init__(self):
        self.certificates = [cert()]
        self.zones = [
            {
                "meta": {"id": ZONE},
                "domain": "maxxxpavlov.online.",
                "role": "ROLE_PUBLIC",
                "state": "STATE_OK",
                "activeState": True,
            }
        ]
        self.records = []
        self.gateways = []
        self.spec = None
        self.calls = []

    def existing(self):
        self.gateways = [
            {
                "id": GATEWAY,
                "name": NAME,
                "description": gateway.owner(PROJECT, NAME),
                "status": "API_GW_STATUS_PUBLISHED",
                "domains": [
                    {"host": SYSTEM, "isSystem": True},
                    {
                        "id": DOMAIN,
                        "host": PUBLIC.removeprefix("https://"),
                        "isSystem": False,
                        "certId": CERTIFICATE,
                        "status": "DOMAIN_STATUS_ACTIVATED",
                    },
                ],
            }
        ]
        self.records = [
            {
                "meta": {"id": RECORD},
                "name": NAME,
                "type": "PUBLIC_RECORD_TYPE_CNAME",
                "values": [SYSTEM + "."],
                "ttl": 300,
                "state": "STATE_OK",
            }
        ]
        self.spec = json.dumps(gateway_spec(CONTAINER, PUBLIC))
        return self

    def request(self, service, method, route, *, params=None, json_body=None):
        self.calls.append((service, method, route, deepcopy(params), deepcopy(json_body)))
        if service == "chrome_certificates" and method == "GET" and route == "/certificate":
            return {"certificates": deepcopy(self.certificates)}
        if service == "chrome_dns" and method == "GET":
            values = self.zones if route == "/v1/zones" else self.records
            offset = params["pagination.offset"]
            limit = params["pagination.limit"]
            return {
                "zones" if route == "/v1/zones" else "records": deepcopy(
                    values[offset : offset + limit]
                ),
                "pagination": {"total": str(len(values))},
            }
        if service == "chrome_dns" and method == "POST":
            self.records.append(
                {
                    **deepcopy(json_body),
                    "meta": {"id": RECORD},
                    "type": "PUBLIC_RECORD_TYPE_CNAME",
                    "state": "STATE_OK",
                }
            )
            return {"task": {"id": "dns-task"}}
        if service == "chrome_gateway" and route == "/api-gws" and method == "GET":
            offset = params["skip"]
            return {
                "apiGws": deepcopy(self.gateways[offset : offset + params["pageSize"]]),
                "totalSize": len(self.gateways),
            }
        if method == "GET" and route == f"/api-gws/{GATEWAY}/openapi":
            return {"openapiSpec": self.spec}
        if method == "GET" and route.startswith("/api-gws/"):
            return deepcopy(
                next(item for item in self.gateways if item["id"] == route.split("/")[2])
            )
        if method == "POST" and route == "/api-gws":
            self.spec = json_body["openapiSpec"]
            value = {
                **deepcopy(json_body),
                "id": GATEWAY,
                "status": "API_GW_STATUS_PUBLISHED",
                "domains": [{"isSystem": True, "host": SYSTEM}],
            }
            self.gateways.append(value)
            # Management APIs may return an asynchronous operation identifier.
            return {"task": {"id": "gateway-task"}}
        if method == "PATCH" and route == f"/api-gws/{GATEWAY}":
            self.spec = json_body["openapiSpec"]
            return {}
        if method == "POST" and route == f"/api-gws/{GATEWAY}/domains":
            domain = {
                **deepcopy(json_body),
                "id": DOMAIN,
                "isSystem": False,
                "status": "DOMAIN_STATUS_ACTIVATED",
            }
            self.gateways[0]["domains"].append(domain)
            return deepcopy(domain)
        raise AssertionError((service, method, route))


class Response:
    def __init__(self, status, value=None, headers=None):
        self.status_code = status
        self.value = value
        self.headers = headers or {}

    def json(self):
        return self.value


def public_get(url, **kwargs):
    assert kwargs == {"timeout": 20, "allow_redirects": False}
    if url == PUBLIC + "/healthz":
        return Response(
            200, {"status": "ok", "state_ready": True, "oauth_ready": True, "deployment_sha": SHA}
        )
    if url == PUBLIC + "/browser/v1/mcp":
        return Response(
            401,
            headers={
                "WWW-Authenticate": f'Bearer resource_metadata="{PUBLIC}/.well-known/oauth-protected-resource/browser/v1/mcp"'
            },
        )
    if url == PUBLIC + "/browser/v1/status":
        return Response(401)
    if "/.well-known/oauth-protected-resource" in url:
        return Response(
            200,
            {
                "resource": PUBLIC + "/browser/v1/mcp",
                "authorization_servers": [PUBLIC + "/browser/oauth"],
            },
        )
    if "/.well-known/oauth-authorization-server" in url:
        return Response(
            200,
            {
                "issuer": PUBLIC + "/browser/oauth",
                "authorization_endpoint": PUBLIC + "/browser/oauth/authorize",
                "token_endpoint": PUBLIC + "/browser/oauth/token",
                "registration_endpoint": PUBLIC + "/browser/oauth/register",
            },
        )
    raise AssertionError(url)


@pytest.fixture(autouse=True)
def oauth_env(monkeypatch):
    for key, value in OAUTH.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("BROWSER_DEPLOYMENT_SHA", SHA)


@pytest.fixture(autouse=True)
def owned_target(monkeypatch):
    # Provider ownership validation has independent coverage below. Other tests
    # focus on Gateway/DNS reconciliation after the worker was deployed.
    original = gateway.validate_target
    monkeypatch.setattr(gateway, "validate_target", lambda *args: None)
    return original


def test_preflight_reports_missing_certificate_and_callback_without_mutations():
    client = Client()
    client.certificates = []
    result = gateway.preflight(client, PROJECT, PUBLIC, NAME, env={})
    assert len(result["blockers"]) == 2
    assert (
        result["safe_summary"]["github_callback_url"] == PUBLIC + "/browser/oauth/github/callback"
    )
    assert result["safe_summary"]["github_callback_verified"] is False
    assert all(call[1] == "GET" for call in client.calls)
    assert OAUTH["ALICE_GITHUB_CLIENT_SECRET"] not in json.dumps(result["safe_summary"])


def test_preflight_uses_existing_evolution_dns_zone_and_enabled_wildcard():
    client = Client()
    result = gateway.preflight(client, PROJECT, PUBLIC, NAME)
    assert result["blockers"] == []
    assert result["zone"]["meta"]["id"] == ZONE
    assert result["certificate"]["id"] == CERTIFICATE
    assert any(
        call[3].get("projectId") == PROJECT for call in client.calls if call[2] == "/v1/zones"
    )


@pytest.mark.parametrize(
    "version",
    [
        {
            "enabled": False,
            "expiresAt": "2099-01-01T00:00:00Z",
            "dnsNames": ["*.maxxxpavlov.online"],
        },
        {
            "enabled": True,
            "expiresAt": "2000-01-01T00:00:00Z",
            "dnsNames": ["*.maxxxpavlov.online"],
        },
        {"enabled": True, "expiresAt": "2099-01-01T00:00:00Z", "dnsNames": ["*.other.online"]},
    ],
)
def test_unsuitable_certificates_do_not_pass(version):
    assert gateway.certificate_for([cert(primary=version)], PUBLIC.removeprefix("https://")) is None


def test_lets_encrypt_uses_current_issued_version_not_primary():
    version = cert()["primary"]
    value = cert(
        type="CERTIFICATE_TYPE_LETS_ENCRYPT", primary=None, issueState={"current": version}
    )
    assert (
        gateway.certificate_for(
            [value], PUBLIC.removeprefix("https://"), datetime(2026, 10, 3, tzinfo=timezone.utc)
        )["id"]
        == CERTIFICATE
    )
    assert gateway.matches("*.maxxxpavlov.online", "nested.chrome.maxxxpavlov.online") is False


@pytest.mark.parametrize(
    "record_type,values",
    [
        ("PUBLIC_RECORD_TYPE_A", ["192.0.2.1"]),
        ("PUBLIC_RECORD_TYPE_CNAME", ["other.apigw.cloud.ru."]),
    ],
)
def test_conflicting_dns_is_a_preflight_blocker(record_type, values):
    client = Client().existing()
    client.records[0].update(type=record_type, values=values)
    assert any(
        "conflict" in issue
        for issue in gateway.preflight(client, PROJECT, PUBLIC, NAME)["blockers"]
    )
    with pytest.raises(CloudProviderError, match="preflight_failed"):
        gateway.deploy(client, PROJECT, CONTAINER, PUBLIC, NAME, http_get=public_get)
    assert all(call[1] == "GET" for call in client.calls)


def test_existing_gateway_must_have_exact_ownership_marker():
    client = Client().existing()
    client.gateways[0]["description"] = "An unrelated service"
    assert any(
        "not owned" in issue
        for issue in gateway.preflight(client, PROJECT, PUBLIC, NAME)["blockers"]
    )


def test_apex_or_different_container_hostname_is_rejected_before_api_calls():
    client = Client()
    with pytest.raises(CloudProviderError, match="validation_failed"):
        gateway.preflight(client, PROJECT, "https://maxxxpavlov.online", NAME)
    assert client.calls == []


def test_deploy_creates_only_owned_gateway_one_cname_and_domain_then_checks_https():
    client = Client()
    client.records.append(
        {
            "meta": {"id": "50000000-0000-4000-8000-000000000002"},
            "name": "mail",
            "type": "PUBLIC_RECORD_TYPE_A",
            "values": ["192.0.2.10"],
        }
    )
    result = gateway.deploy(
        client, PROJECT, CONTAINER, PUBLIC, NAME, http_get=public_get, expected_sha=SHA
    )
    assert result["gateway_deployed"] is True
    writes = [call for call in client.calls if call[1] != "GET"]
    assert [(call[0], call[1], call[2]) for call in writes] == [
        ("chrome_gateway", "POST", "/api-gws"),
        ("chrome_dns", "POST", "/v1/public/records"),
        ("chrome_gateway", "POST", f"/api-gws/{GATEWAY}/domains"),
    ]
    assert writes[1][4]["values"] == [SYSTEM + "."]
    assert writes[1][4]["name"] == NAME
    assert client.records[0]["values"] == ["192.0.2.10"]
    assert json.loads(client.spec) == gateway_spec(CONTAINER, PUBLIC)


def test_repeat_deploy_is_idempotent_with_matching_spec_domain_and_dns():
    client = Client().existing()
    result = gateway.deploy(client, PROJECT, CONTAINER, PUBLIC, NAME, http_get=public_get)
    assert result["gateway_id"] == GATEWAY
    assert all(call[1] == "GET" for call in client.calls)


def test_stale_preflight_is_not_trusted_after_dns_changes():
    client = Client().existing()
    plan = gateway.preflight(client, PROJECT, PUBLIC, NAME)
    client.records[0]["values"] = ["unrelated.example."]
    with pytest.raises(CloudProviderError, match="preflight_failed"):
        gateway.deploy(client, PROJECT, CONTAINER, PUBLIC, NAME, plan=plan, http_get=public_get)
    assert all(call[1] == "GET" for call in client.calls)


def test_changed_worker_only_updates_owned_gateway_specification():
    client = Client().existing()
    client.spec = json.dumps(gateway_spec("10000000-0000-4000-8000-000000000002", PUBLIC))
    gateway.deploy(client, PROJECT, CONTAINER, PUBLIC, NAME, http_get=public_get)
    assert [(call[1], call[2]) for call in client.calls if call[1] != "GET"] == [
        ("PATCH", f"/api-gws/{GATEWAY}")
    ]


def test_public_verification_rejects_old_sha_and_unprotected_worker():
    assert gateway.verify_public(PUBLIC, SHA, http_get=public_get)
    assert not gateway.verify_public(PUBLIC, "a" * 40, http_get=public_get)

    def insecure(url, **kwargs):
        return Response(200, {}) if url.endswith("/browser/v1/mcp") else public_get(url, **kwargs)

    assert not gateway.verify_public(PUBLIC, SHA, http_get=insecure)


def test_failed_https_verification_never_reports_deployed():
    client = Client().existing()
    with pytest.raises(CloudProviderError, match="verification_failed"):
        gateway.deploy(
            client,
            PROJECT,
            CONTAINER,
            PUBLIC,
            NAME,
            timeout=0,
            http_get=lambda *a, **k: Response(502),
        )


def test_inventory_paginates_and_rejects_repeated_records():
    client = Client()
    client.zones = [
        {"meta": {"id": f"40000000-0000-4000-8000-{number:012d}"}} for number in range(1, 106)
    ]
    assert (
        len(gateway.items(client, "chrome_dns", "/v1/zones", "zones", {"projectId": PROJECT}))
        == 105
    )
    assert [call[3]["pagination.offset"] for call in client.calls] == [0, 100]
    client.zones[100] = client.zones[0]
    with pytest.raises(CloudProviderError, match="invalid_response"):
        gateway.items(client, "chrome_dns", "/v1/zones", "zones", {"projectId": PROJECT})


def test_endpoint_overrides_cannot_redirect_iam_token(monkeypatch):
    monkeypatch.setenv("CLOUDRU_CHROME_GATEWAY_ENDPOINT", "https://attacker.invalid")
    client = gateway.ChromeGatewayClient(iam_client=object())
    assert client.endpoint("chrome_gateway") == "https://console.cloud.ru/u-api/apigw/v2"


def test_target_validation_checks_owned_container_origin_and_reviewed_sha(
    monkeypatch, owned_target
):
    from scripts import cloudru_chrome

    record = {
        "name": NAME,
        "status": "running",
        "template": {
            "containers": [
                {
                    "env": [
                        {"name": "BROWSER_PUBLIC_URL", "value": PUBLIC},
                        {"name": "BROWSER_DEPLOYMENT_SHA", "value": SHA},
                    ]
                }
            ]
        },
    }
    monkeypatch.setattr(gateway, "CloudRuContainerAppsClient", lambda **kwargs: object())

    def owned(apps, *, identifier):
        assert identifier == CONTAINER
        return record

    monkeypatch.setattr(cloudru_chrome, "owned_record", owned)
    owned_target(PROJECT, CONTAINER, NAME, PUBLIC, SHA)
    with pytest.raises(CloudProviderError, match="validation_failed"):
        owned_target(PROJECT, CONTAINER, NAME, PUBLIC, "0" * 40)
    with pytest.raises(CloudProviderError, match="validation_failed"):
        owned_target(PROJECT, CONTAINER, NAME, PUBLIC, None)
    record["name"] = "unrelated"
    with pytest.raises(CloudProviderError, match="validation_failed"):
        owned_target(PROJECT, CONTAINER, NAME, PUBLIC, SHA)


def test_existing_domain_keeps_its_valid_certificate_without_rotation():
    client = Client().existing()
    client.certificates.append(cert(id="30000000-0000-4000-8000-000000000002"))
    result = gateway.preflight(client, PROJECT, PUBLIC, NAME)
    assert result["certificate"]["id"] == CERTIFICATE
    assert result["blockers"] == []


def test_missing_certificate_enabled_state_fails_closed():
    value = cert()
    del value["primary"]["enabled"]
    assert gateway.certificate_for([value], PUBLIC.removeprefix("https://")) is None


def test_preflight_rejects_ambiguous_oauth_owner():
    result = gateway.preflight(
        Client(), PROJECT, PUBLIC, NAME, env={**OAUTH, "ALICE_GITHUB_ALLOWED_IDS": "123,456"}
    )
    assert any("one immutable" in blocker for blocker in result["blockers"])


def test_cli_deploy_requires_reviewed_sha_before_credentials_or_cloud(monkeypatch):
    monkeypatch.setattr(
        gateway, "_load_cloudru_credentials", lambda: pytest.fail("must not load credentials")
    )
    with pytest.raises(CloudProviderError, match="validation_failed"):
        gateway.main(
            [
                "deploy",
                "--container-name",
                NAME,
                "--public-url",
                PUBLIC,
                "--container-id",
                CONTAINER,
            ]
        )
