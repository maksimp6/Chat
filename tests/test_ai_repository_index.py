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


def _build_impact_fixture(tmp_path: Path) -> dict:
    """Build a small fixture index with pkg/service.py, pkg/helpers.py, tests/test_service.py."""
    _write(
        tmp_path,
        "pkg/helpers.py",
        "def helper(x):\n    return x\n",
    )
    _write(
        tmp_path,
        "pkg/service.py",
        "from pkg.helpers import helper\n\ndef run(x):\n    return helper(x)\n",
    )
    _write(
        tmp_path,
        "pkg/unrelated.py",
        "def unrelated():\n    return 42\n",
    )
    _write(
        tmp_path,
        "tests/test_service.py",
        "from pkg.service import run\n\ndef test_run():\n    assert run(1)\n",
    )
    return build_index(tmp_path)


def test_query_affected_direct_change(tmp_path):
    index = _build_impact_fixture(tmp_path)
    result = query_affected(index, ["pkg/service.py"])

    assert "pkg.service" in result["affected_modules"]
    assert result["query"] == "affected_modules"
    assert result["changed_files"] == ["pkg/service.py"]


def test_query_affected_reverse_dep_included(tmp_path):
    index = _build_impact_fixture(tmp_path)
    # Changing helpers.py should pull in service.py (which imports helpers)
    result = query_affected(index, ["pkg/helpers.py"])

    assert "pkg.helpers" in result["affected_modules"]
    assert "pkg.service" in result["affected_modules"]


def test_query_affected_unrelated_module_excluded(tmp_path):
    index = _build_impact_fixture(tmp_path)
    result = query_affected(index, ["pkg/service.py"])

    assert "pkg.unrelated" not in result["affected_modules"]


def test_query_affected_tests_collected(tmp_path):
    index = _build_impact_fixture(tmp_path)
    result = query_affected(index, ["pkg/service.py"])

    assert "tests/test_service.py" in result["affected_tests"]


def test_query_affected_provenance_fields(tmp_path):
    index = _build_impact_fixture(tmp_path)
    result = query_affected(index, ["pkg/service.py"], git_revision="abc123")

    prov = result["provenance"]
    assert prov["index_schema_version"] == 1
    assert prov["git_revision"] == "abc123"
    file_prov = prov["files"]["pkg/service.py"]
    assert file_prov["in_index"] is True
    assert len(file_prov["sha256_in_index"]) == 64
    assert file_prov["module"] == "pkg.service"


def test_query_affected_unknown_path_in_index_flag(tmp_path):
    index = _build_impact_fixture(tmp_path)
    result = query_affected(index, ["pkg/nonexistent.py"])

    prov_file = result["provenance"]["files"]["pkg/nonexistent.py"]
    assert prov_file["in_index"] is False
    assert "sha256_in_index" not in prov_file


def test_query_affected_is_deterministic(tmp_path):
    index = _build_impact_fixture(tmp_path)
    first = query_affected(index, ["pkg/helpers.py"])
    second = query_affected(index, ["pkg/helpers.py"])

    assert first == second


def test_query_affected_empty_input(tmp_path):
    index = _build_impact_fixture(tmp_path)
    result = query_affected(index, [])

    assert result["affected_modules"] == []
    assert result["affected_tests"] == []
    assert result["changed_files"] == []


def test_query_affected_json_serialisable(tmp_path):
    index = _build_impact_fixture(tmp_path)
    result = query_affected(index, ["pkg/service.py"])

    encoded = json.dumps(result, sort_keys=True)
    assert json.loads(encoded) == result
