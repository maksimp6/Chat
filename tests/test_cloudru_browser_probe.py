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
