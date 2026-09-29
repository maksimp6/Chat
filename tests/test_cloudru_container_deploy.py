import os
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
    client = RecordingClient(
        [
            {
                "registries": [
                    {"name": "alice-pro", "id": "r1", "isPublic": False, "registryType": "DOCKER"}
                ]
            }
        ]
    )
    reg = CloudRuRegistryClient(project_id="p1", client=client, iam_client=_iam())
    assert reg.ensure_registry("alice-pro")["id"] == "r1"
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"isPublic": True, "registryType": "DOCKER"},
        {"isPublic": "false", "registryType": "DOCKER"},
        {"isPublic": False, "registryType": "MAVEN"},
    ],
)
def test_deploy_rejects_unsafe_or_unknown_registry_before_publishing(metadata):
    client = RecordingClient([{"registries": [{"name": "alice-pro", **metadata}]}])
    reg = CloudRuRegistryClient(project_id="p1", client=client, iam_client=_iam())
    with pytest.raises(CloudProviderError, match="private and type DOCKER"):
        reg.ensure_registry("alice-pro")
    assert [call[1] for call in client.calls] == ["GET"]


def test_deploy_cannot_request_a_public_registry():
    client = RecordingClient()
    reg = CloudRuRegistryClient(project_id="p1", client=client, iam_client=_iam())
    with pytest.raises(CloudProviderError, match="private registry"):
        reg.ensure_registry("alice-pro", is_public=True)
    assert client.calls == []


@pytest.mark.parametrize(
    "domain",
    [
        "evil.example",
        "cr.cloud.ru.evil.example",
        "https://cr.cloud.ru",
        "cr.cloud.ru:443",
    ],
)
def test_registry_domain_allowlist_rejects_untrusted_hosts_before_login(domain):
    runner = Mock(side_effect=AssertionError("docker login must not run"))
    with pytest.raises(CloudProviderError, match="CLOUDRU_REGISTRY_DOMAIN"):
        CloudRuRegistryClient(
            project_id="p1",
            client=RecordingClient(),
            iam_client=_iam(),
            registry_domain=domain,
            runner=runner,
        ).docker_login("alice-pro")
    runner.assert_not_called()


def test_registry_domain_defaults_to_official_cloudru_host():
    reg = CloudRuRegistryClient(project_id="p1", client=RecordingClient(), iam_client=_iam())
    assert reg.registry_host("alice-pro") == "alice-pro.cr.cloud.ru"


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
    configs = {kwargs["env"]["DOCKER_CONFIG"] for _, kwargs in runs}
    assert len(configs) == 1
    assert not os.path.exists(configs.pop())  # removed after the push
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


def test_deploy_creates_scale_to_zero_service_when_missing():
    client = RecordingClient([CloudProviderError("nf", http_status=404), {"done": False}])
    apps = CloudRuContainerAppsClient(project_id="p1", client=client)
    spec = ContainerSpec(name="alice-pro", image="img@sha", env={"B": "2", "A": "1"})

    assert apps.deploy(spec)["action"] == "create"
    service, method, path, _, body = client.calls[1]
    assert (service, method, path) == ("container_apps", "POST", "/v2/containers")
    assert body["template"]["scaling"] == {"minInstanceCount": 0, "maxInstanceCount": 1}
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
    assert (method, path, params) == ("PATCH", "/v2/containers/alice-pro", None)
    assert body["projectId"] == "p1"
    assert body["template"]["containers"][0]["image"] == "new@sha"
    assert body["template"]["scaling"]["minInstanceCount"] == 0
    assert "status" not in body and "id" not in body


def test_get_uses_v2_contract_with_project_query():
    client = RecordingClient([_app()])
    apps = CloudRuContainerAppsClient(project_id="p1", client=client)

    assert apps.get("alice-pro")["id"] == "c-1"
    assert client.calls == [
        ("container_apps", "GET", "/v2/containers/alice-pro", {"projectId": "p1"}, None)
    ]


def test_list_uses_v2_pagination_contract():
    client = RecordingClient(
        [
            {"data": [{"name": "one"}], "nextPageToken": "next", "total": 2},
            {"data": [{"name": "two"}], "nextPageToken": "", "total": 2},
        ]
    )
    apps = CloudRuContainerAppsClient(project_id="p1", client=client)

    assert apps.list(page_size=50, filter_expr="status=RUNNING", order_by="name") == [
        {"name": "one"},
        {"name": "two"},
    ]
    assert client.calls[0][1:4] == (
        "GET",
        "/v2/containers",
        {"projectId": "p1", "pageSize": 50, "filter": "status=RUNNING", "orderBy": "name"},
    )
    assert client.calls[1][1:4] == (
        "GET",
        "/v2/containers",
        {
            "projectId": "p1",
            "pageSize": 50,
            "pageToken": "next",
            "filter": "status=RUNNING",
            "orderBy": "name",
        },
    )


def test_list_rejects_non_positive_page_size():
    apps = CloudRuContainerAppsClient(project_id="p1", client=RecordingClient())
    with pytest.raises(CloudProviderError, match="page_size"):
        apps.list(page_size=0)


def test_list_rejects_repeated_pagination_token():
    apps = CloudRuContainerAppsClient(
        project_id="p1",
        client=RecordingClient(
            [
                {"data": [], "nextPageToken": "same"},
                {"data": [], "nextPageToken": "same"},
            ]
        ),
    )
    with pytest.raises(CloudProviderError, match="pagination token"):
        apps.list()


def test_list_rejects_invalid_pagination_payload():
    apps = CloudRuContainerAppsClient(
        project_id="p1",
        client=RecordingClient([{"data": "not-a-list"}]),
    )
    with pytest.raises(CloudProviderError, match="invalid list payload"):
        apps.list()


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


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"name": "Bad_Name"}, "name must"),
        ({"image": ""}, "image is required"),
        ({"cpu": "3"}, "cpu must"),
        ({"min_instances": 2, "max_instances": 1}, "min_instances"),
    ],
)
def test_container_spec_validation(overrides, match):
    spec = ContainerSpec(**{"name": "alice-pro", "image": "img", **overrides})
    with pytest.raises(CloudProviderError, match=match):
        spec.validate()


def test_project_id_is_required(monkeypatch):
    monkeypatch.delenv("CLOUDRU_PROJECT_ID", raising=False)
    apps = CloudRuContainerAppsClient(client=RecordingClient())
    with pytest.raises(CloudProviderError, match="CLOUDRU_PROJECT_ID"):
        apps.delete("alice-pro")
    reg = CloudRuRegistryClient(client=RecordingClient(), iam_client=_iam())
    with pytest.raises(CloudProviderError, match="CLOUDRU_PROJECT_ID"):
        reg.list_registries()


def test_get_reraises_non_404_errors():
    apps = CloudRuContainerAppsClient(
        project_id="p1", client=RecordingClient([CloudProviderError("boom", http_status=500)])
    )
    with pytest.raises(CloudProviderError, match="boom"):
        apps.get("alice-pro")


def test_update_requires_existing_service():
    apps = CloudRuContainerAppsClient(
        project_id="p1", client=RecordingClient([CloudProviderError("nf", http_status=404)])
    )
    with pytest.raises(CloudProviderError, match="not found"):
        apps.update(ContainerSpec(name="alice-pro", image="img"))


def test_wait_until_ready_times_out():
    apps = CloudRuContainerAppsClient(
        project_id="p1", client=RecordingClient([_app("DEPLOYING")]), sleep=lambda _: None
    )
    with pytest.raises(CloudProviderError, match="not ready"):
        apps.wait_until_ready("alice-pro", timeout_s=0)


def test_health_check_reports_last_error():
    import requests

    def boom(url, timeout):
        raise requests.ConnectionError("down")

    apps = CloudRuContainerAppsClient(
        project_id="p1", client=RecordingClient(), sleep=lambda _: None, http_get=boom
    )
    with pytest.raises(CloudProviderError, match="ConnectionError"):
        apps.health_check("https://alice.example", attempts=2)


def test_image_ref_without_digest_uses_tag():
    ref = ImageRef("r.cr.cloud.ru", "alice-pro", "abc")
    assert ref.pinned == ref.tagged == "r.cr.cloud.ru/alice-pro:abc"


def test_registry_rejects_invalid_names_and_tags():
    reg = CloudRuRegistryClient(project_id="p1", client=RecordingClient(), iam_client=_iam())
    with pytest.raises(CloudProviderError, match="registry_name"):
        reg.registry_host("Bad Name")
    with pytest.raises(CloudProviderError, match="tag is invalid"):
        reg.build_and_push(registry_name="alice-pro", repository="alice-pro", tag="bad tag")


def test_registry_list_payload_must_be_a_list():
    reg = CloudRuRegistryClient(
        project_id="p1", client=RecordingClient([{"registries": "nope"}]), iam_client=_iam()
    )
    with pytest.raises(CloudProviderError, match="invalid"):
        reg.list_registries()


def test_delete_registry_validates_id():
    client = RecordingClient()
    reg = CloudRuRegistryClient(project_id="p1", client=client, iam_client=_iam())
    reg.delete_registry("r-1")
    assert client.calls[0][:3] == ("artifact_registry", "DELETE", "/v1/registries/r-1")
    with pytest.raises(CloudProviderError):
        reg.delete_registry("../x")


def test_docker_login_needs_key_pair_and_reports_failure():
    no_keys = CloudRuRegistryClient(
        project_id="p1",
        client=RecordingClient(),
        iam_client=CloudRuIamClient(key_id="", key_secret=""),
    )
    with pytest.raises(CloudProviderError, match="CLOUDRU_IAM_KEY_ID"):
        no_keys.docker_login("alice-pro")

    def denied(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="unauthorized")

    reg = CloudRuRegistryClient(
        project_id="p1", client=RecordingClient(), iam_client=_iam(), runner=denied
    )
    with pytest.raises(CloudProviderError, match="unauthorized"):
        reg.docker_login("alice-pro")


def test_push_without_digest_fails_closed():
    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="pushed", stderr="")

    reg = CloudRuRegistryClient(
        project_id="p1", client=RecordingClient(), iam_client=_iam(), runner=runner
    )
    with pytest.raises(CloudProviderError, match="no sha256 digest"):
        reg.build_and_push(registry_name="alice-pro", repository="alice-pro", tag="abc")


def _verified_client(responses, http_status=200):
    return CloudRuContainerAppsClient(
        project_id="p1",
        client=RecordingClient(responses),
        sleep=lambda _: None,
        http_get=lambda url, timeout: Mock(status_code=http_status),
    )


def test_deploy_verified_success_reports_previous_image():
    apps = _verified_client(
        [_app(image="old"), _app(image="old"), _app(image="old"), {}, _app(image="new")]
    )
    result = apps.deploy_verified(ContainerSpec(name="alice-pro", image="new"))
    assert result["action"] == "update"
    assert result["previous_image"] == "old"
    assert result["health"]["http_status"] == 200


def test_deploy_verified_restores_whole_previous_configuration():
    previous = _app(image="old")
    previous["configuration"]["ingress"].update(
        {
            "internalUri": "internal.example",
            "additionalPortMappings": [{"targetPort": 9000, "url": "https://response-only"}],
        }
    )
    previous["template"]["containers"][0]["env"] = [{"name": "ALICE_DATABASE_URL", "value": "a"}]
    previous["template"]["scaling"] = {"minInstanceCount": 1, "maxInstanceCount": 1}
    apps = _verified_client(
        [
            previous,  # captured before deploy
            previous,  # deploy -> get
            previous,  # update -> get
            {},  # patch
            _app(image="new"),  # ready on new image
            {},  # restore patch
            _app(image="old"),  # ready on old image
        ],
        http_status=503,
    )
    spec = ContainerSpec(name="alice-pro", image="new", env={"ALICE_DATABASE_URL": "b"})
    with pytest.raises(CloudProviderError, match="rolled back to old"):
        apps.deploy_verified(spec)
    patches = [c for c in apps.client.calls if c[1] == "PATCH"]
    assert len(patches) == 2
    restored = patches[1][4]
    assert patches[1][3] is None
    assert restored["projectId"] == "p1"
    assert restored["template"]["containers"][0]["env"] == [
        {"name": "ALICE_DATABASE_URL", "value": "a"}
    ]
    assert restored["template"]["scaling"] == {"minInstanceCount": 1, "maxInstanceCount": 1}
    assert "status" not in restored and "id" not in restored
    ingress = restored["configuration"]["ingress"]
    assert "publicUri" not in ingress and "internalUri" not in ingress
    assert ingress["additionalPortMappings"] == [{"targetPort": 9000}]


def test_iam_failure_becomes_provider_error():
    from cloudru_iam import CloudRuIamError

    iam = Mock()
    iam._token.side_effect = CloudRuIamError("invalid key")
    client = CloudRuClient(iam_client=iam, api_key_auth=False)
    with pytest.raises(CloudProviderError) as info:
        client._auth_header()
    assert info.value.code == "auth_failed"


def _deploy_script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "cloudru_deploy.py"
    spec = importlib.util.spec_from_file_location("cloudru_deploy_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SHA = "a" * 40


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
    )


def test_export_commit_builds_only_committed_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "remote", "add", "origin", str(repo))
    (repo / "app.py").write_text("committed\n")
    _git(repo, "add", "app.py")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    (repo / "app.py").write_text("edited\n")
    (repo / ".env").write_text("SECRET=1\n")
    (repo / "Dockerfile").write_text("FROM scratch\n")

    out = tmp_path / "out"
    out.mkdir()
    _deploy_script()._export_commit(sha, str(repo), str(out))
    assert sorted(p.name for p in out.iterdir()) == ["app.py"]
    assert (out / "app.py").read_text() == "committed\n"


@pytest.mark.parametrize(
    ("tag", "message"),
    [("latest", "full 40-character"), (SHA, "cannot fetch canonical master")],
)
def test_export_commit_rejects_bad_tags(tmp_path, tag, message):
    with pytest.raises(CloudProviderError, match=message):
        _deploy_script()._export_commit(tag, str(tmp_path), str(tmp_path))


def test_export_rejects_side_branch_even_with_stale_local_master(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "remote", "add", "origin", str(repo))
    (repo / "app.py").write_text("trusted\n")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "trusted")
    _git(repo, "checkout", "-qb", "unreviewed")
    (repo / "app.py").write_text("unreviewed\n")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "side")
    sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    _git(repo, "update-ref", "refs/remotes/origin/master", sha)
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(CloudProviderError, match="not on canonical master"):
        _deploy_script()._export_commit(sha, str(repo), str(out))
    assert list(out.iterdir()) == []


def test_export_reports_archive_failure_after_master_check(tmp_path):
    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1 if "archive" in argv else 0, b"", b"failed")

    with pytest.raises(CloudProviderError, match="cannot export commit"):
        _deploy_script()._export_commit(SHA, str(tmp_path), str(tmp_path), runner=runner)


def _run_deploy(monkeypatch, capsys, argv, **env):
    for name in ("ALICE_REQUIRE_SHORT_TOKEN", "ALICE_SHORT_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    code = _deploy_script().main(argv)
    return code, capsys.readouterr().out


@pytest.mark.parametrize(
    ("env_args", "env"),
    [
        ([], {}),
        (["--env", "ALICE_SHORT_TOKEN"], {"ALICE_SHORT_TOKEN": "t"}),
        (
            ["--env", "ALICE_REQUIRE_SHORT_TOKEN", "--env", "ALICE_SHORT_TOKEN"],
            {"ALICE_REQUIRE_SHORT_TOKEN": "0", "ALICE_SHORT_TOKEN": "t"},
        ),
        (
            ["--env", "ALICE_REQUIRE_SHORT_TOKEN", "--env", "ALICE_SHORT_TOKEN"],
            {"ALICE_REQUIRE_SHORT_TOKEN": "1", "ALICE_SHORT_TOKEN": ""},
        ),
    ],
)
def test_deploy_requires_short_token_gate(monkeypatch, capsys, env_args, env):
    code, out = _run_deploy(monkeypatch, capsys, ["deploy", "--tag", SHA, *env_args], **env)
    assert code == 1
    assert "ALICE_REQUIRE_SHORT_TOKEN" in out


def test_invalid_repository_name_fails_before_provider_calls(monkeypatch, capsys):
    monkeypatch.setenv("CLOUDRU_REPOSITORY_NAME", "Bad_Name")
    code, out = _run_deploy(monkeypatch, capsys, ["estimate"])
    assert code == 1
    assert "CLOUDRU_REPOSITORY_NAME" in out


@pytest.mark.parametrize("name", sorted(_deploy_script().CONTROL_PLANE_ENV))
def test_control_plane_secrets_rejected_before_any_provider_calls(monkeypatch, capsys, name):
    script = _deploy_script()
    registry = Mock(side_effect=AssertionError("must not construct a provider"))
    monkeypatch.setattr(script, "CloudRuRegistryClient", registry)
    monkeypatch.setenv(name, "private-test-value")
    assert script.main(["deploy", "--tag", SHA, "--env", name]) == 1
    output = capsys.readouterr().out
    assert "control-plane credentials" in output
    assert "private-test-value" not in output
    registry.assert_not_called()


@pytest.mark.parametrize("database_url", [None, "", "sqlite:///alice.db"])
def test_deploy_requires_postgres_before_provider_calls(monkeypatch, capsys, database_url):
    monkeypatch.delenv("ALICE_DATABASE_URL", raising=False)
    env = {"ALICE_REQUIRE_SHORT_TOKEN": "1", "ALICE_SHORT_TOKEN": "private-test-value"}
    argv = [
        "deploy",
        "--tag",
        SHA,
        "--env",
        "ALICE_REQUIRE_SHORT_TOKEN",
        "--env",
        "ALICE_SHORT_TOKEN",
    ]
    if database_url is not None:
        env["ALICE_DATABASE_URL"] = database_url
        argv += ["--env", "ALICE_DATABASE_URL"]
    code, output = _run_deploy(monkeypatch, capsys, argv, **env)
    assert code == 1
    assert "durable PostgreSQL" in output
    assert "private-test-value" not in output


def test_successful_deploy_passes_app_configuration_only(monkeypatch):
    import argparse

    script = _deploy_script()
    app_env = {
        "ALICE_REQUIRE_SHORT_TOKEN": "1",
        "ALICE_SHORT_TOKEN": "test-token",
        "ALICE_DATABASE_URL": "postgresql://test/db",
    }
    for name, value in app_env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CLOUDRU_IAM_KEY_SECRET", "control-secret")
    registry, apps = Mock(), Mock()
    registry.build_and_push.return_value = ImageRef("registry", "alice", SHA, DIGEST)
    apps.deploy_verified.return_value = {"action": "create"}
    monkeypatch.setattr(script, "CloudRuRegistryClient", lambda: registry)
    monkeypatch.setattr(script, "CloudRuContainerAppsClient", lambda: apps)
    monkeypatch.setattr(script, "_export_commit", Mock())
    result = script.cmd_deploy(
        argparse.Namespace(env=list(app_env), tag=SHA, context=".", timeout=30)
    )
    assert apps.deploy_verified.call_args.args[0].env == app_env
    assert result["image"].endswith(DIGEST)
    assert "control-secret" not in str(result)


@pytest.mark.parametrize(
    "action,missing,bad_database,success",
    [
        ("preflight", None, False, True),
        ("deploy", "CLOUDRU_IAM_KEY_SECRET", False, False),
        ("preflight", "ALICE_DATABASE_URL", False, False),
        ("deploy", "ALICE_GITHUB_CLIENT_SECRET", False, False),
        ("preflight", None, True, False),
        ("status", "ALICE_DATABASE_URL", False, True),
    ],
)
def test_workflow_preflight_reports_configuration_without_secret_values(
    action, missing, bad_database, success
):
    from pathlib import Path
    import yaml

    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/cloudru-deploy.yml").read_text()
    )
    step = next(
        s for s in workflow["jobs"]["cloudru"]["steps"] if s["name"] == "Validate configuration"
    )
    env = {name: "private-test-value" for name in step["env"]}
    env.update(
        ACTION=action, CLOUDRU_PROJECT_ID="project", ALICE_DATABASE_URL="postgresql://test/db"
    )
    if missing:
        env.pop(missing)
    if bad_database:
        env["ALICE_DATABASE_URL"] = "sqlite:///test"
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]], env=env, text=True, capture_output=True
    )
    assert (result.returncode == 0) is success
    assert "private-test-value" not in result.stdout + result.stderr
    if missing and not success:
        assert missing in result.stdout


def test_deploy_verified_restores_config_even_with_same_image():
    previous = _app(image="same")
    apps = _verified_client(
        [previous, previous, previous, {}, _app(image="same"), {}, _app(image="same")],
        http_status=503,
    )
    with pytest.raises(CloudProviderError, match="rolled back"):
        apps.deploy_verified(ContainerSpec(name="alice-pro", image="same", cpu="1"))
    patches = [c for c in apps.client.calls if c[1] == "PATCH"]
    assert len(patches) == 2
    restored = patches[1][4]
    assert patches[1][3] is None
    assert restored["projectId"] == "p1"
    assert restored["name"] == previous["name"]
    assert restored["configuration"]["ingress"]["publiclyAccessible"] is True
    assert "publicUri" not in restored["configuration"]["ingress"]
    assert restored["template"] == previous["template"]


def test_deploy_verified_without_previous_image_just_raises():
    apps = _verified_client(
        [
            CloudProviderError("nf", http_status=404),
            CloudProviderError("nf", http_status=404),
            {},
            _app("FAILED", "new"),
        ]
    )
    with pytest.raises(CloudProviderError, match="FAILED"):
        apps.deploy_verified(ContainerSpec(name="alice-pro", image="new"))


def test_provider_advertises_deploy_services(monkeypatch):
    from cloud.cloudru.provider import CloudRuProvider

    for name in ("CLOUDRU_PROJECT_ID", "CLOUDRU_IAM_KEY_ID", "CLOUDRU_IAM_KEY_SECRET"):
        monkeypatch.setenv(name, "x")
    services = CloudRuProvider(client=RecordingClient()).capabilities()["services"]
    assert services["container_apps"]["enabled"] is True
    assert "deploy" in services["container_apps"]["operations"]
    monkeypatch.delenv("CLOUDRU_PROJECT_ID")
    assert (
        CloudRuProvider(client=RecordingClient()).capabilities()["services"]["artifact_registry"][
            "enabled"
        ]
        is False
    )
