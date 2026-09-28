import subprocess
from unittest.mock import Mock

import pytest

from cloud.base import CloudProviderError
from cloud.cloudru.client import CloudRuClient
from cloud.cloudru.container_apps_client import (
    CloudRuContainerAppsClient,
    ContainerSpec,
    estimate_monthly_cost,
)
from cloud.cloudru.registry_client import CloudRuRegistryClient, ImageRef
from cloudru_iam import CloudRuIamClient

DIGEST = "sha256:" + "a" * 64


class RecordingClient:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def request(self, service, method, path, *, params=None, json_body=None):
        self.calls.append((service, method, path, params, json_body))
        if not self.responses:
            return {}
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _iam():
    return CloudRuIamClient(key_id="key-id", key_secret="key-secret")


def _app(status="RUNNING", image="img", uri="alice.containers.cloud.ru"):
    return {
        "name": "alice-pro",
        "status": status,
        "id": "c-1",
        "configuration": {"ingress": {"publiclyAccessible": True, "publicUri": uri}},
        "template": {
            "scaling": {"minInstanceCount": 0, "maxInstanceCount": 1},
            "containers": [{"name": "alice-pro", "image": image, "containerPort": 8080}],
        },
    }


def test_control_plane_client_ignores_foundation_models_api_key(monkeypatch):
    monkeypatch.setenv("CLOUDRU_API_KEY", "fm-key")
    assert CloudRuClient(api_key_auth=False).api_key is None
    assert CloudRuClient().api_key == "fm-key"
    assert CloudRuClient().endpoint("container_apps") == "https://containers.api.cloud.ru"
    assert CloudRuClient().endpoint("artifact_registry") == "https://ar.api.cloud.ru"


def test_ensure_registry_creates_private_docker_registry_when_missing():
    client = RecordingClient([{"registries": [{"name": "other"}]}, {"name": "alice-pro"}])
    reg = CloudRuRegistryClient(project_id="p1", client=client, iam_client=_iam())

    assert reg.ensure_registry("alice-pro") == {"name": "alice-pro"}
    assert client.calls[0][:4] == (
        "artifact_registry",
        "GET",
        "/v1/registries",
        {"projectId": "p1"},
    )
    assert client.calls[1][4] == {
        "projectId": "p1",
        "name": "alice-pro",
        "isPublic": False,
        "registryType": "DOCKER",
    }


def test_ensure_registry_reuses_existing():
    client = RecordingClient([{"registries": [{"name": "alice-pro", "id": "r1"}]}])
    reg = CloudRuRegistryClient(project_id="p1", client=client, iam_client=_iam())
    assert reg.ensure_registry("alice-pro")["id"] == "r1"
    assert len(client.calls) == 1


def test_build_and_push_passes_secret_on_stdin_and_pins_digest():
    runs = []

    def runner(argv, **kwargs):
        runs.append((argv, kwargs))
        stdout = f"sha: digest: {DIGEST} size: 1" if argv[1] == "push" else ""
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    reg = CloudRuRegistryClient(
        project_id="p1", client=RecordingClient(), iam_client=_iam(), runner=runner
    )
    ref = reg.build_and_push(registry_name="alice-pro", repository="alice-pro", tag="abc123")

    login_argv, login_kwargs = runs[0]
    assert login_argv[:3] == ["docker", "login", "alice-pro.cr.cloud.ru"]
    assert "key-secret" not in login_argv
    assert login_kwargs["input"] == "key-secret"
    assert [argv[1] for argv, _ in runs] == ["login", "build", "push"]
    assert ref == ImageRef("alice-pro.cr.cloud.ru", "alice-pro", "abc123", DIGEST)
    assert ref.pinned == f"alice-pro.cr.cloud.ru/alice-pro@{DIGEST}"


def test_build_failure_raises_without_pushing():
    def runner(argv, **kwargs):
        code = 1 if argv[1] == "build" else 0
        return subprocess.CompletedProcess(argv, code, stdout="", stderr="boom")

    reg = CloudRuRegistryClient(
        project_id="p1", client=RecordingClient(), iam_client=_iam(), runner=runner
    )
    with pytest.raises(CloudProviderError, match="build failed"):
        reg.build_and_push(registry_name="alice-pro", repository="alice-pro", tag="abc")


def test_deploy_creates_always_on_service_when_missing():
    client = RecordingClient([CloudProviderError("nf", http_status=404), {"done": False}])
    apps = CloudRuContainerAppsClient(project_id="p1", client=client)
    spec = ContainerSpec(name="alice-pro", image="img@sha", env={"B": "2", "A": "1"})

    assert apps.deploy(spec)["action"] == "create"
    service, method, path, _, body = client.calls[1]
    assert (service, method, path) == ("container_apps", "POST", "/v2/containers")
    assert body["template"]["scaling"] == {"minInstanceCount": 1, "maxInstanceCount": 1}
    container = body["template"]["containers"][0]
    assert container["resources"] == {"cpu": "0.5", "memory": "1024Mi"}
    assert container["containerPort"] == 8080
    assert container["env"] == [{"name": "A", "value": "1"}, {"name": "B", "value": "2"}]
    assert body["configuration"]["ingress"]["publiclyAccessible"] is True


def test_deploy_patches_existing_service_with_new_image():
    client = RecordingClient([_app(), _app(), {"done": False}])
    apps = CloudRuContainerAppsClient(project_id="p1", client=client)

    result = apps.deploy(ContainerSpec(name="alice-pro", image="new@sha"))

    assert result["action"] == "update"
    _, method, path, params, body = client.calls[2]
    assert (method, path, params) == ("PATCH", "/v2/containers/alice-pro", {"projectId": "p1"})
    assert body["template"]["containers"][0]["image"] == "new@sha"
    assert body["template"]["scaling"]["minInstanceCount"] == 1
    assert "status" not in body and "id" not in body


def test_status_is_condensed_and_reports_missing():
    apps = CloudRuContainerAppsClient(project_id="p1", client=RecordingClient([_app()]))
    status = apps.status("alice-pro")
    assert status["public_uri"] == "alice.containers.cloud.ru"
    assert status["image"] == "img"

    missing = CloudRuContainerAppsClient(
        project_id="p1", client=RecordingClient([CloudProviderError("nf", http_status=404)])
    )
    assert missing.status("alice-pro")["status"] == "NOT_FOUND"


def test_wait_until_ready_waits_for_new_image_and_fails_fast():
    client = RecordingClient(
        [_app("DEPLOYING", "old"), _app("RUNNING", "old"), _app("RUNNING", "new")]
    )
    apps = CloudRuContainerAppsClient(project_id="p1", client=client, sleep=lambda _: None)
    assert apps.wait_until_ready("alice-pro", image="new")["image"] == "new"

    failed = CloudRuContainerAppsClient(
        project_id="p1", client=RecordingClient([_app("FAILED")]), sleep=lambda _: None
    )
    with pytest.raises(CloudProviderError, match="FAILED"):
        failed.wait_until_ready("alice-pro")


def test_health_check_retries_until_ok():
    responses = [Mock(status_code=503), Mock(status_code=200)]
    apps = CloudRuContainerAppsClient(
        project_id="p1",
        client=RecordingClient(),
        sleep=lambda _: None,
        http_get=lambda url, timeout: responses.pop(0),
    )
    result = apps.health_check("alice.containers.cloud.ru")
    assert result == {
        "url": "https://alice.containers.cloud.ru/healthz",
        "http_status": 200,
        "attempts": 2,
    }


def test_delete_start_stop_paths():
    client = RecordingClient()
    apps = CloudRuContainerAppsClient(project_id="p1", client=client)
    apps.delete("alice-pro")
    apps.start("alice-pro")
    apps.stop("alice-pro")
    assert [(c[1], c[2]) for c in client.calls] == [
        ("DELETE", "/v2/containers/alice-pro"),
        ("POST", "/v2/containers/alice-pro:start"),
        ("POST", "/v2/containers/alice-pro:stop"),
    ]


@pytest.mark.parametrize("name", ["Alice", "a/b", "", "-x"])
def test_invalid_names_rejected(name):
    apps = CloudRuContainerAppsClient(project_id="p1", client=RecordingClient())
    with pytest.raises(CloudProviderError):
        apps.delete(name)


def test_cost_estimate_for_always_on_half_vcpu():
    estimate = estimate_monthly_cost("0.5", 1)
    assert estimate["vcpu_hours"] == 365.0
    assert estimate["gb_hours"] == 730.0
    assert 1400 < estimate["rub_per_month"] < 1600
    assert estimate_monthly_cost("0.5", 0)["rub_per_month"] == 0
