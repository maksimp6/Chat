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
    apps.list.side_effect = [[], [{"name": NAME, "id": ID}]]
    apps.get.return_value = owned()
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


def test_wall_clock_timeout_still_stops_the_probe(monkeypatch):
    apps = Mock()
    apps.list.side_effect = [[], [{"name": NAME, "id": ID}]]
    apps.get.return_value = owned()

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
    apps.list.side_effect = [[], [{"name": NAME, "id": ID}]]
    apps.get.return_value = owned()
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
    apps.list.side_effect = [[], [{"name": NAME, "id": ID}]]
    apps.get.return_value = owned()
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
    apps.list.side_effect = [[], [], [], [{"name": NAME, "id": ID}]]
    apps.get.return_value = owned()
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
    apps.list.side_effect = [[], [], [], [{"name": NAME, "id": ID}]]
    apps.get.side_effect = [None, None, owned()]
    with pytest.raises(CloudProviderError, match="ambiguous"):
        probe.run_probe(apps, SHA, IMAGE)
    assert clock[0] == 4
    apps.create.assert_called_once()
    apps.stop.assert_called_once_with(NAME)


def test_discovery_timeout_still_waits_and_stops_late_resource(monkeypatch, clock):
    apps = Mock()
    apps.list.side_effect = [[]] * 33 + [[{"name": NAME, "id": ID}]]
    apps.get.return_value = owned()
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
    apps.list.side_effect = [[], [{"name": NAME, "id": ID}]]
    monkeypatch.setattr(probe, "verify_probe", Mock())

    def stalled_get(_name):
        probe.signal.raise_signal(probe.signal.SIGALRM)

    apps.get.side_effect = stalled_get
    with pytest.raises(CloudProviderError) as failure:
        probe.run_probe(apps, SHA, IMAGE)
    assert failure.value.code == "cleanup_failed"
    assert probe.signal.getitimer(probe.signal.ITIMER_REAL) == (0, 0)
    assert '"probe_cleanup_unconfirmed": true' in capsys.readouterr().out
    apps.stop.assert_not_called()


def test_cleanup_refuses_resource_with_unexpected_image(monkeypatch):
    apps = Mock()
    apps.list.side_effect = [[], [{"name": NAME, "id": ID}]]
    apps.get.return_value = {**owned(), "template": {"containers": [{"image": "other"}]}}
    monkeypatch.setattr(probe, "verify_probe", Mock())
    with pytest.raises(CloudProviderError) as failure:
        probe.run_probe(apps, SHA, IMAGE)
    assert failure.value.code == "cleanup_failed"
    apps.stop.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [{}, {"data": [], "total": 4}, {"items": [], "nextPageToken": "next"}, {"data": "invalid"}],
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


@pytest.mark.parametrize("payload", [{"registries": [], "totalCount": "0"}, {"totalCount": 0}])
def test_registry_uses_official_project_scoped_list_and_create_routes(payload):
    registry = Mock(project_id=ID)
    registry.client.request.return_value = payload
    probe.prepare_registry(registry)
    calls = registry.client.request.call_args_list
    assert calls[0].args == ("artifact_registry", "GET", f"/v1/projects/{ID}/registries")
    assert calls[0].kwargs == {"params": {"pageSize": 100}}
    assert calls[1].args == ("artifact_registry", "POST", f"/v1/projects/{ID}/registries")
    assert calls[1].kwargs == {
        "json_body": {"name": probe.REGISTRY, "isPublic": False, "registryType": "DOCKER"}
    }


@pytest.mark.parametrize("total", [True, -1, "-1", "01", "secret", 1.0, 1 << 63, 2])
def test_registry_total_count_must_prove_complete_inventory(total):
    registry = Mock(project_id=ID)
    registry.client.request.return_value = {"registries": [], "totalCount": total}
    with pytest.raises(CloudProviderError):
        probe.prepare_registry(registry)
    assert registry.client.request.call_count == 1


def test_existing_private_registry_is_reused_without_creation():
    registry = Mock(project_id=ID)
    registry.client.request.return_value = {
        "registries": [{"name": probe.REGISTRY, "isPublic": False, "registryType": "DOCKER"}],
        "totalCount": "1",
    }
    probe.prepare_registry(registry)
    assert registry.client.request.call_count == 1


def test_project_route_404_gets_read_only_legacy_diagnostic_and_preserves_failure(capsys):
    registry = Mock(project_id=ID)
    original = CloudProviderError("unavailable", code="provider_http_error", http_status=404)
    registry.client.request.side_effect = [
        original,
        {"data": {"registries": [], "totalCount": "0"}, "message": "secret-canary"},
    ]
    with pytest.raises(CloudProviderError) as failure:
        probe.prepare_registry(registry)
    assert failure.value is original
    calls = registry.client.request.call_args_list
    assert len(calls) == 2
    assert all(call.args[1] == "GET" for call in calls)
    assert calls[1].args == ("artifact_registry", "GET", "/v1/registries")
    assert calls[1].kwargs["params"] == {"projectId": ID, "pageSize": 100}
    output = capsys.readouterr().out
    assert "secret-canary" not in output
    assert "registry_legacy_response_shape" in output


@pytest.mark.parametrize("status", [401, 403, 500])
def test_registry_diagnostic_does_not_retry_auth_or_provider_errors(status):
    registry = Mock(project_id=ID)
    registry.client.request.side_effect = CloudProviderError("failed", http_status=status)
    with pytest.raises(CloudProviderError):
        probe.prepare_registry(registry)
    assert registry.client.request.call_count == 1


def test_legacy_diagnostic_failure_never_replaces_original_or_creates(capsys):
    registry = Mock(project_id=ID)
    original = CloudProviderError("missing", http_status=404)
    registry.client.request.side_effect = [
        original,
        CloudProviderError("secret-canary", http_status=403),
    ]
    with pytest.raises(CloudProviderError) as failure:
        probe.prepare_registry(registry)
    assert failure.value is original
    output = capsys.readouterr().out
    assert "secret-canary" not in output
    assert '"http_status": 403' in output
    assert all(call.args[1] == "GET" for call in registry.client.request.call_args_list)


def test_readiness_requires_expected_image_and_exact_static_response():
    apps = Mock()
    apps.get.return_value = {
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
    get = Mock(return_value=response)
    probe.verify_probe(apps, NAME, ID, IMAGE, http_get=get)
    get.assert_called_once_with(
        "https://" + NAME + ".containers.cloud.ru/healthz", timeout=3, allow_redirects=False
    )


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
        "id": ID,
        "template": {"containers": [{"image": IMAGE}]},
        "configuration": {"ingress": {"publicUri": uri}},
    }
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
