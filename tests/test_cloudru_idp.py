"""Alice IdP deployment: lane separation, secrets per lane, ownership and ingress gates."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cloud.base import CloudProviderError
from scripts import cloudru_idp as idp

PROJECT = "22706bfa-6066-4047-90d9-9ad15f242b1f"
IDENTIFIER = "1440c7da-b147-4ed1-87fb-f0bdf2ec5b77"
SHA = "a" * 40
IMAGE = "alice-chrome-browser.cr.cloud.ru/oauth-idp@sha256:" + "b" * 64
ORIGIN = "https://idp-test.containerapps.ru"
MCP = "https://chrome-test.containerapps.ru/browser/v1/mcp"
SECRET = "s" * 40
PASSPHRASE = "correct horse battery staple"

TEST_ENV = {"IDP_SECRET": SECRET, "IDP_OWNER_PASSPHRASE": PASSPHRASE}
PRODUCTION_ENV = {
    "IDP_SECRET": SECRET,
    "IDP_GITHUB_CLIENT_ID": "gh-client",
    "IDP_GITHUB_CLIENT_SECRET": "gh-secret",
}


def environment(lane="test", env=None, **kwargs):
    base = TEST_ENV if lane == "test" else PRODUCTION_ENV
    return idp.runtime_env(
        SHA,
        lane,
        env or base,
        public_url=kwargs.pop("public_url", ORIGIN),
        resources=[MCP],
        **kwargs,
    )


def record(lane="test", image=IMAGE, env=None, **overrides):
    value = idp.creation_body(PROJECT, lane, image, env or environment(lane))
    value.update({"id": IDENTIFIER, "status": "running", **overrides})
    value["configuration"]["ingress"]["publicUri"] = ORIGIN
    return value


def test_lanes_use_separate_names_and_unknown_lanes_are_rejected():
    assert idp.container_name(PROJECT, "production") == "idp-22706bfa6066"
    assert idp.container_name(PROJECT, "test") == "idp-test-22706bfa6066"
    assert idp.lane({}) == "production"
    assert idp.lane({"IDP_LANE": "test"}) == "test"
    with pytest.raises(CloudProviderError):
        idp.lane({"IDP_LANE": "staging"})


def test_test_lane_signs_in_with_a_passphrase_and_never_gets_github_credentials():
    values = environment("test")
    assert values["IDP_OWNER_PASSPHRASE"] == PASSPHRASE
    assert not {k for k in values if "GITHUB_CLIENT" in k}
    # The deploy step must drop GitHub credentials for the test lane rather than fail on them.
    stripped = idp.runtime_env(
        SHA, "test", {**TEST_ENV, **PRODUCTION_ENV}, public_url=ORIGIN, resources=[MCP]
    )
    assert "IDP_GITHUB_CLIENT_ID" not in stripped


def test_production_requires_github_and_can_never_carry_the_passphrase():
    values = environment("production")
    assert values["IDP_GITHUB_CLIENT_ID"] == "gh-client"
    assert values["IDP_GITHUB_CLIENT_SECRET"] == "gh-secret"
    mixed = idp.runtime_env(
        SHA, "production", {**PRODUCTION_ENV, **TEST_ENV}, public_url=ORIGIN, resources=[MCP]
    )
    assert "IDP_OWNER_PASSPHRASE" not in mixed
    for missing in ("IDP_GITHUB_CLIENT_ID", "IDP_GITHUB_CLIENT_SECRET"):
        with pytest.raises(CloudProviderError):
            environment("production", {k: v for k, v in PRODUCTION_ENV.items() if k != missing})


@pytest.mark.parametrize(
    "bad",
    [
        {"IDP_SECRET": "short", "IDP_OWNER_PASSPHRASE": PASSPHRASE},
        {"IDP_SECRET": SECRET, "IDP_OWNER_PASSPHRASE": "short"},
        {"IDP_SECRET": SECRET},
        {"IDP_OWNER_PASSPHRASE": PASSPHRASE},
    ],
)
def test_weak_or_missing_test_lane_secrets_stop_the_deploy(bad):
    with pytest.raises(CloudProviderError):
        environment("test", bad)


def test_runtime_forwards_only_known_settings_and_a_pinned_owner_and_scopes():
    values = idp.runtime_env(
        SHA,
        "test",
        {
            **TEST_ENV,
            "CLOUDRU_IAM_KEY_SECRET": "never-forward",
            "ALICE_DATABASE_URL": "never-forward",
        },
        public_url=ORIGIN,
        resources=[MCP],
    )
    assert "never-forward" not in json.dumps(values)
    assert values["IDP_ALLOWED_GITHUB_IDS"] == "293531601"
    assert values["IDP_SCOPES"] == "browser.read,browser.control"
    assert values["IDP_ALLOWED_RESOURCES"] == MCP
    assert values["IDP_PUBLIC_URL"] == ORIGIN
    assert values["IDP_DEPLOYMENT_SHA"] == SHA
    assert set(values) <= idp.APP_ENV


@pytest.mark.parametrize(
    "kwargs",
    [
        {"public_url": "http://idp-test.containerapps.ru"},
        {"public_url": "https://evil.example"},
        {"public_url": "https://user:x@idp-test.containerapps.ru"},
        {"resources": ["http://chrome.containerapps.ru/browser/v1/mcp"]},
        {"resources": ["https://evil.example/browser/v1/mcp"]},
        {"resources": ["https://chrome.containerapps.ru/other"]},
        {"resources": []},
    ],
)
def test_public_url_and_resources_must_be_provider_or_owned_https_origins(kwargs):
    arguments = {"public_url": ORIGIN, "resources": [MCP], **kwargs}
    with pytest.raises(CloudProviderError):
        idp.runtime_env(SHA, "test", TEST_ENV, **arguments)


def test_custom_domain_is_accepted_only_when_explicit_and_https():
    values = idp.runtime_env(
        SHA,
        "production",
        PRODUCTION_ENV,
        public_url="https://oauth.maxxxpavlov.online",
        resources=[MCP],
    )
    assert values["IDP_PUBLIC_URL"] == "https://oauth.maxxxpavlov.online"
    for bad in (
        "http://oauth.maxxxpavlov.online",
        "https://oauth.maxxxpavlov.online/x",
        "https://oauth.maxxxpavlov.online:8443",
    ):
        with pytest.raises(CloudProviderError):
            idp.runtime_env(SHA, "production", PRODUCTION_ENV, public_url=bad, resources=[MCP])


def test_singleton_stateless_container_with_no_volume_and_minimal_size():
    body = record()
    assert body["name"] == "idp-test-22706bfa6066"
    assert body["template"]["scaling"] == {"minInstanceCount": 0, "maxInstanceCount": 1}
    assert body["template"]["containers"][0]["resources"] == {"cpu": "0.1", "memory": "256Mi"}
    assert "volumes" not in body["template"]
    assert body["configuration"]["ingress"]["accessSettings"]["enableAuth"] is False
    assert "chrome" not in body["name"]


def test_image_must_be_a_pinned_digest_in_our_registry():
    for bad in (
        "alice-chrome-browser.cr.cloud.ru/oauth-idp:latest",
        "evil.example/oauth-idp@sha256:" + "b" * 64,
        "",
    ):
        with pytest.raises(CloudProviderError):
            idp.creation_body(PROJECT, "test", bad, environment())


@pytest.mark.parametrize(
    "modify",
    [
        lambda p: p.update(description="unrelated"),
        lambda p: p.update(projectId=IDENTIFIER),
        lambda p: p["template"]["scaling"].update(maxInstanceCount=2),
        lambda p: p["template"].update(volumes=[{"name": "x"}]),
        lambda p: p["template"]["containers"][0]["env"].append(
            {"name": "CLOUDRU_IAM_KEY_SECRET", "value": "p"}
        ),
        lambda p: p["template"]["containers"][0].update(resources={"cpu": "1", "memory": "4096Mi"}),
    ],
)
def test_foreign_or_unsafe_existing_container_is_never_mutated(modify):
    value = record()
    modify(value)
    apps = SimpleNamespace(project_id=PROJECT, list=Mock(return_value=[value]), stop=Mock())
    with pytest.raises(CloudProviderError, match="could not be verified"):
        idp.owned_record(apps, "test")
    apps.stop.assert_not_called()


def test_test_lane_cannot_adopt_the_production_container_or_the_reverse():
    production = record("production")
    apps = SimpleNamespace(project_id=PROJECT, list=Mock(return_value=[production]))
    assert idp.owned_record(apps, "test") is None
    assert idp.owned_record(apps, "production")["name"] == "idp-22706bfa6066"


def test_chrome_resource_is_the_chrome_container_of_the_same_lane():
    chrome_record = {
        "name": "chrome-test-22706bfa6066",
        "configuration": {"ingress": {"publicUri": "https://chrome-test.containerapps.ru"}},
    }
    other = {
        "name": "chrome-22706bfa6066",
        "configuration": {"ingress": {"publicUri": "https://chrome-prod.containerapps.ru"}},
    }
    apps = SimpleNamespace(project_id=PROJECT, list=Mock(return_value=[other, chrome_record]))
    assert idp.chrome_resource(apps, "test") == MCP
    assert (
        idp.chrome_resource(apps, "production")
        == "https://chrome-prod.containerapps.ru/browser/v1/mcp"
    )
    with pytest.raises(CloudProviderError):
        idp.chrome_resource(SimpleNamespace(project_id=PROJECT, list=Mock(return_value=[])), "test")


def reply(value, status=200):
    return SimpleNamespace(status_code=status, json=lambda: value)


def ingress(lane="test", **overrides):
    values = environment(lane)
    jwk = {"kty": "OKP", "crv": "Ed25519", "x": "x", "kid": "k"}
    health = {
        "status": "ok",
        "deployment_sha": SHA,
        **({"sign_in": "passphrase"} if lane == "test" else {}),
    }
    answers = {
        "/healthz": reply(health),
        "/.well-known/oauth-authorization-server": reply(
            {"issuer": values["IDP_PUBLIC_URL"], "code_challenge_methods_supported": ["S256"]}
        ),
        "/jwks.json": reply({"keys": [jwk]}),
    }
    answers.update(overrides)
    return Mock(side_effect=lambda url, **_: answers[url.removeprefix(ORIGIN)])


def test_ingress_passes_only_for_the_exact_revision_issuer_and_one_signing_key():
    idp.verify_ingress(record(), "test", http_get=ingress())
    idp.verify_ingress(
        record("production", env=environment("production")),
        "production",
        http_get=ingress("production"),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"/healthz": reply({"status": "ok", "deployment_sha": "d" * 40, "sign_in": "passphrase"})},
        {"/healthz": reply({"status": "ok", "deployment_sha": SHA})},
        {"/healthz": reply({"status": "misconfigured", "missing": ["IDP_SECRET"]}, 503)},
        {"/.well-known/oauth-authorization-server": reply({"issuer": "https://other.example"})},
        {"/jwks.json": reply({"keys": []})},
        {"/jwks.json": reply({"keys": [{"kty": "RSA"}]})},
    ],
)
def test_ingress_rejects_wrong_revision_mode_issuer_or_keys(overrides):
    with pytest.raises(CloudProviderError):
        idp.verify_ingress(record(), "test", http_get=ingress(**overrides))


def test_production_ingress_rejects_a_passphrase_issuer():
    bad = ingress(
        "production",
        **{"/healthz": reply({"status": "ok", "deployment_sha": SHA, "sign_in": "passphrase"})},
    )
    with pytest.raises(CloudProviderError):
        idp.verify_ingress(
            record("production", env=environment("production")), "production", http_get=bad
        )


def test_new_container_is_bound_to_its_own_origin_with_one_create_and_one_restore(monkeypatch):
    created = record(env={**environment(), "IDP_PUBLIC_URL": ORIGIN})
    request = Mock(return_value={"resourceId": IDENTIFIER})
    apps = SimpleNamespace(
        project_id=PROJECT,
        list=Mock(return_value=[]),
        client=SimpleNamespace(request=request),
        restore=Mock(),
        start=Mock(),
        stop=Mock(),
    )
    states = iter([None, created, created])
    monkeypatch.setattr(idp, "owned_record", lambda *a, **k: next(states))
    monkeypatch.setattr(idp, "stop_owned", Mock(return_value=created))
    monkeypatch.setattr(idp, "wait_ready", lambda *a, **k: created)
    environment_without_url = {k: v for k, v in environment().items() if k != "IDP_PUBLIC_URL"}
    result = idp.deploy(apps, "test", IMAGE, environment_without_url)
    assert request.call_count == 1
    assert apps.restore.call_count == 1
    assert apps.start.call_count == 1
    restored = apps.restore.call_args.args[1]
    names = {e["name"]: e["value"] for e in restored["template"]["containers"][0]["env"]}
    assert names["IDP_PUBLIC_URL"] == ORIGIN
    assert result["issuer"] == ORIGIN
    assert result["lane"] == "test"


def test_explicit_public_url_needs_no_second_restore(monkeypatch):
    created = record()
    apps = SimpleNamespace(
        project_id=PROJECT,
        list=Mock(return_value=[]),
        client=SimpleNamespace(request=Mock(return_value={"resourceId": IDENTIFIER})),
        restore=Mock(),
        start=Mock(),
        stop=Mock(),
    )
    monkeypatch.setattr(idp, "owned_record", Mock(side_effect=[None, created]))
    monkeypatch.setattr(idp, "wait_ready", lambda *a, **k: created)
    idp.deploy(apps, "test", IMAGE, environment(), pinned_url=True)
    assert apps.restore.call_count == 0


def test_failed_update_restores_the_previous_revision(monkeypatch):
    previous = record(image="alice-chrome-browser.cr.cloud.ru/oauth-idp@sha256:" + "c" * 64)
    apps = SimpleNamespace(
        project_id=PROJECT,
        list=Mock(return_value=[previous]),
        client=SimpleNamespace(request=Mock()),
        restore=Mock(),
        start=Mock(),
        stop=Mock(),
    )
    monkeypatch.setattr(idp, "owned_record", Mock(return_value=previous))
    monkeypatch.setattr(idp, "stop_owned", Mock(return_value=previous))
    calls = {"count": 0}

    def flaky(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise CloudProviderError("x", code="idp_readiness_timeout")
        return previous

    monkeypatch.setattr(idp, "wait_ready", flaky)
    with pytest.raises(CloudProviderError):
        idp.deploy(apps, "test", IMAGE, environment(), pinned_url=True)
    assert apps.restore.call_args_list[-1].args[1] is previous


def test_errors_are_reduced_to_allow_listed_codes():
    exc = CloudProviderError("secret detail s" * 3, code="idp_ingress_unconfirmed", http_status=502)
    assert idp.safe_error(exc) == {"error": "idp_ingress_unconfirmed", "http_status": 502}
    leaked = CloudProviderError("token abc", code="token abc", http_status=200)
    assert idp.safe_error(leaked) == {"error": "idp_operation_failed", "http_status": 200}


def test_summary_lists_the_connector_and_github_callback_only_where_they_apply():
    test = idp.summary(record(), "test")
    assert test["issuer"] == ORIGIN
    assert test["github_callback_url"] is None
    production = idp.summary(record("production", env=environment("production")), "production")
    assert production["github_callback_url"] == ORIGIN + "/github/callback"
