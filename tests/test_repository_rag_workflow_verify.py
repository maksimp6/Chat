"""Execute the repository workflow routing without provider or model calls."""

import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml

from scripts import repository_rag as rag
from scripts import repository_rag_verify as verify


@pytest.mark.parametrize(
    "mode,verify,expected",
    [
        ("repository-check", "true", ["--provider-check", "--verify-available"]),
        ("repository-check", "false", ["--provider-check"]),
        ("repository", "true", ["--upload", "--github-passes", "3"]),
    ],
)
def test_repository_check_verification_is_explicit_and_literal(tmp_path, mode, verify, expected):
    root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((root / ".github/workflows/semantic-work.yml").read_text())
    step = next(
        row
        for row in data["jobs"]["repository-corpus"]["steps"]
        if row["name"] == "Collect, redact, upload, and verify direct retrieval"
    )
    capture = tmp_path / "invocation.json"
    executable = tmp_path / "python"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json,os,pathlib,sys\n"
        "pathlib.Path(os.environ['CAPTURE']).write_text(json.dumps(sys.argv[1:]))\n"
    )
    executable.chmod(0o700)
    query = '$(touch UNEXPECTED) `touch ALSO_UNEXPECTED` ; echo "literal"'
    result = subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "CAPTURE": str(capture),
            "RAG_MODE": mode,
            "RAG_VERIFY_AVAILABLE": verify,
            "RAG_QUERY": query,
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_WORKSPACE": str(root),
            "GITHUB_REPOSITORY": "maksimp6/Chat",
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    arguments = json.loads(capture.read_text())
    assert arguments[-len(expected) - 2 :] == [*expected, "--query", query]
    assert arguments[:2] == ["-m", "scripts.repository_rag"]
    assert not (tmp_path / "UNEXPECTED").exists()
    assert not (tmp_path / "ALSO_UNEXPECTED").exists()
    events = data.get("on", data.get(True))
    assert events["workflow_dispatch"]["inputs"]["verify_available"]["default"] is False


def test_verification_flag_without_provider_check_stops_before_collection(tmp_path, monkeypatch):
    monkeypatch.setattr(rag, "_collect", lambda *args: pytest.fail("unexpected collection"))
    with pytest.raises(SystemExit) as error:
        rag.main(
            [
                "--verify-available",
                "--archive-dir",
                str(tmp_path / "archive"),
                "--receipt",
                str(tmp_path / "receipt.json"),
            ]
        )
    assert error.value.code == 2
    assert not (tmp_path / "archive").exists()


@pytest.mark.parametrize("status,exit_code", [("verified_fragments", 0), ("failed", 1)])
def test_read_only_verification_failure_is_visible_in_workflow_result(
    tmp_path, monkeypatch, status, exit_code
):
    inventory = {"status": "observed_without_content_verification", "generation_requests": 0}
    monkeypatch.setattr(rag, "provider_inventory", lambda *args: inventory)
    monkeypatch.setattr(rag, "_collect", lambda *args: pytest.fail("unexpected collection"))
    monkeypatch.setattr(rag, "upload_archive", lambda *args: pytest.fail("unexpected upload"))
    monkeypatch.setattr(
        verify, "verify_available", lambda *args: {"status": status, "corpus_complete": False}
    )
    receipt = tmp_path / "receipt.json"
    result = rag.main(
        [
            "--provider-check",
            "--verify-available",
            "--archive-dir",
            str(tmp_path / "archive"),
            "--receipt",
            str(receipt),
        ]
    )
    assert result == exit_code
    saved = json.loads(receipt.read_text())
    assert saved["provider_inventory"] == inventory
    assert saved["available_retrieval"]["status"] == status
    assert saved["generation_requests"] == 0
    assert saved["status"] == (
        "provider_inspected" if exit_code == 0 else "retrieval_verification_failed"
    )
