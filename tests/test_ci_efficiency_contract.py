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

    assert "pytest --durations=30 --cov=." in workflow
    assert "pytest --durations=30 -q" in workflow


def test_focused_python_suites_are_not_duplicated_before_full_suite() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "Run runtime dispatcher regression tests" not in workflow
    assert "pytest -q tests/test_frontend_policy.py" not in workflow
    assert "pytest -q tests/test_unified_buttons_contract.py" not in workflow
    assert "pytest -q tests/test_real_ui_contract.py" not in workflow
    assert "pytest -q tests/test_frontend_module_code.py" not in workflow


def test_postgres_matrix_resets_lazily_on_first_connection() -> None:
    conftest = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")

    assert 'monkeypatch.setattr(db, "connect_postgres", connect_postgres_for_test)' in conftest
    assert "reset_done = False" in conftest
    assert "conn = db.get_conn()" not in conftest
