from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cli_adapter_uses_canonical_backend_runtime():
    source = (ROOT / "cli_agent.py").read_text(encoding="utf-8")

    assert "/api/chat" in source
    assert "/api/conversations" in source
    assert "/api/mcp/execute-approved" in source
    assert "foundationModels/v1/completion" not in source
    assert "YANDEX_API_KEY" not in source
    assert "YANDEX_PROJECT_ID" not in source
    assert "subprocess" not in source


def test_pruned_agent_runner_stays_deleted():
    assert not (ROOT / "agent_runner.py").exists()


def test_github_agent_runs_as_packaged_module():
    workflow = (ROOT / ".github" / "workflows" / "alice.yml").read_text(encoding="utf-8")

    assert "python -m agents.github_runner" in workflow
    assert "python alice_agent_runner.py" not in workflow
