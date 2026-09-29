import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.conftest import _make_lazy_postgres_reset_connector


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
    assert "_make_lazy_postgres_reset_connector" in conftest
    assert "conn = db.get_conn()" not in conftest


def test_postgres_lazy_reset_runs_once_under_concurrent_first_use() -> None:
    barrier = threading.Barrier(2)
    created_connections = []
    created_lock = threading.Lock()

    class FakeResult:
        def fetchall(self):
            return [{"tablename": "invocations"}]

    class FakeConnection:
        def __init__(self):
            self.catalog_queries = 0
            self.truncates = 0
            self.commits = 0
            self.closed = False

        def execute(self, query):
            if "FROM pg_catalog.pg_tables" in query:
                self.catalog_queries += 1
                return FakeResult()
            if query.startswith("TRUNCATE TABLE"):
                self.truncates += 1
                return FakeResult()
            raise AssertionError(f"unexpected query: {query}")

        def commit(self):
            self.commits += 1

        def close(self):
            self.closed = True

    def connect_postgres(_url=None):
        connection = FakeConnection()
        with created_lock:
            created_connections.append(connection)
        barrier.wait(timeout=5)
        return connection

    connect = _make_lazy_postgres_reset_connector(connect_postgres)

    with ThreadPoolExecutor(max_workers=2) as pool:
        connections = list(pool.map(lambda _: connect(), range(2)))

    assert len(connections) == 2
    assert connections[0] is not connections[1]
    assert sum(conn.catalog_queries for conn in created_connections) == 1
    assert sum(conn.truncates for conn in created_connections) == 1
    assert sum(conn.commits for conn in created_connections) == 1
    assert not any(conn.closed for conn in created_connections)
