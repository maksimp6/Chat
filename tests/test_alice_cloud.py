"""Contract tests for the Alice Cloud MVP."""

from __future__ import annotations

import json

import pytest

from cloud.alice.provider import AliceCloudProvider
from cloud.alice.runtime import CommandResult, DockerRuntime
from cloud.base import CloudProviderError
from cloud.registry import ensure_default_providers, resolve_provider_name


class RecordingRunner:
    """Deterministic Docker command stub."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.responses: list[CommandResult] = []

    def push(self, stdout: str = "", *, returncode: int = 0) -> None:
        self.responses.append(CommandResult(returncode=returncode, stdout=stdout))

    def __call__(self, argv: list[str]) -> CommandResult:
        self.calls.append(argv)
        if self.responses:
            return self.responses.pop(0)
        return CommandResult(returncode=0)


def test_alice_is_default_provider(monkeypatch):
    monkeypatch.delenv("ALICE_CLOUD_PROVIDER", raising=False)

    assert resolve_provider_name({}) == "alice"
    assert "alice" in ensure_default_providers().names()


def test_runtime_create_uses_owned_labels_and_argv_only():
    runner = RecordingRunner()
    runtime = DockerRuntime(runner=runner)

    result = runtime.create(
        lane="dev",
        service="alice",
        config={"image": "example.invalid/alice@sha256:deadbeef", "scale": 1},
    )

    assert result["state"] == "running"
    create = runner.calls[0]
    assert create[:4] == ["docker", "create", "--name", "alice-dev-alice"]
    assert "alice.cloud.managed=1" in create
    assert "alice.cloud.lane=dev" in create
    assert "alice.cloud.service=alice" in create
    assert runner.calls[1] == ["docker", "start", "alice-dev-alice"]


@pytest.mark.parametrize("scale", [-1, 2, 5])
def test_runtime_rejects_scale_outside_mvp(scale):
    runtime = DockerRuntime(runner=RecordingRunner())

    with pytest.raises(CloudProviderError, match="invalid_scale"):
        runtime.create(
            lane="dev",
            service="alice",
            config={"image": "example.invalid/alice:latest", "scale": scale},
        )


def test_delete_refuses_container_not_owned_by_alice():
    runner = RecordingRunner()
    runner.push(json.dumps({"someone.else": "1"}))
    runtime = DockerRuntime(runner=runner)

    with pytest.raises(CloudProviderError, match="resource_not_owned"):
        runtime.delete(lane="dev", service="alice")

    assert all("rm" not in call for call in runner.calls)


def test_provider_reports_self_hosted_cost_boundary():
    provider = AliceCloudProvider(runtime=DockerRuntime(runner=RecordingRunner()))

    summary = provider.costs_summary(period="month")

    assert summary["billing_model"] == "self_hosted"
    assert summary["provider_charge"] == 0.0
    assert summary["currency"] == "RUB"


def test_provider_lists_only_requested_service():
    runner = RecordingRunner()
    runner.push(
        "\n".join(
            [
                json.dumps(
                    {
                        "ID": "a1",
                        "Names": "alice-dev-api",
                        "Image": "api:1",
                        "State": "running",
                        "Status": "Up",
                        "Labels": "alice.cloud.managed=1,alice.cloud.lane=dev,alice.cloud.service=api",
                    }
                ),
                json.dumps(
                    {
                        "ID": "b1",
                        "Names": "alice-dev-browser",
                        "Image": "browser:1",
                        "State": "exited",
                        "Status": "Exited",
                        "Labels": "alice.cloud.managed=1,alice.cloud.lane=dev,alice.cloud.service=browser",
                    }
                ),
            ]
        )
    )
    provider = AliceCloudProvider(runtime=DockerRuntime(runner=runner))

    resources = provider.list_resources(service="api")

    assert [item["id"] for item in resources["containers"]] == ["a1"]
