"""Check the manual dialogue workflow without contacting a provider."""

from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/rag-work.yml").read_text())








def run_saved_result(tmp_path, source_run, saved_result):
    """Run the real workflow shell/Python with only the GitHub transport replaced."""
    tools = tmp_path / "bin"
    tools.mkdir()
    call_file = tmp_path / "github-call.json"
    artifact = tmp_path / "saved-answer.json"
    artifact.write_text(json.dumps(saved_result), encoding="utf-8")
    gh = tools / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, shutil, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "Path(os.environ['FAKE_GH_CALL']).write_text(json.dumps(\n"
        "    {'args': args, 'repo': os.environ['GH_REPO']}))\n"
        "destination = Path(args[args.index('--dir') + 1])\n"
        "destination.mkdir(parents=True)\n"
        "shutil.copyfile(os.environ['FAKE_ARTIFACT'], destination / 'logos-answer.json')\n"
    )
    gh.chmod(0o755)
    (tools / "python").symlink_to(sys.executable)
    script = workflow()["jobs"]["logos-result"]["steps"][0]["run"]
    result = subprocess.run(
        ["/bin/bash", "-c", script],
        cwd=tmp_path,
        env={
            "PATH": str(tools),
            "RUNNER_TEMP": str(tmp_path),
            "GH_TOKEN": "offline-token",
            "GH_REPO": "maksimp6/Chat",
            "LOGOS_SOURCE_RUN": source_run,
            "FAKE_GH_CALL": str(call_file),
            "FAKE_ARTIFACT": str(artifact),
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result, call_file


def test_saved_result_downloads_requested_run_and_prints_original_json_on_one_line(tmp_path):
    saved = {
        "status": "completed",
        "reply": 'Ответ из артефакта.\n::error::literal text\r$(touch escaped) "quote"',
        "generation_requests": 1,
        "response_ids": ["original-provider-response"],
    }
    result, call_file = run_saved_result(tmp_path, "39625558554", saved)

    assert result.returncode == 0, result.stderr
    assert json.loads(call_file.read_text()) == {
        "args": [
            "run",
            "download",
            "39625558554",
            "--name",
            "logos-answer-39625558554",
            "--dir",
            str(tmp_path / "logos-result"),
        ],
        "repo": "maksimp6/Chat",
    }
    lines = result.stdout.splitlines()
    assert len(lines) == 1 and lines[0].startswith("LOGOS_RESULT=")
    assert json.loads(lines[0].removeprefix("LOGOS_RESULT=")) == saved
    assert not (tmp_path / "escaped").exists()


@pytest.mark.parametrize(
    "source_run", ["", "0", "--help", "123; touch escaped", "$(touch escaped)", "123\n456"]
)
def test_saved_result_rejects_invalid_run_id_before_download(tmp_path, source_run):
    result, call_file = run_saved_result(tmp_path, source_run, {})

    assert result.returncode != 0
    assert "A numeric run ID is required." in result.stdout
    assert not call_file.exists()
    assert not (tmp_path / "escaped").exists()










