import json
from pathlib import Path

from scripts.build_ai_index import build_index


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
