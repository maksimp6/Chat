"""Execute the repository workflow routing without provider or model calls."""

import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml

from scripts import repository_rag as rag
from scripts import repository_rag_verify as verify




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
