"""Run the audit through real native tools without a model or external HTTP."""

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_offline_probe_reports_native_contracts_and_cleans_owned_files(tmp_path):
    def snapshot():
        return subprocess.check_output(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=ROOT
        )

    before = snapshot()
    backups = ROOT / ".safe_backups"
    existing_backups = {
        path.name: path.read_bytes() for path in backups.glob("*") if path.is_file()
    }
    parent_memory = tmp_path / "parent.memory"
    parent_memory.write_bytes(b"do not use the parent invocation store")
    result = subprocess.run(
        [sys.executable, "-m", "scripts.logos_tool_probe"],
        cwd=ROOT,
        env={
            **os.environ,
            "ALICE_MEMORY_PATH": str(parent_memory),
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "NO_PROXY": "127.0.0.1,localhost",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert len(result.stdout.strip()) < 4000
    assert len(result.stdout.splitlines()) == 1
    report = json.loads(result.stdout)
    assert report["status"] == "completed" and report["cleanup"] is True
    assert report["active_categories"] == ["filesystem"]
    assert report["categories"]["filesystem"] == report["exposed_count"] == 17
    assert report["hosted_enabled"] == {
        "code_interpreter": False,
        "file_search": False,
        "web_search": False,
    }
    assert len(report["tools"]) == 17
    assert sum(value == "pass" for value in report["tools"].values()) == 16
    assert report["tools"]["apply_patch"]["s"] == "blocked"
    assert report["tools"]["apply_patch"]["phase"] == "approval_required"
    assert report["patch_schema_search_text"] is False
    assert len(report["defaults"]) == 9 and set(report["defaults"].values()) == {"pass"}
    assert "http_fetch:external" not in report["defaults"]
    assert len(report["null"]) == 8
    assert all(
        value == {"advertised": True, "accepted": False, "phase": "validation"}
        for value in report["null"].values()
    )
    assert parent_memory.read_bytes() == b"do not use the parent invocation store"
    assert {
        path.name: path.read_bytes() for path in backups.glob("*") if path.is_file()
    } == existing_backups
    assert snapshot() == before
