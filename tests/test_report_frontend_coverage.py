from pathlib import Path

import pytest

from tests.report_frontend_coverage import build_summary


def test_build_summary_aggregates_v8_function_ranges(tmp_path: Path):
    (tmp_path / "first.json").write_text(
        '{"result":[{"functions":[{"ranges":[{"count":1}]},{"ranges":[{"count":0}]}]}]}',
        encoding="utf-8",
    )
    (tmp_path / "second.json").write_text(
        '{"result":[{"functions":[{"ranges":[{"count":2}]}]}]}',
        encoding="utf-8",
    )

    assert build_summary(tmp_path) == {
        "schema_version": 1,
        "metric": "v8_function_coverage",
        "files": [
            {"file": "first.json", "functions": 2, "covered_functions": 1},
            {"file": "second.json", "functions": 1, "covered_functions": 1},
        ],
        "functions": 3,
        "covered_functions": 2,
        "percent": 66.67,
    }


def test_build_summary_requires_v8_files(tmp_path: Path):
    with pytest.raises(ValueError, match="No V8 coverage files"):
        build_summary(tmp_path)
