"""Gateway routing boundaries; provider header forwarding still needs a live smoke."""

import json

import pytest

from scripts.chrome_gateway_spec import gateway_spec


CONTAINER = "1440c7da-b147-4ed1-87fb-f0bdf2ec5b77"
ORIGIN = "https://chrome-22706bfa6066.maxxxpavlov.online"


def test_routes_cover_mcp_oauth_and_lifecycle_without_a_catchall():
    expected = {
        "/healthz": {"get"},
        "/browser/v1/mcp": {"get", "post", "delete"},
        "/browser/v1/status": {"get"},
        "/browser/v1/wake": {"post"},
        "/browser/v1/sleep": {"post"},
        "/browser/v1/navigate": {"post"},
        "/browser/v1/click": {"post"},
        "/browser/v1/type": {"post"},
        "/browser/v1/extract": {"post"},
        "/browser/v1/screenshot": {"post"},
        "/.well-known/oauth-protected-resource": {"get"},
        "/.well-known/oauth-protected-resource/browser/v1/mcp": {"get"},
        "/.well-known/oauth-authorization-server": {"get"},
        "/.well-known/oauth-authorization-server/browser/oauth": {"get"},
        "/browser/oauth/register": {"post"},
        "/browser/oauth/authorize": {"get", "post"},
        "/browser/oauth/github/callback": {"get"},
        "/browser/oauth/token": {"post"},
        "/browser/oauth/revoke": {"post"},
    }
    spec = gateway_spec(CONTAINER, ORIGIN)
    assert {path: set(methods) for path, methods in spec["paths"].items()} == expected
    identifiers = [
        operation["operationId"] for route in spec["paths"].values() for operation in route.values()
    ]
    assert len(identifiers) == len(set(identifiers))


def test_every_route_uses_the_native_container_backend_and_limits():
    spec = gateway_spec(CONTAINER, ORIGIN + "/")
    assert spec["servers"] == [{"url": ORIGIN}]
    for route in spec["paths"].values():
        for operation in route.values():
            reference = operation["x-cloud-backend"]["$ref"]
            backend = spec
            for component in reference.removeprefix("#/").split("/"):
                backend = backend[component]
            assert backend["type"] == "serverless_container"
            assert backend["nodes"] == [{"container_id": CONTAINER, "weight": 1}]
            assert all(value > 0 for value in backend["timeout"].values())
            limit = operation["x-cloud-limit-count"]
            assert limit["count"] > 0 and limit["time_window"] > 0
            assert limit["rejected_code"] == 429
    assert "containerapps.ru" not in json.dumps(spec)


def test_spec_does_not_intercept_application_oauth_or_cache_log_or_rewrite_secrets():
    # These policies could consume/replace Authorization, record callback codes,
    # cache cookies, or change query strings. Their absence is a config guarantee,
    # not proof of the provider's default header/query forwarding behavior.
    forbidden = {
        "security",
        "securitySchemes",
        "x-cloud-authtype",
        "x-cloud-proxy-rewrite",
        "x-cloud-proxy-cache",
        "x-cloud-log-group",
        "x-cloud-mock",
    }

    def visit(value):
        if isinstance(value, dict):
            assert forbidden.isdisjoint(value)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(gateway_spec(CONTAINER, ORIGIN))


@pytest.mark.parametrize(
    "origin",
    [
        "http://chrome.example.test",
        "https:///missing-host",
        "https://user:secret@chrome.example.test",
        "https://chrome.example.test/browser/v1/mcp",
        "https://chrome.example.test?code=private",
        "https://chrome.example.test#fragment",
        "https://chrome.example.test:invalid",
        "https://chrome.example.test:65536",
    ],
)
def test_invalid_public_origin_is_rejected_before_emitting_configuration(origin):
    with pytest.raises(ValueError):
        gateway_spec(CONTAINER, origin)


@pytest.mark.parametrize("container_id", ["chrome-service-name", "", "../other-container"])
def test_native_backend_requires_a_container_uuid(container_id):
    with pytest.raises(ValueError):
        gateway_spec(container_id, ORIGIN)
