"""Cloud Chrome deployment gates, isolated ownership, and rollback contracts."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cloud.base import CloudProviderError
from scripts import cloudru_chrome as chrome
from storage import StorageObjectNotFound

PROJECT = "22706bfa-6066-4047-90d9-9ad15f242b1f"
IDENTIFIER = "1440c7da-b147-4ed1-87fb-f0bdf2ec5b77"
SHA = "a" * 40
IMAGE = "alice-chrome-browser.cr.cloud.ru/chrome-worker@sha256:" + "b" * 64
OLD_IMAGE = "alice-chrome-browser.cr.cloud.ru/chrome-worker@sha256:" + "c" * 64
ORIGIN = "https://chrome-test.containerapps.ru"
MCP_ORIGIN = ORIGIN


def environment(**overrides):
    return chrome.runtime_env(
        SHA,
        {
            "ALICE_SHORT_TOKEN": "test-private-token",
            "ALICE_GITHUB_CLIENT_ID": "test-client",
            "ALICE_GITHUB_CLIENT_SECRET": "test-client-secret",
            "BROWSER_PUBLIC_URL": MCP_ORIGIN,
            **overrides,
        },
    )


def record(image=IMAGE, **overrides):
    value = chrome.creation_body(PROJECT, image, environment())
    value.update({"id": IDENTIFIER, "status": "running", **overrides})
    value["configuration"]["ingress"]["publicUri"] = ORIGIN
    return value


def response(value, status=200):
    return SimpleNamespace(
        status_code=status, content=json.dumps(value).encode(), json=lambda: value
    )


def test_runtime_passes_only_browser_secrets_and_local_profile_paths():
    values = environment(CLOUDRU_IAM_KEY_SECRET="never-forward", ALICE_DATABASE_URL="never-forward")
    assert values["BROWSER_API_TOKEN"] == "test-private-token"
    assert values["BROWSER_OAUTH_STATE_FILE"] == "/tmp/chrome-auth/oauth.json"
    assert values["CHROME_PROFILE_DIR"] == "/tmp/chrome-profile"
    assert values["CHROME_STATE_DIR"] == "/chrome-state"
    assert "never-forward" not in json.dumps(values)
    assert environment(BROWSER_API_TOKEN="dedicated")["BROWSER_API_TOKEN"] == "dedicated"


def test_separate_singleton_worker_and_state_do_not_replace_rdc():
    payload = record()
    assert payload["name"] == "chrome-22706bfa6066"
    assert payload["template"]["scaling"] == {"minInstanceCount": 1, "maxInstanceCount": 1}
    assert payload["template"]["containers"][0]["resources"] == {"cpu": "1", "memory": "4096Mi"}
    assert (
        payload["template"]["volumes"][0]["volumeAttributes"]["bucketName"]
        == "alice-chrome-state-22706bfa6066"
    )
    assert payload["configuration"]["ingress"]["accessSettings"]["enableAuth"] is False
    assert "rdc-" not in json.dumps(payload)


@pytest.mark.parametrize(
    "modify",
    [
        lambda p: p.update(description="unrelated"),
        lambda p: p.update(projectId=IDENTIFIER),
        lambda p: p["template"]["scaling"].update(maxInstanceCount=2),
        lambda p: p["template"]["volumes"][0]["volumeAttributes"].update(bucketName="other-data"),
        lambda p: p["template"]["containers"][0]["volumeMounts"][0].update(mountPath="/profile"),
        lambda p: p["template"]["containers"][0]["env"].append(
            {"name": "CLOUDRU_IAM_KEY_SECRET", "value": "private"}
        ),
    ],
)
def test_foreign_or_unsafe_existing_container_is_never_mutated(modify):
    value = record()
    modify(value)
    apps = SimpleNamespace(project_id=PROJECT, list=Mock(return_value=[value]), stop=Mock())
    with pytest.raises(CloudProviderError, match="could not be verified"):
        chrome.owned_record(apps)
    apps.stop.assert_not_called()


@pytest.mark.parametrize(
    "target",
    [
        "http://chrome-test.containerapps.ru",
        "https://evil.example",
        "https://chrome-22706bfa6066.maxxxpavlov.online",
        "https://gateway.apigw.cloud.ru",
        "https://user:secret@chrome-test.containerapps.ru",
        "https://chrome-test.containerapps.ru?secret=x",
    ],
)
def test_public_mcp_origin_is_only_the_https_container_origin(target):
    with pytest.raises(CloudProviderError):
        environment(BROWSER_PUBLIC_URL=target)


def test_private_worker_check_uses_authenticated_control_plane_and_only_safe_paths():
    request = Mock(return_value={"statusCode": 200, "body": json.dumps({"state": "awake"})})
    apps = SimpleNamespace(project_id=PROJECT, client=SimpleNamespace(request=request))
    chrome.request_worker(apps, record(), "private", "/browser/v1/status")
    assert request.call_args.args == (
        "container_apps",
        "POST",
        "/v2/containers/chrome-22706bfa6066:testCall",
    )
    assert request.call_args.kwargs["json_body"]["headers"] == {"Authorization": "Bearer private"}
    with pytest.raises(CloudProviderError):
        chrome.request_worker(apps, record(), "private", "/profile")
    assert request.call_count == 1


def test_old_revision_or_missing_oauth_never_passes_readiness(monkeypatch):
    values = environment()
    payload = {"status": "ok", "state_ready": True, "deployment_sha": SHA, "oauth_ready": True}
    request = Mock(return_value=payload)
    monkeypatch.setattr(chrome, "request_worker", request)
    chrome.verify_health(Mock(), record(), values)
    for changes in ({"deployment_sha": "d" * 40}, {"oauth_ready": False}, {"state_ready": False}):
        request.return_value = {**payload, **changes}
        with pytest.raises(CloudProviderError):
            chrome.verify_health(Mock(), record(), values)


def test_sleep_without_verified_snapshot_cannot_precede_restart(monkeypatch):
    monkeypatch.setattr(chrome, "request_worker", Mock(return_value={"state": "sleeping"}))
    with pytest.raises(CloudProviderError) as error:
        chrome.checkpoint(Mock(), record())
    assert error.value.code == "chrome_checkpoint_failed"
    chrome.request_worker.return_value["profile"] = {
        "enabled": True,
        "generation": 7,
        "restored": True,
    }
    assert chrome.checkpoint(Mock(), record()) == 7


def test_summary_contains_endpoint_digest_and_no_private_values():
    value = chrome.summary(record())
    assert value["mcp_url"] == MCP_ORIGIN + "/browser/v1/mcp"
    assert value["provider_url"] == ORIGIN
    assert value["digest"] == "sha256:" + "b" * 64
    assert "gateway_deployed" not in value
    assert value["ingress"] == "public_worker_auth"
    assert "test-private-token" not in json.dumps(value)
    assert "test-client-secret" not in json.dumps(value)


def test_failed_update_restores_previous_full_config_after_stopping_new_revision(monkeypatch):
    old = record(OLD_IMAGE)
    new = record()
    calls = []
    apps = SimpleNamespace(
        project_id=PROJECT,
        restore=Mock(side_effect=lambda *args: calls.append(("restore", args[1]))),
        start=Mock(side_effect=lambda *args: calls.append(("start", args[0]))),
    )
    monkeypatch.setattr(chrome, "owned_record", Mock(side_effect=[old, new]))
    monkeypatch.setattr(chrome, "prepare_bucket", Mock())
    monkeypatch.setattr(
        chrome, "checkpoint", Mock(side_effect=lambda *args: calls.append(("checkpoint", None)))
    )
    monkeypatch.setattr(
        chrome,
        "stop_owned",
        Mock(
            side_effect=lambda *args: calls.append(
                ("stop", args[1]["template"]["containers"][0]["image"])
            )
        ),
    )
    monkeypatch.setattr(
        chrome,
        "wait_ready",
        Mock(side_effect=[CloudProviderError("failed", code="chrome_readiness_timeout"), old]),
    )
    with pytest.raises(CloudProviderError) as error:
        chrome.deploy(apps, Mock(), {}, IMAGE, environment())
    assert error.value.code == "chrome_readiness_timeout"
    assert [item[0] for item in calls] == [
        "checkpoint",
        "stop",
        "restore",
        "start",
        "stop",
        "restore",
        "start",
    ]
    assert calls[4][1] == IMAGE
    assert calls[5][1] == old


def test_failed_first_install_suspends_only_new_container_and_preserves_bucket(monkeypatch):
    new = record()
    apps = SimpleNamespace(
        project_id=PROJECT,
        client=SimpleNamespace(request=Mock(return_value={"resourceId": IDENTIFIER})),
    )
    monkeypatch.setattr(chrome, "owned_record", Mock(side_effect=[None, new]))
    monkeypatch.setattr(chrome, "prepare_bucket", Mock())
    monkeypatch.setattr(
        chrome,
        "wait_ready",
        Mock(side_effect=CloudProviderError("failed", code="chrome_readiness_timeout")),
    )
    monkeypatch.setattr(chrome, "stop_owned", Mock())
    store = Mock()
    with pytest.raises(CloudProviderError):
        chrome.deploy(apps, store, {}, IMAGE, environment())
    chrome.stop_owned.assert_called_once_with(apps, new)
    store.delete.assert_not_called()


def test_first_install_binds_oauth_to_the_same_single_container(monkeypatch):
    current = record()
    apps = SimpleNamespace(
        project_id=PROJECT,
        client=SimpleNamespace(request=Mock(return_value={"resourceId": IDENTIFIER})),
        restore=Mock(),
        start=Mock(),
    )
    monkeypatch.setattr(chrome, "owned_record", Mock(return_value=None))
    monkeypatch.setattr(chrome, "prepare_bucket", Mock())
    monkeypatch.setattr(chrome, "wait_ready", Mock(return_value=current))
    monkeypatch.setattr(chrome, "request_worker", Mock(return_value={"state": "awake"}))
    monkeypatch.setattr(chrome, "checkpoint", Mock(return_value=7))
    monkeypatch.setattr(chrome, "stop_owned", Mock())
    monkeypatch.setattr(chrome, "verify_restored", Mock())
    initial = {key: value for key, value in environment().items() if key != "BROWSER_PUBLIC_URL"}
    result = chrome.deploy(apps, Mock(), {}, IMAGE, initial)
    # Exactly one creation call; OAuth is bound later on that same container.
    apps.client.request.assert_called_once()
    assert apps.client.request.call_args.args == ("container_apps", "POST", "/v2/containers")
    payload = apps.client.request.call_args.kwargs["json_body"]
    assert payload["configuration"]["ingress"]["accessSettings"] == {"enableAuth": False}
    created = {item["name"] for item in payload["template"]["containers"][0]["env"]}
    assert "BROWSER_PUBLIC_URL" not in created
    apps.restore.assert_called_once()
    assert apps.restore.call_args.args[0] == current["name"]
    bound = apps.restore.call_args.args[1]["template"]["containers"][0]["env"]
    assert {"name": "BROWSER_PUBLIC_URL", "value": ORIGIN} in bound
    chrome.checkpoint.assert_called_once_with(apps, current)
    apps.start.assert_called_once_with(current["name"])
    chrome.verify_restored.assert_called_once_with(apps, current, 7)
    assert result["mcp_url"] == ORIGIN + "/browser/v1/mcp"


def test_update_reuses_existing_container_and_never_creates_another(monkeypatch):
    old = record(OLD_IMAGE)
    apps = SimpleNamespace(
        project_id=PROJECT, client=SimpleNamespace(request=Mock()), restore=Mock(), start=Mock()
    )
    monkeypatch.setattr(chrome, "owned_record", Mock(return_value=old))
    monkeypatch.setattr(chrome, "prepare_bucket", Mock())
    monkeypatch.setattr(chrome, "checkpoint", Mock(return_value=7))
    monkeypatch.setattr(chrome, "stop_owned", Mock())
    monkeypatch.setattr(chrome, "wait_ready", Mock(return_value=record()))
    monkeypatch.setattr(chrome, "verify_restored", Mock())
    initial = {key: value for key, value in environment().items() if key != "BROWSER_PUBLIC_URL"}
    chrome.deploy(apps, Mock(), {}, IMAGE, initial)
    apps.client.request.assert_not_called()
    apps.restore.assert_called_once()
    env = apps.restore.call_args.args[1]["template"]["containers"][0]["env"]
    assert {"name": "BROWSER_PUBLIC_URL", "value": ORIGIN} in env


def test_duplicate_named_containers_stop_before_any_mutation():
    apps = SimpleNamespace(project_id=PROJECT, list=Mock(return_value=[record(), record()]))
    with pytest.raises(CloudProviderError):
        chrome.owned_record(apps)


@pytest.mark.parametrize(
    "health, status, mcp",
    [
        (response({"status": "ok"}), response({}, 200), response({}, 401)),
        (response({"status": "ok"}), response({}, 401), response({}, 200)),
        (response({"status": "ok"}), response({}, 302), response({}, 401)),
        (response({}, 403), response({}, 401), response({}, 401)),
    ],
)
def test_public_ingress_requires_reachable_worker_that_refuses_anonymous_access(
    health, status, mcp
):
    with pytest.raises(CloudProviderError) as error:
        chrome.verify_ingress(record(), http_get=Mock(side_effect=[health, status, mcp]))
    assert error.value.code == "chrome_ingress_unconfirmed"


def test_public_ingress_check_sends_no_credentials_and_follows_no_redirects():
    http_get = Mock(side_effect=[response({"status": "ok"}), response({}, 401), response({}, 401)])
    chrome.verify_ingress(record(), http_get=http_get)
    assert [call.args[0] for call in http_get.call_args_list] == [
        ORIGIN + "/healthz",
        ORIGIN + "/browser/v1/status",
        ORIGIN + "/browser/v1/mcp",
    ]
    for call in http_get.call_args_list:
        assert call.kwargs == {"timeout": 10, "allow_redirects": False}


def test_deployed_origin_is_exported_for_live_acceptance(tmp_path):
    target = tmp_path / "env"
    chrome.export_public_url(ORIGIN, {"GITHUB_ENV": str(target)})
    assert target.read_text() == "BROWSER_PUBLIC_URL=" + ORIGIN + "\n"
    with pytest.raises(CloudProviderError):
        chrome.export_public_url("https://evil.example", {"GITHUB_ENV": str(target)})


def test_bucket_with_unknown_owner_is_not_adopted(monkeypatch):
    store = Mock()
    store.download.return_value = json.dumps({"purpose": "someone-else"}).encode()
    monkeypatch.setattr(chrome, "verify_private_acl", Mock())
    with pytest.raises(CloudProviderError) as error:
        chrome.prepare_bucket(store, {}, PROJECT)
    assert error.value.code == "chrome_bucket_ownership_unconfirmed"
    store.upload.assert_not_called()
    assert [call.args[0] for call in store._request.call_args_list] == ["HEAD"]


def test_missing_bucket_preflight_is_read_only():
    store = Mock()
    store._request.side_effect = StorageObjectNotFound("missing")
    assert chrome.bucket_exists(store, {}, PROJECT) is False
    store.upload.assert_not_called()
    store._request.assert_called_once_with("HEAD", credentials={})


def test_unreviewed_commit_rejected_before_cloud_calls(monkeypatch, tmp_path):
    execute = Mock(side_effect=[SimpleNamespace(returncode=0), SimpleNamespace(returncode=1)])
    monkeypatch.setattr(chrome.subprocess, "run", execute)
    with pytest.raises(CloudProviderError):
        chrome.require_reviewed_head(tmp_path, SHA)
    assert execute.call_args_list[0].args[0] == ["git", "fetch", "--no-tags", "origin", "master"]
    assert execute.call_args_list[1].args[0] == [
        "git",
        "merge-base",
        "--is-ancestor",
        SHA,
        "FETCH_HEAD",
    ]


def test_cli_never_prints_provider_error_or_token(monkeypatch, capsys):
    monkeypatch.setattr(
        chrome, "main", Mock(side_effect=RuntimeError("private-token-from-provider"))
    )
    assert chrome.cli() == 1
    assert json.loads(capsys.readouterr().out) == {
        "error": "chrome_operation_failed",
        "http_status": None,
    }


def test_workflow_uses_reviewed_master_production_and_fixed_credentials():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github/workflows/cloudru-chrome.yml"
    ).read_text()
    assert "github.ref == 'refs/heads/master'" in workflow
    assert "git merge-base --is-ancestor HEAD FETCH_HEAD" in workflow
    assert "environment: production" in workflow
    assert "secrets.BROWSER_API_TOKEN || secrets.ALICE_SHORT_TOKEN" in workflow
    assert "secrets.BROWSER_GITHUB_CLIENT_ID || secrets.ALICE_GITHUB_CLIENT_ID" in workflow
    assert "secrets.BROWSER_GITHUB_CLIENT_SECRET || secrets.ALICE_GITHUB_CLIENT_SECRET" in workflow
    assert "CLOUDRU_STORAGE_TENANT_ID" in workflow
    assert 'python scripts/cloudru_chrome.py "$ACTION" --sha "$SOURCE_SHA"' in workflow
    assert "ALICE_DATABASE_URL" not in workflow
    seed = workflow.index("node deploy/chrome-worker/live-smoke.mjs seed")
    restart = workflow.index('python scripts/cloudru_chrome.py restart --sha "$SOURCE_SHA"')
    verify = workflow.index("node deploy/chrome-worker/live-smoke.mjs verify")
    assert seed < restart < verify
    assert 'node-version: "22.22.2"' in workflow


def test_workflow_runs_only_on_manual_dispatch_one_at_a_time():
    import yaml

    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/cloudru-chrome.yml").read_text()
    )
    # PyYAML reads the bare `on` key as boolean True.
    assert set(workflow[True]) == {"workflow_dispatch"}
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert workflow["jobs"]["chrome"]["timeout-minutes"] <= 35
    assert "public_url" not in workflow[True]["workflow_dispatch"]["inputs"]
    text = yaml.safe_dump(workflow)
    assert "maxxxpavlov" not in text and "Gateway" not in text


@pytest.mark.parametrize("action", ["preflight", "deploy", "status", "restart", "stop"])
def test_cli_actions_respect_read_only_and_checkpoint_boundaries(monkeypatch, capsys, action):
    for key, value in {
        "CLOUDRU_PROJECT_ID": PROJECT,
        "CLOUDRU_IAM_KEY_ID": "test-key-id",
        "CLOUDRU_IAM_KEY_SECRET": "test-key-secret",
        "CLOUDRU_STORAGE_TENANT_ID": IDENTIFIER,
        "ALICE_SHORT_TOKEN": "test-private-token",
        "ALICE_GITHUB_CLIENT_ID": "test-client",
        "ALICE_GITHUB_CLIENT_SECRET": "test-client-secret",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("BROWSER_PUBLIC_URL", raising=False)
    apps = SimpleNamespace(project_id=PROJECT, start=Mock())
    for name, value in {
        "require_reviewed_head": Mock(),
        "_load_cloudru_credentials": Mock(),
        "CloudRuContainerAppsClient": Mock(return_value=apps),
        "owned_record": Mock(return_value=record()),
        "storage_client": Mock(return_value=(Mock(), {})),
        "bucket_exists": Mock(return_value=True),
        "build_image": Mock(return_value=IMAGE),
        "deploy": Mock(
            return_value={
                "status": "CHROME_PRIVATE_RUNNING",
                "container_id": IDENTIFIER,
                "provider_url": ORIGIN,
            }
        ),
        "export_public_url": Mock(),
        "checkpoint": Mock(return_value=7),
        "stop_owned": Mock(),
        "wait_ready": Mock(return_value=record()),
        "verify_restored": Mock(),
    }.items():
        monkeypatch.setattr(chrome, name, value)
    chrome.main([action, "--sha", SHA])
    output = json.loads(capsys.readouterr().out)
    assert output["status"] in {"PREFLIGHT_PASSED", "CHROME_PRIVATE_RUNNING"}
    if action in {"preflight", "status"}:
        chrome.build_image.assert_not_called()
        chrome.deploy.assert_not_called()
        chrome.stop_owned.assert_not_called()
        apps.start.assert_not_called()
        chrome.export_public_url.assert_not_called()
    if action == "deploy":
        assert "BROWSER_PUBLIC_URL" not in chrome.deploy.call_args.args[-1]
        chrome.export_public_url.assert_called_once_with(ORIGIN)
    if action in {"restart", "stop"}:
        chrome.checkpoint.assert_called_once()
        chrome.stop_owned.assert_called_once()
    if action == "restart":
        chrome.verify_restored.assert_called_once_with(apps, record(), 7)


class Clock:
    def __init__(self):
        self.value = 0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def test_readiness_retries_startup_but_never_accepts_wrong_image(monkeypatch):
    clock = Clock()
    apps = SimpleNamespace(project_id=PROJECT)
    monkeypatch.setattr(chrome, "owned_record", Mock(return_value=record()))
    monkeypatch.setattr(
        chrome, "verify_health", Mock(side_effect=[CloudProviderError("starting"), None])
    )
    monkeypatch.setattr(
        chrome,
        "request_worker",
        Mock(return_value={"state": "sleeping", "profile": {"enabled": True}}),
    )
    monkeypatch.setattr(chrome, "verify_ingress", Mock())
    assert (
        chrome.wait_ready(apps, image=IMAGE, timeout=6, sleep=clock.sleep, clock=clock.now)
        == record()
    )
    assert clock.value == 2
    with pytest.raises(CloudProviderError) as error:
        chrome.wait_ready(apps, image=OLD_IMAGE, timeout=4, sleep=clock.sleep, clock=clock.now)
    assert error.value.code == "chrome_readiness_timeout"


def test_stop_waits_for_suspension_before_returning(monkeypatch):
    clock = Clock()
    apps = SimpleNamespace(stop=Mock())
    stopped = record(status="suspended")
    monkeypatch.setattr(chrome, "owned_record", Mock(side_effect=[record(), record(), stopped]))
    assert chrome.stop_owned(apps, record(), sleep=clock.sleep, clock=clock.now) == stopped
    apps.stop.assert_called_once_with("chrome-22706bfa6066")
    assert clock.value == 2


def test_stop_timeout_is_reported_without_deleting_state(monkeypatch):
    clock = Clock()
    apps = SimpleNamespace(stop=Mock(), delete=Mock())
    monkeypatch.setattr(chrome, "owned_record", Mock(return_value=record()))
    with pytest.raises(CloudProviderError) as error:
        chrome.stop_owned(apps, record(), timeout=4, sleep=clock.sleep, clock=clock.now)
    assert error.value.code == "chrome_stop_timeout"
    apps.delete.assert_not_called()


@pytest.mark.parametrize(
    "profile",
    [
        {"restored": False, "generation": 7},
        {"restored": True, "generation": 6},
        {"restored": True, "generation": "7"},
    ],
)
def test_profile_restart_requires_restored_matching_generation(monkeypatch, profile):
    monkeypatch.setattr(chrome, "request_worker", Mock(return_value={"profile": profile}))
    with pytest.raises(CloudProviderError) as error:
        chrome.verify_restored(Mock(), record(), 7)
    assert error.value.code == "chrome_checkpoint_failed"
    chrome.request_worker.return_value = {"profile": {"restored": True, "generation": 7}}
    chrome.verify_restored(Mock(), record(), 7)


def test_registry_reuses_only_expected_private_docker_registry(monkeypatch):
    value = {"id": IDENTIFIER, "name": chrome.REGISTRY, "status": "ACTIVE"}
    registry = SimpleNamespace(
        project_id=PROJECT, client=SimpleNamespace(request=Mock(return_value=value))
    )
    monkeypatch.setattr(chrome, "registry_inventory", Mock(return_value=[value]))
    chrome.prepare_registry(registry)
    assert registry.client.request.call_args.args[:2] == ("artifact_registry", "GET")
    value["isPublic"] = True
    with pytest.raises(CloudProviderError):
        chrome.prepare_registry(registry)


def test_build_exports_only_reviewed_commit_and_returns_digest(monkeypatch, tmp_path):
    registry = SimpleNamespace(build_and_push=Mock(return_value=SimpleNamespace(pinned=IMAGE)))
    monkeypatch.setattr(chrome, "CloudRuRegistryClient", Mock(return_value=registry))
    monkeypatch.setattr(chrome, "prepare_registry", Mock())
    monkeypatch.setattr(chrome, "_export_commit", Mock())
    assert chrome.build_image(tmp_path, SHA) == IMAGE
    assert chrome._export_commit.call_args.args[:2] == (SHA, str(tmp_path))
    build = registry.build_and_push.call_args.kwargs
    assert build["context_dir"].endswith("/deploy/chrome-worker")
    assert build["dockerfile"].endswith("/deploy/chrome-worker/Dockerfile")
    assert build["tag"] == SHA
    assert build["registry_name"] == chrome.REGISTRY


def test_fresh_private_bucket_is_marked_and_reverified(monkeypatch):
    store = Mock()
    store._request.side_effect = [StorageObjectNotFound("missing"), None, None]
    store.download.return_value = json.dumps(chrome.owner_marker(PROJECT)).encode()
    monkeypatch.setattr(chrome, "verify_private_acl", Mock())
    chrome.prepare_bucket(store, {}, PROJECT)
    assert [call.args[0] for call in store._request.call_args_list] == ["HEAD", "PUT", "HEAD"]
    assert json.loads(store.upload.call_args.args[1]) == chrome.owner_marker(PROJECT)


def test_ambiguous_create_failure_never_reports_successful_rollback(monkeypatch):
    apps = SimpleNamespace(
        project_id=PROJECT,
        client=SimpleNamespace(request=Mock(side_effect=CloudProviderError("timeout"))),
    )
    monkeypatch.setattr(chrome, "owned_record", Mock(return_value=None))
    monkeypatch.setattr(chrome, "prepare_bucket", Mock())
    with pytest.raises(CloudProviderError) as error:
        chrome.deploy(apps, Mock(), {}, IMAGE, environment())
    assert error.value.code == "chrome_rollback_failed"
