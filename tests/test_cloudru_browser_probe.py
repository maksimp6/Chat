from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from cloud.base import CloudProviderError
from scripts import cloudru_browser_probe as probe

SHA = "a" * 40
IMAGE = "alice-rdc-probe.cr.cloud.ru/chromium-probe@sha256:" + "b" * 64
ID = "6d94852d-98e4-4a98-8101-d103b9834d45"
NAME = "rdc-browser-probe-" + SHA[:12]


def owned():
    return {
        "name": NAME,
        "id": ID,
        "description": probe.DESCRIPTION,
        "template": {"containers": [{"image": IMAGE}]},
    }


def test_probe_passes_only_after_health_and_stops_verified_identity(monkeypatch):
    apps = Mock()
    apps.list.side_effect = [[], [{"name": NAME, "id": ID}], [owned()]]
    verify = Mock()
    monkeypatch.setattr(probe, "verify_probe", verify)
    result = probe.run_probe(apps, SHA, IMAGE)
    assert result["status"] == "BROWSER_PROBE_PASSED"
    spec = apps.create.call_args.args[0]
    assert spec.image == IMAGE
    assert spec.env == {"ALICE_RDC_MODE": "cloud-probe", "PORT": "8080"}
    assert (spec.min_instances, spec.max_instances) == (0, 1)
    verify.assert_called_once_with(apps, NAME, ID, IMAGE)
    apps.stop.assert_called_once_with(NAME)
    apps.get.assert_not_called()


def test_wall_clock_timeout_still_stops_the_probe(monkeypatch):
    apps = Mock()
    apps.list.side_effect = [[], [{"name": NAME, "id": ID}], [owned()]]

    def expire(*_args):
        probe.signal.raise_signal(probe.signal.SIGALRM)

    monkeypatch.setattr(probe, "verify_probe", expire)
    with pytest.raises(CloudProviderError) as failure:
        probe.run_probe(apps, SHA, IMAGE)
    assert failure.value.code == "browser_probe_timeout"
    assert probe.signal.getitimer(probe.signal.ITIMER_REAL) == (0, 0)
    apps.stop.assert_called_once_with(NAME)


def test_probe_stops_on_runtime_failure(monkeypatch):
    apps = Mock()
    apps.list.side_effect = [[], [{"name": NAME, "id": ID}], [owned()]]
    monkeypatch.setattr(
        probe,
        "verify_probe",
        Mock(side_effect=CloudProviderError("bad", code="browser_probe_failed")),
    )
    with pytest.raises(CloudProviderError, match="bad"):
        probe.run_probe(apps, SHA, IMAGE)
    apps.stop.assert_called_once_with(NAME)


def test_probe_never_takes_over_existing_resource():
    apps = Mock()
    apps.list.return_value = [{"name": NAME, "id": ID}]
    with pytest.raises(CloudProviderError) as failure:
        probe.run_probe(apps, SHA, IMAGE)
    assert failure.value.code == "already_exists"
    apps.create.assert_not_called()
    apps.stop.assert_not_called()


def test_ambiguous_create_timeout_stops_only_verified_owned_probe():
    apps = Mock()
    apps.list.side_effect = [[], [{"name": NAME, "id": ID}], [owned()]]
    apps.create.side_effect = CloudProviderError("timeout", code="provider_http_error")
    with pytest.raises(CloudProviderError, match="timeout"):
        probe.run_probe(apps, SHA, IMAGE)
    apps.stop.assert_called_once_with(NAME)


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    monkeypatch.setattr(probe.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(probe.time, "sleep", sleep)
    return now


def test_async_create_waits_for_inventory_before_verification(monkeypatch, clock):
    apps = Mock()
    apps.list.side_effect = [[], [], [], [{"name": NAME, "id": ID}], [owned()]]
    verify = Mock()
    monkeypatch.setattr(probe, "verify_probe", verify)
    assert probe.run_probe(apps, SHA, IMAGE)["status"] == "BROWSER_PROBE_PASSED"
    assert clock[0] == 2
    apps.create.assert_called_once()
    verify.assert_called_once_with(apps, NAME, ID, IMAGE)
    apps.stop.assert_called_once_with(NAME)


def test_ambiguous_create_waits_for_delayed_inventory_and_detail(clock):
    apps = Mock()
    apps.create.side_effect = CloudProviderError("ambiguous", code="provider_http_error")
    apps.list.side_effect = [[], [], [], [{"name": NAME, "id": ID}], [], [], [owned()]]
    with pytest.raises(CloudProviderError, match="ambiguous"):
        probe.run_probe(apps, SHA, IMAGE)
    assert clock[0] == 4
    apps.create.assert_called_once()
    apps.stop.assert_called_once_with(NAME)


def test_discovery_timeout_still_waits_and_stops_late_resource(monkeypatch, clock):
    apps = Mock()
    apps.list.side_effect = [[]] * 33 + [[{"name": NAME, "id": ID}], [owned()]]
    verify = Mock()
    monkeypatch.setattr(probe, "verify_probe", verify)
    with pytest.raises(CloudProviderError) as failure:
        probe.run_probe(apps, SHA, IMAGE)
    assert failure.value.code == "probe_identity_timeout"
    assert clock[0] == 32
    verify.assert_not_called()
    apps.stop.assert_called_once_with(NAME)


@pytest.mark.parametrize("create_error", [False, True])
def test_missing_resource_is_unconfirmed_cleanup_not_success(create_error, clock, capsys):
    apps = Mock()
    apps.list.return_value = []
    if create_error:
        apps.create.side_effect = CloudProviderError("ambiguous", code="provider_http_error")
    with pytest.raises(CloudProviderError) as failure:
        probe.run_probe(apps, SHA, IMAGE)
    assert failure.value.code == "cleanup_failed"
    assert clock[0] == (30 if create_error else 60)
    output = capsys.readouterr().out
    assert '"probe_cleanup_unconfirmed": true' in output
    assert NAME in output
    assert "BROWSER_PROBE_PASSED" not in output
    apps.stop.assert_not_called()


def test_cleanup_wall_clock_budget_reports_unconfirmed_resource(monkeypatch, capsys):
    apps = Mock()
    monkeypatch.setattr(probe, "verify_probe", Mock())

    def stalled_list(**_kwargs):
        if apps.list.call_count == 1:
            return []
        if apps.list.call_count == 2:
            return [{"name": NAME, "id": ID}]
        probe.signal.raise_signal(probe.signal.SIGALRM)

    apps.list.side_effect = stalled_list
    with pytest.raises(CloudProviderError) as failure:
        probe.run_probe(apps, SHA, IMAGE)
    assert failure.value.code == "cleanup_failed"
    assert probe.signal.getitimer(probe.signal.ITIMER_REAL) == (0, 0)
    assert '"probe_cleanup_unconfirmed": true' in capsys.readouterr().out
    apps.stop.assert_not_called()


def test_cleanup_refuses_resource_with_unexpected_image(monkeypatch):
    apps = Mock()
    apps.list.side_effect = [
        [],
        [{"name": NAME, "id": ID}],
        [{**owned(), "template": {"containers": [{"image": "other"}]}}],
    ]
    monkeypatch.setattr(probe, "verify_probe", Mock())
    with pytest.raises(CloudProviderError) as failure:
        probe.run_probe(apps, SHA, IMAGE)
    assert failure.value.code == "cleanup_failed"
    apps.stop.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {"registries": "bad"},
        {"data": [], "total": 4},
        {"items": [], "nextPageToken": "next"},
        {"code": 7, "message": "denied"},
    ],
)
def test_unknown_or_partial_registry_inventory_never_creates(payload):
    registry = Mock(project_id="project")
    registry.client.request.return_value = payload
    with pytest.raises(CloudProviderError):
        probe.prepare_registry(registry)
    assert registry.client.request.call_count == 1


def test_registry_diagnostic_exposes_structure_without_provider_values(capsys):
    secret = "provider-secret-canary"
    registry = Mock(project_id="project")
    registry.client.request.return_value = {
        "data": {"items": [{"name": secret}], "total": "1", secret: secret},
        "nextPageToken": secret,
        secret: secret,
    }
    with pytest.raises(CloudProviderError):
        probe.prepare_registry(registry)
    output = capsys.readouterr().out
    assert secret not in output
    assert '"stage": "registry_response_shape"' in output
    assert '"items": {"type": "array", "length": 1}' in output
    assert '"total": {"type": "string", "empty": false, "zero": false}' in output
    assert registry.client.request.call_count == 1


@pytest.mark.parametrize("value,kind", [(0, "integer"), (False, "boolean"), (None, "null")])
def test_registry_diagnostic_distinguishes_counter_types(value, kind):
    result = probe.registry_response_shape({"total": value})
    assert result["fields"]["total"]["type"] == kind


OP_ID = "8d786cac-04bb-4f43-bef3-383eab421ab5"
OTHER_ID = "4576b5bf-2cbc-463c-831b-3d74757cc360"


def registry_record(**changes):
    return {"id": ID, "name": probe.REGISTRY, "status": "ACTIVE", **changes}


@pytest.mark.parametrize("payload", [{}, {"registries": None}, {"registries": []}])
def test_empty_protojson_inventory_waits_for_created_registry_before_push(payload, clock):
    registry = Mock(project_id=ID)
    registry.client.request.side_effect = [
        payload,
        {"id": OP_ID},
        {"id": OP_ID, "done": True, "resourceId": ID},
        registry_record(status="CREATING"),
        registry_record(),
    ]
    probe.prepare_registry(registry)
    calls = registry.client.request.call_args_list
    assert calls[0].args == ("artifact_registry", "GET", "/v1/registries")
    assert calls[0].kwargs == {"params": {"projectId": ID, "pageSize": 100}}
    assert calls[1].args == ("artifact_registry", "POST", "/v1/registries")
    assert calls[1].kwargs == {
        "json_body": {
            "projectId": ID,
            "name": probe.REGISTRY,
            "isPublic": False,
            "registryType": "DOCKER",
        }
    }
    assert calls[2].args == ("artifact_registry", "GET", f"/v1/operations/{OP_ID}")
    assert calls[2].kwargs == {}
    assert calls[-1].args == ("artifact_registry", "GET", f"/v1/registries/{ID}")
    assert calls[-1].kwargs == {"params": {"projectId": ID}}
    assert clock[0] == 2


@pytest.mark.parametrize(
    "metadata",
    [{}, {"registryType": "DOCKER", "isPublic": False}, {"registryType": 0, "isPublic": False}],
)
def test_private_docker_protojson_defaults_are_checked_on_existing_registry(metadata):
    registry = Mock(project_id=ID)
    registry.client.request.side_effect = [
        {"registries": [registry_record(**metadata)]},
        registry_record(**metadata),
    ]
    probe.prepare_registry(registry)
    assert all(call.args[1] == "GET" for call in registry.client.request.call_args_list)


def test_paginated_inventory_reuses_registry_from_second_page():
    registry = Mock(project_id=ID)
    registry.client.request.side_effect = [
        {
            "registries": [registry_record(id=OTHER_ID, name="other-registry")],
            "nextPageToken": "next",
        },
        {"registries": [registry_record()]},
        registry_record(),
    ]
    probe.prepare_registry(registry)
    calls = registry.client.request.call_args_list
    assert calls[1].kwargs["params"]["pageToken"] == "next"
    assert all(call.args[1] == "GET" for call in calls)


@pytest.mark.parametrize(
    "second",
    [
        {"registries": [registry_record()], "nextPageToken": "same"},
        {"registries": [registry_record(id=OTHER_ID)]},
        {"registries": "bad"},
    ],
)
def test_incomplete_or_duplicate_registry_pages_never_create(second):
    registry = Mock(project_id=ID)
    registry.client.request.side_effect = [
        {
            "registries": [registry_record(id=OTHER_ID, name="other-registry")],
            "nextPageToken": "same",
        },
        second,
    ]
    with pytest.raises(CloudProviderError):
        probe.prepare_registry(registry)
    assert all(call.args[1] == "GET" for call in registry.client.request.call_args_list)


@pytest.mark.parametrize(
    "changes",
    [
        {"isPublic": True},
        {"registryType": "NPM"},
        {"registryType": False},
        {"name": "other-registry"},
        {"id": OTHER_ID},
        {"status": "ERROR"},
    ],
)
def test_registry_detail_must_match_expected_private_ready_resource(changes):
    registry = Mock(project_id=ID)
    registry.client.request.side_effect = [
        {"registries": [registry_record()]},
        registry_record(**changes),
    ]
    with pytest.raises(CloudProviderError):
        probe.prepare_registry(registry)
    assert all(call.args[1] == "GET" for call in registry.client.request.call_args_list)


def test_failed_create_operation_stops_before_registry_login(clock, capsys):
    registry = Mock(project_id=ID)
    registry.client.request.side_effect = [
        {},
        {"id": OP_ID},
        {"id": OP_ID, "done": True, "error": {"code": 7, "message": "secret-canary"}},
    ]
    with pytest.raises(CloudProviderError) as failure:
        probe.prepare_registry(registry)
    assert failure.value.code == "registry_create_failed"
    assert "secret-canary" not in capsys.readouterr().out
    assert registry.client.request.call_count == 3


def test_registry_create_timeout_never_posts_twice(clock):
    registry = Mock(project_id=ID)
    operation = {"id": OP_ID}
    registry.client.request.side_effect = [{}, operation] + [operation] * 30
    with pytest.raises(CloudProviderError) as failure:
        probe.prepare_registry(registry)
    assert failure.value.code == "registry_create_timeout"
    assert sum(call.args[1] == "POST" for call in registry.client.request.call_args_list) == 1
    assert clock[0] == 30


def test_registry_operation_identity_change_is_rejected(clock):
    registry = Mock(project_id=ID)
    registry.client.request.side_effect = [
        {},
        {"id": OP_ID},
        {"id": OTHER_ID, "done": True, "resourceId": ID},
    ]
    with pytest.raises(CloudProviderError) as failure:
        probe.prepare_registry(registry)
    assert failure.value.code == "invalid_response"


def test_registry_operation_propagation_404_is_polled_without_recreating(clock):
    registry = Mock(project_id=ID)
    registry.client.request.side_effect = [
        {},
        {"id": OP_ID},
        CloudProviderError("not visible", http_status=404),
        {"id": OP_ID, "done": True, "resourceId": ID},
        registry_record(),
    ]
    probe.prepare_registry(registry)
    assert sum(call.args[1] == "POST" for call in registry.client.request.call_args_list) == 1
    assert clock[0] == 2


def test_provider_opaque_operation_id_is_preserved_and_path_quoted(clock):
    registry = Mock(project_id=ID)
    operation_id = "registry-create/opaque?request=1"
    registry.client.request.side_effect = [
        {},
        {"id": operation_id},
        {"id": operation_id, "done": True, "resourceId": ID},
        registry_record(),
    ]
    probe.prepare_registry(registry)
    operation_get = registry.client.request.call_args_list[2]
    assert operation_get.args == (
        "artifact_registry",
        "GET",
        "/v1/operations/registry-create%2Fopaque%3Frequest%3D1",
    )


@pytest.mark.parametrize("operation_id", [".", ".."])
def test_dot_segment_operation_ids_cannot_redirect_authenticated_request(operation_id):
    registry = Mock(project_id=ID)
    registry.client.request.side_effect = [{}, {"id": operation_id}]
    with pytest.raises(CloudProviderError) as failure:
        probe.prepare_registry(registry)
    assert failure.value.code == "registry_creation_unconfirmed"
    assert registry.client.request.call_count == 2


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_registry_provider_errors_are_not_retried_or_used_for_creation(status):
    registry = Mock(project_id=ID)
    registry.client.request.side_effect = CloudProviderError("failed", http_status=status)
    with pytest.raises(CloudProviderError):
        probe.prepare_registry(registry)
    assert registry.client.request.call_count == 1


def test_readiness_requires_expected_image_and_exact_static_response():
    apps = Mock()
    apps.get.return_value = {
        "name": NAME,
        "id": ID,
        "status": "RUNNING",
        "template": {"containers": [{"image": IMAGE}]},
        "configuration": {"ingress": {"publicUri": NAME + ".containers.cloud.ru"}},
    }
    response = Mock(status_code=200)
    response.json.return_value = {
        "mode": "cloud-probe",
        "browser_ready": True,
        "smoke_passed": True,
    }
    apps.list.return_value = [apps.get.return_value]
    get = Mock(return_value=response)
    probe.verify_probe(apps, NAME, ID, IMAGE, http_get=get)
    get.assert_called_once_with(
        "https://" + NAME + ".containers.cloud.ru/healthz", timeout=3, allow_redirects=False
    )
    apps.get.assert_not_called()


@pytest.mark.parametrize(
    "uri",
    [
        "http://bad.containers.cloud.ru",
        "https://evil.example",
        "https://user@probe.containers.cloud.ru",
        "https://probe.containers.cloud.ru:444",
    ],
)
def test_readiness_rejects_untrusted_origin(uri):
    apps = Mock()
    apps.get.return_value = {
        "name": NAME,
        "id": ID,
        "template": {"containers": [{"image": IMAGE}]},
        "configuration": {"ingress": {"publicUri": uri}},
    }
    apps.list.return_value = [apps.get.return_value]
    get = Mock()
    with pytest.raises(CloudProviderError):
        probe.verify_probe(apps, NAME, ID, IMAGE, http_get=get)
    get.assert_not_called()


def test_production_workflow_is_master_only_and_scopes_credentials():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/cloudru-browser-probe.yml").read_text())
    job = workflow["jobs"]["probe"]
    assert job["if"] == "github.ref == 'refs/heads/master'"
    assert job["environment"] == "production"
    assert "env" not in job
    assert job["steps"][0]["with"]["ref"] == "${{ github.sha }}"
    assert "merge-base --is-ancestor" in job["steps"][1]["run"]
    assert set(job["steps"][-1]["env"]) == {
        "SOURCE_SHA",
        "CLOUDRU_PROJECT_ID",
        "CLOUDRU_IAM_KEY_ID",
        "CLOUDRU_IAM_KEY_SECRET",
    }
