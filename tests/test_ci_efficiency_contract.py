import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.conftest import _make_lazy_postgres_connector


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

    assert "_make_lazy_postgres_connector" in conftest
    assert 'monkeypatch.setattr(db, "connect_postgres", connect_postgres_for_test)' in conftest
    assert "conn = db.get_conn()" not in conftest


def test_lazy_postgres_reset_runs_once_under_concurrent_first_use() -> None:
    start_barrier = threading.Barrier(2)
    events = []
    events_lock = threading.Lock()
    connection_number = 0

    class FakeRows:
        def fetchall(self):
            return [{"tablename": "example"}]

    class FakeConnection:
        def __init__(self, name):
            self.name = name

        def execute(self, sql):
            kind = "catalog" if "pg_catalog.pg_tables" in sql else "truncate"
            with events_lock:
                events.append((self.name, kind))
            return FakeRows()

        def commit(self):
            with events_lock:
                events.append((self.name, "commit"))

        def close(self):
            with events_lock:
                events.append((self.name, "close"))

    def connect_postgres(_url=None):
        nonlocal connection_number
        with events_lock:
            connection_number += 1
            name = f"conn-{connection_number}"
        start_barrier.wait(timeout=5)
        return FakeConnection(name)

    connect = _make_lazy_postgres_connector(connect_postgres)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: connect(), range(2)))

    assert len(results) == 2
    assert sum(kind == "catalog" for _, kind in events) == 1
    assert sum(kind == "truncate" for _, kind in events) == 1
    assert sum(kind == "commit" for _, kind in events) == 1
    assert sum(kind == "close" for _, kind in events) == 0
