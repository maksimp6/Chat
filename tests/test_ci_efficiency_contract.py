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

    assert "pytest -n auto --dist=loadfile --durations=30 --cov=." in workflow
    assert "pytest --durations=30 -q" in workflow
    assert "Parallel Python application tests:" in workflow


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
    events = []
    events_lock = threading.Lock()

    def record(event):
        with events_lock:
            events.append(event)

    class SynchronizedLock:
        def __init__(self):
            self.lock = threading.Lock()
            self.attempts = 0
            self.attempts_lock = threading.Lock()
            self.second_attempt = threading.Event()

        def __enter__(self):
            with self.attempts_lock:
                self.attempts += 1
                first_attempt = self.attempts == 1
                if self.attempts == 2:
                    self.second_attempt.set()
            self.lock.acquire()
            if first_attempt:
                assert self.second_attempt.wait(timeout=5)
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self.lock.release()

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
                record("catalog")
                return FakeResult()
            if query.startswith("TRUNCATE TABLE"):
                self.truncates += 1
                record("truncate")
                return FakeResult()
            raise AssertionError(f"unexpected query: {query}")

        def commit(self):
            self.commits += 1
            record("commit")

        def close(self):
            self.closed = True

    def connect_postgres(_url=None):
        connection = FakeConnection()
        with created_lock:
            created_connections.append(connection)
        barrier.wait(timeout=5)
        return connection

    reset_lock = SynchronizedLock()
    connect = _make_lazy_postgres_reset_connector(connect_postgres, lock_factory=lambda: reset_lock)

    def connect_and_record_return():
        connection = connect()
        record("returned")
        return connection

    with ThreadPoolExecutor(max_workers=2) as pool:
        connections = list(pool.map(lambda _: connect_and_record_return(), range(2)))

    assert len(connections) == 2
    assert connections[0] is not connections[1]
    assert sum(conn.catalog_queries for conn in created_connections) == 1
    assert sum(conn.truncates for conn in created_connections) == 1
    assert sum(conn.commits for conn in created_connections) == 1
    assert not any(conn.closed for conn in created_connections)
    assert reset_lock.attempts == 2
    commit_index = events.index("commit")
    assert all(index > commit_index for index, event in enumerate(events) if event == "returned")
