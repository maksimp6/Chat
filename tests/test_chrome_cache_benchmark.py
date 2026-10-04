import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cloud.base import CloudProviderError
from scripts import benchmark_chrome_cache as probe


def source(root):
    context = root / "deploy/chrome-worker"
    context.mkdir(parents=True)
    (context / "Dockerfile").write_text("RUN apt-get install ca-certificates curl gnupg\n")
    (context / "server.mjs").write_text("console.log(1);")
    (root / "scripts").mkdir()
    (root / "scripts/cloudru_chrome.py").write_text("print(1)")
    return context


@pytest.mark.parametrize("scenario", probe.SCENARIOS)
def test_chrome_cache_scenarios_change_only_the_intended_input(tmp_path, scenario):
    context = source(tmp_path)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert probe.mutate_context(tmp_path, scenario) == context
    changed = [str(p) for p, contents in before.items() if (tmp_path / p).read_bytes() != contents]
    assert (
        changed
        == {
            "cold": [],
            "warm": [],
            "python": ["scripts/cloudru_chrome.py"],
            "worker-js": ["deploy/chrome-worker/server.mjs"],
            "dependencies": ["deploy/chrome-worker/Dockerfile"],
        }[scenario]
    )


def test_docker_probe_preserves_inline_cache_and_secret_stdin(monkeypatch):
    runner = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(probe.subprocess, "run", runner)
    probe.docker_runner(
        [
            "docker",
            "build",
            "--cache-from",
            "registry/repo:buildcache",
            "--build-arg",
            "BUILDKIT_INLINE_CACHE=1",
            ".",
        ],
        cold=True,
        capture_output=True,
    )
    args = runner.call_args.args[0]
    assert "--no-cache" in args and "--progress=plain" in args
    assert "registry/repo:buildcache" in args and "BUILDKIT_INLINE_CACHE=1" in args
    probe.docker_runner(
        ["docker", "login", "registry", "--password-stdin"], cold=True, input="secret"
    )
    assert "--no-cache" not in runner.call_args.args[0]
    assert runner.call_args.kwargs["input"] == "secret"


def test_benchmark_is_test_only_and_never_deploys(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(probe.chrome, "lane", lambda: "production")
    registry = Mock()
    with pytest.raises(CloudProviderError):
        probe.benchmark(tmp_path, "a" * 40, "warm", registry)
    registry.build_and_push.assert_not_called()
    monkeypatch.setattr(probe.chrome, "lane", lambda: "test")
    reviewed = Mock()
    monkeypatch.setattr(probe.chrome, "require_reviewed_head", reviewed)
    monkeypatch.setattr(probe.chrome, "prepare_registry", Mock())
    monkeypatch.setattr(probe.chrome, "export_source", lambda sha, root, dest: source(dest))
    deploy = Mock()
    monkeypatch.setattr(probe.chrome, "deploy", deploy)
    registry.build_and_push.return_value = SimpleNamespace(digest="sha256:" + "a" * 64)
    probe.benchmark(tmp_path, "a" * 40, "warm", registry)
    reviewed.assert_called_once_with(tmp_path, "a" * 40)
    deploy.assert_not_called()
    record = json.loads(capsys.readouterr().out)
    assert record["scenario"] == "warm" and record["seconds"] >= 0
    assert registry.build_and_push.call_args.kwargs["tag"] == "benchmark-" + "a" * 40 + "-warm"


def test_dispatch_main_selects_cold_runner(monkeypatch):
    monkeypatch.setattr(probe.chrome, "_load_cloudru_credentials", Mock())
    registry = Mock()
    monkeypatch.setattr(probe, "CloudRuRegistryClient", registry)
    benchmark = Mock()
    monkeypatch.setattr(probe, "benchmark", benchmark)
    probe.main(["--sha", "a" * 40, "--scenario", "cold"])
    assert registry.call_args.kwargs["runner"].keywords == {"cold": True}
    assert benchmark.call_args.args[2] == "cold"


def test_docker_probe_timeout_emits_only_operation_and_time(monkeypatch, capsys):
    runner = Mock(side_effect=subprocess.TimeoutExpired(["docker", "login", "secret-id"], 180))
    monkeypatch.setattr(probe.subprocess, "run", runner)
    with pytest.raises(CloudProviderError) as error:
        probe.docker_runner(
            ["docker", "login", "registry", "--password-stdin"], input="secret-password"
        )
    assert error.value.code == "docker_error"
    assert runner.call_args.kwargs["timeout"] == 180
    output = capsys.readouterr().out
    records = [json.loads(line) for line in output.splitlines()]
    assert records[0] == {"stage": "chrome_docker_command_started", "operation": "login"}
    assert records[1]["stage"] == "chrome_docker_timeout"
    assert records[1]["operation"] == "login" and records[1]["seconds"] >= 0
    assert "secret" not in output
