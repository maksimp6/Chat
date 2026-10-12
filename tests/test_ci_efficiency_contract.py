from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_does_not_rerun_frontend_tests_only_for_logging() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "Save frontend test log" not in workflow
    assert "frontend-test.log" in workflow
    assert "tee -a frontend-test.log" in workflow


def test_full_python_suites_report_slowest_tests() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    application_step = workflow.split("- name: Run Python application tests with coverage", 1)[
        1
    ].split("- name: Generate validation report", 1)[0]

    for argument in (
        "pytest",
        "--durations=30",
        "--cov=.",
        "--cov-config=.coveragerc",
        "--cov-branch",
        "--cov-report=xml:coverage.xml",
        "--cov-report=json:coverage.json",
    ):
        assert argument in application_step
    assert "-n auto" in application_step
    assert "--dist=loadfile" in application_step
    assert "Parallel Python application tests:" in application_step



def test_focused_python_suites_are_not_duplicated_before_full_suite() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "Run runtime dispatcher regression tests" not in workflow
    assert "pytest -q tests/test_frontend_policy.py" not in workflow
    assert "pytest -q tests/test_unified_buttons_contract.py" not in workflow
    assert "pytest -q tests/test_real_ui_contract.py" not in workflow
    assert "pytest -q tests/test_frontend_module_code.py" not in workflow


