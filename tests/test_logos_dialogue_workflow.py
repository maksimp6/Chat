"""Check the manual dialogue workflow without contacting a provider."""

from pathlib import Path
import os
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/rag-work.yml").read_text())


def test_dialogue_is_manual_with_native_runtime_and_safe_checkout():
    data = workflow()
    events = data.get("on", data.get(True))
    assert "allow_model_call" not in events["workflow_dispatch"]["inputs"]
    job = data["jobs"]["logos"]
    assert job["permissions"] == {"contents": "read"}
    for condition in (
        "github.event_name == 'workflow_dispatch'",
        "inputs.mode == 'logos'",
    ):
        assert condition in job["if"]
    assert data["concurrency"]["cancel-in-progress"] is False
    assert "inputs.mode != 'logos'" in data["jobs"]["rag"]["if"]
    checkout = next(step for step in job["steps"] if step["name"] == "Checkout repository")
    assert checkout["with"]["persist-credentials"] is False
    artifact = job["steps"][-1]["with"]["path"]
    assert artifact == "${{ runner.temp }}/logos-answer.json"


@pytest.mark.parametrize(
    "question,accepted",
    [
        ("", False),
        ('почему $(touch escaped) `touch escaped` "Иисуса"', True),
    ],
)
def test_question_is_required_and_passed_literally(tmp_path, question, accepted):
    script = workflow()["jobs"]["logos"]["steps"][0]["run"]
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env={
            **os.environ,
            "RUNNER_TEMP": str(tmp_path),
            "LOGOS_QUESTION": question,
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert (result.returncode == 0) is accepted
    assert not (tmp_path / "escaped").exists()
    question_file = tmp_path / "logos-question.txt"
    if accepted:
        assert question_file.read_text() == question
    else:
        assert not question_file.exists()
