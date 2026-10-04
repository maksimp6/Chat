from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRE_PUSH = ROOT / "scripts" / "pre_push.sh"


def test_pre_push_gate_is_fast_formatting_only() -> None:
    script = PRE_PUSH.read_text(encoding="utf-8")

    stages = [
        "bash scripts/format.sh write",
        "bash scripts/format.sh check",
        "git diff --check",
        "git diff --quiet",
    ]
    positions = [script.index(stage) for stage in stages]
    assert positions == sorted(positions)
    assert "pytest" not in script
    assert "compileall" not in script
    assert "validate_frontend_modules.py" not in script
    assert "validate_agent_skills.py" not in script
    assert "exit 2" in script
    assert "pre-push formatting gate passed" in script
