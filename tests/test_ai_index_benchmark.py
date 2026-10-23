"""Contract for a local, reproducible repository-index benchmark (#538)."""

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import bench_ai_index


def test_benchmark_checks_snapshot_equivalence_and_reports_real_work(tmp_path):
    report = bench_ai_index.benchmark(iterations=2, temporary_parent=tmp_path)

    assert report["schema_version"] == 1
    assert report["fixture"]["python_files"] == 8
    assert len(report["fixture"]["source_version"]) == 40
    assert report["fixture"]["source_version"] != report["fixture"]["changed_version"]
    assert len(report["fixture"]["content_sha256"]) == 64
    assert report["verified"] == {
        "same_revision_equivalent": True,
        "changed_revision_equivalent": True,
        "query_deterministic": True,
        "inputs_unchanged": True,
    }
    assert report["work"]["full"] == {"parsed_files": 8, "reused_files": 0, "deleted_files": 0}
    assert report["work"]["warm"] == {"parsed_files": 0, "reused_files": 8, "deleted_files": 0}
    assert report["work"]["changed"] == {
        "parsed_files": 2,
        "reused_files": 6,
        "deleted_files": 1,
    }
    assert "pkg.service" in report["query"]["affected_modules"]
    assert "tests/test_service.py" in report["query"]["affected_tests"]
    for measurement in report["timings"].values():
        assert measurement["iterations"] == 2
        assert 0 <= measurement["minimum_ms"] <= measurement["median_ms"]
        assert measurement["median_ms"] <= measurement["maximum_ms"]
    assert report["sizes"]["index_json_bytes"] > report["sizes"]["query_json_bytes"] > 0
    assert list(tmp_path.iterdir()) == []


def test_fixture_provenance_is_repeatable_between_runs(tmp_path):
    first = bench_ai_index.benchmark(iterations=1, temporary_parent=tmp_path)
    second = bench_ai_index.benchmark(iterations=1, temporary_parent=tmp_path)
    assert first["fixture"] == second["fixture"]
    assert first["work"] == second["work"]
    assert first["sizes"] == second["sizes"]
    assert first["query"] == second["query"]


@pytest.mark.parametrize("iterations", [0, -1, 101, True, 1.5, "2"])
def test_iteration_limit_rejected_before_fixture_creation(tmp_path, iterations):
    with pytest.raises(ValueError, match="iterations"):
        bench_ai_index.benchmark(iterations=iterations, temporary_parent=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_benchmark_refuses_unequal_incremental_output(tmp_path, monkeypatch):
    original = bench_ai_index.build_index

    def corrupt_incremental(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get("previous_index") is not None:
            result = copy.deepcopy(result)
            result["files"][0]["sha256"] = "0" * 64
        return result

    monkeypatch.setattr(bench_ai_index, "build_index", corrupt_incremental)
    with pytest.raises(ValueError, match="equivalent"):
        bench_ai_index.benchmark(iterations=1, temporary_parent=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_benchmark_refuses_mutating_queries(tmp_path, monkeypatch):
    original = bench_ai_index.query_affected

    def mutate(index, *args, **kwargs):
        result = original(index, *args, **kwargs)
        index["files"][0]["symbols"].clear()
        return result

    monkeypatch.setattr(bench_ai_index, "query_affected", mutate)
    with pytest.raises(ValueError, match="mutated"):
        bench_ai_index.benchmark(iterations=1, temporary_parent=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_direct_cli_reports_json_without_changing_checkout(tmp_path):
    root = Path(__file__).resolve().parents[1]
    before = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    completed = subprocess.run(
        [sys.executable, str(root / "scripts/bench_ai_index.py"), "--iterations", "1"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)
    assert all(report["verified"].values())
    assert completed.stderr == ""
    after = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert after == before


def test_cli_failure_does_not_report_verified(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/bench_ai_index.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--iterations", "0"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert not completed.stdout
    assert "iterations" in completed.stderr
