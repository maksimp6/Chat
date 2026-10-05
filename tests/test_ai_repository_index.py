import json
from pathlib import Path
import subprocess

from scripts.build_ai_index import build_index, query_affected


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_python_ast_index_is_deterministic_and_compact(tmp_path):
    _write(
        tmp_path,
        "pkg/service.py",
        """
import os
from pkg.helpers import helper as local_helper

class Service:
    def run(self, value):
        return local_helper(value)

async def fetch():
    return Service().run(1)
""".strip()
        + "\n",
    )
    _write(
        tmp_path,
        "tests/test_service.py",
        """
from pkg.service import Service

def test_run():
    assert Service().run(1)
""".strip()
        + "\n",
    )

    first = build_index(tmp_path)
    second = build_index(tmp_path)

    assert first == second
    assert first["schema_version"] == 1
    assert first["summary"] == {"files": 2, "symbols": 4, "test_files": 1}

    service = next(item for item in first["files"] if item["path"] == "pkg/service.py")
    assert service["module"] == "pkg.service"
    assert service["is_test"] is False
    assert [symbol["qualified_name"] for symbol in service["symbols"]] == [
        "Service",
        "Service.run",
        "fetch",
    ]
    assert "local_helper" in service["calls"]
    assert "Service.run" in service["calls"]
    assert all("source" not in item for item in first["files"])
    assert all(len(item["sha256"]) == 64 for item in first["files"])

    assert first["tests_by_module"]["pkg.service"] == ["tests/test_service.py"]


def test_python_ast_index_hash_changes_only_for_changed_file(tmp_path):
    _write(tmp_path, "a.py", "def alpha():\n    return 1\n")
    _write(tmp_path, "b.py", "def beta():\n    return 2\n")

    before = build_index(tmp_path)
    before_hashes = {item["path"]: item["sha256"] for item in before["files"]}

    _write(tmp_path, "a.py", "def alpha():\n    return 3\n")
    after = build_index(tmp_path)
    after_hashes = {item["path"]: item["sha256"] for item in after["files"]}

    assert before_hashes["a.py"] != after_hashes["a.py"]
    assert before_hashes["b.py"] == after_hashes["b.py"]


def test_python_ast_index_json_is_machine_readable(tmp_path):
    _write(tmp_path, "module.py", "def hello():\n    return print('hi')\n")

    payload = build_index(tmp_path)
    encoded = json.dumps(payload, sort_keys=True)
    decoded = json.loads(encoded)

    assert decoded == payload
    assert decoded["files"][0]["calls"] == ["print"]


def test_python_ast_index_keeps_nested_functions_distinct_from_methods(tmp_path):
    _write(
        tmp_path,
        "nested.py",
        """
def outer():
    def inner():
        return 1

    class Local:
        def method(self):
            return inner()

    return Local().method()
""".strip()
        + "\n",
    )

    payload = build_index(tmp_path)
    nested = payload["files"][0]
    kinds = {symbol["qualified_name"]: symbol["kind"] for symbol in nested["symbols"]}

    assert kinds["outer"] == "function"
    assert kinds["outer.inner"] == "function"
    assert kinds["outer.Local"] == "class"
    assert kinds["outer.Local.method"] == "method"


def test_python_ast_index_prefers_git_tracked_files(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _write(tmp_path, "tracked.py", "def tracked():\n    return 1\n")
    _write(tmp_path, "untracked.py", "def untracked():\n    return 2\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.py"], check=True)

    payload = build_index(tmp_path)

    assert [item["path"] for item in payload["files"]] == ["tracked.py"]


# ---------------------------------------------------------------------------
# query_affected tests
# ---------------------------------------------------------------------------


def _fixture_index(tmp_path: Path):
    """Build a fixed, deterministic index fixture used by query_affected tests."""
    _write(
        tmp_path,
        "pkg/models.py",
        "class User:\n    pass\n",
    )
    _write(
        tmp_path,
        "pkg/service.py",
        "from pkg.models import User\n\ndef create(name):\n    return User()\n",
    )
    _write(
        tmp_path,
        "pkg/utils.py",
        "def helper():\n    return 1\n",
    )
    _write(
        tmp_path,
        "tests/test_service.py",
        "from pkg.service import create\n\ndef test_create():\n    assert create('x')\n",
    )
    _write(
        tmp_path,
        "tests/test_models.py",
        "from pkg.models import User\n\ndef test_user():\n    assert User()\n",
    )
    return build_index(tmp_path)


def test_query_affected_direct_change_returns_module_and_tests(tmp_path):
    index = _fixture_index(tmp_path)
    result = query_affected(index, ["pkg/models.py"])

    assert "pkg.models" in result["affected_modules"]
    assert "tests/test_models.py" in result["affected_tests"]


def test_query_affected_first_order_reverse_dependency(tmp_path):
    index = _fixture_index(tmp_path)
    result = query_affected(index, ["pkg/models.py"])

    # pkg.service imports pkg.models, so it is also affected
    assert "pkg.service" in result["affected_modules"]
    # tests for pkg.service are pulled in too
    assert "tests/test_service.py" in result["affected_tests"]


def test_query_affected_unrelated_module_not_included(tmp_path):
    index = _fixture_index(tmp_path)
    result = query_affected(index, ["pkg/utils.py"])

    assert "pkg.models" not in result["affected_modules"]
    assert "pkg.service" not in result["affected_modules"]
    assert "tests/test_models.py" not in result["affected_tests"]
    assert "tests/test_service.py" not in result["affected_tests"]


def test_query_affected_provenance_has_required_fields(tmp_path):
    index = _fixture_index(tmp_path)
    result = query_affected(index, ["pkg/models.py"])

    assert result["schema_version"] == 1
    assert result["query"] == "affected_modules"
    assert "provenance" in result
    assert "git_revision" in result["provenance"]
    assert "index_root" in result["provenance"]
    assert result["provenance"]["index_root"] == "."


def test_query_affected_per_file_provenance(tmp_path):
    index = _fixture_index(tmp_path)
    result = query_affected(index, ["pkg/models.py", "nonexistent.py"])

    records = {r["path"]: r for r in result["changed_files"]}
    assert records["pkg/models.py"]["in_index"] is True
    assert len(records["pkg/models.py"]["sha256_in_index"]) == 64
    assert records["nonexistent.py"]["in_index"] is False
    assert records["nonexistent.py"]["sha256_in_index"] is None


def test_query_affected_deterministic(tmp_path):
    index = _fixture_index(tmp_path)
    first = query_affected(index, ["pkg/models.py", "pkg/utils.py"])
    second = query_affected(index, ["pkg/models.py", "pkg/utils.py"])

    assert first == second
    assert first["affected_modules"] == sorted(first["affected_modules"])
    assert first["affected_tests"] == sorted(first["affected_tests"])


def test_query_affected_empty_changed_list(tmp_path):
    index = _fixture_index(tmp_path)
    result = query_affected(index, [])

    assert result["affected_modules"] == []
    assert result["affected_tests"] == []
    assert result["changed_files"] == []


def test_query_affected_result_is_json_serialisable(tmp_path):
    index = _fixture_index(tmp_path)
    result = query_affected(index, ["pkg/service.py"])

    encoded = json.dumps(result, sort_keys=True)
    assert json.loads(encoded) == result
