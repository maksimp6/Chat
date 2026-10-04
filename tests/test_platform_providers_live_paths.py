import asyncio
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from alice_platform import __main__ as cli
from alice_platform.billing import calculate_lane_cost
from alice_platform.health import HealthCheck, run_health_check
from alice_platform.providers import cloudru, secrets
from cloud.base import CloudProviderError


def test_observed_state_maps_lane_containers():
    client = MagicMock()
    client.list.return_value = [
        {
            "name": "alice-test-oauth",
            "status": "RUNNING",
            "template": {
                "containers": [{"image": "img", "resources": {"cpu": "0.1", "memory": "256Mi"}}],
                "scaling": {"max": 1},
            },
        },
        {"name": "other-app"},
    ]
    with patch.object(cloudru, "CloudRuContainerAppsClient", return_value=client):
        state = cloudru.get_observed_state("test")
    assert state == {
        "containers": [
            {
                "name": "alice-test-oauth",
                "service": "oauth",
                "lane": "test",
                "status": "RUNNING",
                "image": "img",
                "cpu": "0.1",
                "memory": "256Mi",
                "scaling": {"max": 1},
            }
        ]
    }


def test_container_lifecycle_calls_client(caplog):
    client = MagicMock()
    client.create.return_value = {"name": "alice-production-gpu-svc"}
    client.update.return_value = {"name": "alice-production-gpu-svc"}
    config = {"resources": {"cpu": "1", "gpu": "1", "scale": 1}}
    caplog.set_level("INFO")
    with patch.object(cloudru, "CloudRuContainerAppsClient", return_value=client):
        cloudru.create_container("production", "gpu_svc", config)
        cloudru.update_container("production", "gpu_svc", config)
        cloudru.delete_container("production", "gpu_svc")
    assert client.create.call_args.args[0].name == "alice-production-gpu-svc"
    client.delete.assert_called_once_with("alice-production-gpu-svc")
    assert "with GPU=1 enforcement" in caplog.text
    assert "[CREATE] gpu_svc" in caplog.text
    assert "[UPDATE] gpu_svc" in caplog.text
    assert "[DELETE] gpu_svc" in caplog.text


def _secret_client(versions, value="s3cr3t"):
    client = MagicMock()
    client.list_versions.return_value = versions
    client.get_secret_value.return_value = value
    return client


def test_get_secret_prefers_active_version():
    client = _secret_client([{"id": "v1", "status": "disabled"}, {"id": "v2", "state": "Active"}])
    with patch.object(secrets, "CloudRuSecretManagementClient", return_value=client):
        assert secrets.get_secret("alice/test/api-key") == "s3cr3t"
    client.get_secret_value.assert_called_once_with("alice-test-api-key", "v2")


def test_get_secret_falls_back_to_first_version():
    client = _secret_client([{"version_id": "v9", "status": "pending"}])
    with patch.object(secrets, "CloudRuSecretManagementClient", return_value=client):
        secrets.get_secret("alice/test/api-key")
    client.get_secret_value.assert_called_once_with("alice-test-api-key", "v9")


def test_get_secret_errors_never_include_secret_material():
    with patch.object(secrets, "CloudRuSecretManagementClient", return_value=_secret_client([])):
        with pytest.raises(CloudProviderError, match="No versions found"):
            secrets.get_secret("alice/test/api-key")

    broken = _secret_client([{"id": "v1", "status": "active"}])
    broken.get_secret_value.side_effect = RuntimeError("token=leaky")
    with patch.object(secrets, "CloudRuSecretManagementClient", return_value=broken):
        with pytest.raises(CloudProviderError) as exc:
            secrets.get_secret("alice/test/api-key")
    assert "leaky" not in str(exc.value)


def test_resolve_all_secrets_walks_nested_references():
    with patch.object(secrets, "get_secret", side_effect=lambda path: path.upper()):
        resolved = secrets.resolve_all_secrets(
            {"oauth": {"client": {"secret": "alice/prod/oauth"}, "name": "plain"}, "n": 1}
        )
    assert resolved == {"oauth.client.secret": "ALICE/PROD/OAUTH"}


def test_lane_cost_skips_unknown_services():
    config = {
        "lanes": {"test": {"services": ["ghost", "oauth"]}},
        "services": {"oauth": {"resources": {"cpu": "0.1", "memory": "256Mi"}}},
    }
    result = calculate_lane_cost("test", config)
    assert result["services"] == 1


def test_cli_billing_and_secret_commands(capsys):
    assert cli.cmd_billing(SimpleNamespace(lane="production", config_dir=None)) == 0
    assert "Billing for production" in capsys.readouterr().out

    assert cli.cmd_billing(SimpleNamespace(lane="test", config_dir="/nonexistent")) == 1
    with patch.object(cli, "calculate_lane_cost", side_effect=RuntimeError("boom")):
        assert cli.cmd_billing(SimpleNamespace(lane="test", config_dir=None)) == 1
    assert "boom" in capsys.readouterr().err

    with patch.object(cli, "get_secret", return_value="abcd"):
        assert cli.cmd_secret(SimpleNamespace(path="alice/test/x")) == 0
    out = capsys.readouterr().out
    assert "Value length: 4 chars" in out and "abcd" not in out
    with patch.object(cli, "get_secret", side_effect=CloudProviderError("nope")):
        assert cli.cmd_secret(SimpleNamespace(path="alice/test/x")) == 1


def _check():
    return HealthCheck(
        service="oauth", endpoint="https://oauth.test", expected_sign_in="github", lane="test"
    )


def _fake_playwright(goto_error=None, launch_error=None):
    page = MagicMock()

    async def goto(*_args, **_kwargs):
        if goto_error:
            raise goto_error

    page.goto = goto
    browser = MagicMock()

    async def new_page():
        return page

    async def close():
        browser.closed = True

    browser.new_page = new_page
    browser.close = close

    async def launch():
        if launch_error:
            raise launch_error
        return browser

    class _Context:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        async def __aexit__(self, *_exc):
            return False

    module = types.ModuleType("playwright.async_api")
    module.async_playwright = _Context
    return module, browser


def _run_with(module):
    package = types.ModuleType("playwright")
    with patch.dict(sys.modules, {"playwright": package, "playwright.async_api": module}):
        return asyncio.run(run_health_check(_check()))


def test_health_check_without_playwright_is_skipped():
    with patch.dict(sys.modules, {"playwright": None, "playwright.async_api": None}):
        result = asyncio.run(run_health_check(_check()))
    assert (result.status, result.error) == ("skip", "playwright not installed")


def test_health_check_ok_failed_and_error():
    module, browser = _fake_playwright()
    assert _run_with(module).status == "ok"
    assert browser.closed is True

    module, _ = _fake_playwright(goto_error=RuntimeError("404"))
    failed = _run_with(module)
    assert (failed.status, failed.error) == ("failed", "404")

    module, _ = _fake_playwright(launch_error=RuntimeError("no browser"))
    errored = _run_with(module)
    assert (errored.status, errored.error) == ("error", "no browser")
