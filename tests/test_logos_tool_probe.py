"""Run the audit through real native tools without a model or external HTTP."""

import json
import os
import subprocess
import sys
from pathlib import Path

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
            "YANDEX_API_KEY": "",
            "YANDEX_PROJECT_ID": "",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert len(result.stdout.strip()) < 4000
    assert len(result.stdout.splitlines()) == 1
    report = json.loads(result.stdout)
    assert report["status"] == "completed" and report["cleanup"] is True
    assert report["mode"] == "logos-tools"
    assert report["generation_requests"] == 0
    assert report["provider_credentials_used"] is False
    assert report["active_categories"] == ["filesystem"]
    assert report["categories"]["filesystem"] == report["exposed_count"] == 17
    assert report["hosted_enabled"] == {
        "code_interpreter": False,
        "file_search": False,
        "web_search": False,
    }
    from scripts.logos_knowledge import DEFAULT_STORE_ID
    from scripts.logos_quality import MODEL, dialogue_params

    # The subprocess inventories its own registry before application-only tools
    # are registered. Parent-process suite imports must not redefine its scope.
    params = dialogue_params([DEFAULT_STORE_ID], list(report["categories"]))
    native = report["native_profile"]
    assert native["model"] == MODEL
    assert set(native["categories"]) == set(params["active_tool_categories"])
    assert len(native["categories"]) == len(set(native["categories"]))
    assert set(native["categories"]) == set(report["categories"])
    assert native["offered_count"] == sum(report["categories"].values())
    assert native["offered_count"] > report["exposed_count"]
    assert native["hosted_enabled"] == {
        "code_interpreter": True,
        "file_search": True,
        "web_search": True,
    }
    assert native["hosted_enabled"] == {
        name: cfg["enabled"] for name, cfg in params["tools_config"].items()
    }
    assert native["configured_vector_store_ids"] == DEFAULT_STORE_ID
    assert native["verification"] == "configuration_only; hosted_and_other_categories_not_executed"
    assert native["knowledge_status"] == "not_prepared_or_verified_by_diagnostic"
    assert len(report["tools"]) == 17
    assert sum(value == "pass" for value in report["tools"].values()) == 16
    assert report["tools"]["apply_patch"]["s"] == "blocked"
    assert report["tools"]["apply_patch"]["phase"] == "approval_required"
    assert report["patch_schema_search_text"] is True
    assert len(report["defaults"]) == 9 and set(report["defaults"].values()) == {"pass"}
    assert "http_fetch:external" not in report["defaults"]
    assert len(report["null"]) == 8
    assert all(
        value == {"advertised": True, "accepted": True, "phase": "execution"}
        for value in report["null"].values()
    )
    assert parent_memory.read_bytes() == b"do not use the parent invocation store"
    assert {
        path.name: path.read_bytes() for path in backups.glob("*") if path.is_file()
    } == existing_backups
    assert snapshot() == before
