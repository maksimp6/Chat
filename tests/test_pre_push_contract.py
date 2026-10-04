from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRE_PUSH = ROOT / "scripts" / "pre_push.sh"


def test_pre_push_gate_formats_before_validation_and_publication() -> None:
    script = PRE_PUSH.read_text(encoding="utf-8")

    stages = [
        "bash scripts/format.sh write",
        "bash scripts/format.sh check",
        "python -m compileall -q .",
        "python scripts/validate_frontend_modules.py",
        "python scripts/validate_agent_skills.py",
        "pytest -q",
        "git diff --check",
        "git diff --quiet",
    ]
    positions = [script.index(stage) for stage in stages]
    assert positions == sorted(positions)
    assert "exit 2" in script
    assert "pre-push gate passed" in script


def test_pre_push_full_mode_runs_full_regression_suite() -> None:
    script = PRE_PUSH.read_text(encoding="utf-8")

    assert 'if [[ "$mode" == "full" ]]' in script
    assert "pytest --durations=30 -q" in script
