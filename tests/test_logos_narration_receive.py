"""Recover actual runner log framing; reject corrupted or unsafe audio deliveries."""

import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from scripts.logos_narration_receive import recover
from scripts.logos_once import emit_result


class UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate YAML key")
        return super().construct_mapping(node, deep=deep)


def example():
    audio = b"OggS-OpusHead-offline-control"
    text = "Я — Логос. Это тест доставки, не реальная речь."
    return {
        "status": "completed",
        "narration": {
            "status": "audio_ready",
            "audio_status": "ready",
            "text": text,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "parts": [
                {
                    "file": "speech-001.ogg",
                    "sha256": hashlib.sha256(audio).hexdigest(),
                    "audio_base64": base64.b64encode(audio).decode(),
                }
            ],
        },
    }


def framed(result, capsys):
    emit_result(result, None)
    return "\n".join(
        "2026-10-24T12:00:00Z " + line for line in capsys.readouterr().out.splitlines()
    )


def test_actual_runner_delivery_recovers_identical_audio_and_text(tmp_path, capsys):
    result = example()
    receipt = recover(framed(result, capsys), tmp_path / "out")
    assert (tmp_path / "out/speech-001.ogg").read_bytes() == base64.b64decode(
        result["narration"]["parts"][0]["audio_base64"]
    )
    assert (tmp_path / "out/story.txt").read_text() == result["narration"]["text"]
    assert receipt["delivery"] == "recovered_from_log_playback_not_verified"
    assert "audio_base64" not in (tmp_path / "out/receipt.json").read_text()


@pytest.mark.parametrize("mutation", ["text", "audio", "path", "missing", "status", "base64"])
def test_invalid_delivery_writes_nothing(tmp_path, capsys, mutation):
    result = example()
    item = result["narration"]
    if mutation == "text":
        item["text"] = "Подмена"
    elif mutation == "audio":
        item["parts"][0]["sha256"] = "0" * 64
    elif mutation == "path":
        item["parts"][0]["file"] = "../escape.ogg"
    elif mutation == "missing":
        item["parts"] = []
    elif mutation == "status":
        item["status"] = "failed"
    else:
        item["parts"][0]["audio_base64"] = "%%%%"
    output = tmp_path / "out"
    with pytest.raises(ValueError):
        recover(framed(result, capsys), output)
    assert not output.exists() and not (tmp_path / "escape.ogg").exists()


@pytest.mark.parametrize("mutation", ["checksum", "lost_part", "lost_hash", "order"])
def test_outer_log_corruption_is_rejected(tmp_path, capsys, mutation):
    log = framed(example(), capsys)
    lines = log.splitlines()
    if mutation == "checksum":
        lines = [
            line.split("LOGOS_RESULT_SHA256=")[0] + "LOGOS_RESULT_SHA256=" + "0" * 64
            if "LOGOS_RESULT_SHA256=" in line
            else line
            for line in lines
        ]
    elif mutation == "lost_part":
        lines = [line for line in lines if "LOGOS_RESULT_PART=" not in line]
    elif mutation == "lost_hash":
        lines = [line for line in lines if "LOGOS_RESULT_SHA256=" not in line]
    else:
        lines = [line.replace("LOGOS_RESULT_PART=1/1:", "LOGOS_RESULT_PART=2/2:") for line in lines]
    with pytest.raises(ValueError):
        recover("\n".join(lines), tmp_path / "out")


def test_last_result_supersedes_previous_but_incomplete_last_frame_fails(tmp_path, capsys):
    first = framed(example(), capsys)
    later = copy.deepcopy(example())
    later["status"] = "failed"
    log = first + "\n" + framed(later, capsys)
    assert recover(log, tmp_path / "complete")["audio_status"] == "ready"
    with pytest.raises(ValueError, match="Incomplete"):
        recover(log + "\nLOGOS_RESULT_PART=1/2:YWJj", tmp_path / "incomplete")


@pytest.mark.parametrize("filename", ["ci.yml", "launch-smoke.yml"])
def test_runner_temp_context_is_only_in_job_environment(filename):
    # GitHub rejects runner.* in workflow-level env before creating any job.
    workflow = yaml.load((Path(".github/workflows") / filename).read_text(), Loader=UniqueKeyLoader)
    assert "runner." not in json.dumps(workflow.get("env", {}))
    jobs = workflow["jobs"]
    if filename == "ci.yml":
        assert jobs["backend"]["env"]["OUT"] == "${{ runner.temp }}/ci-out/ci"
        assert jobs["backend"]["env"]["YANDEX_API_KEY"] == "ci-test-placeholder"
        step = next(
            step for step in jobs["backend"]["steps"] if step.get("id") == "narration_tests"
        )
        assert "tests/test_logos_narration_receive.py" in step["run"]
    else:
        for job in ("offline", "live-provider"):
            assert jobs[job]["env"]["EVIDENCE_DIR"] == "${{ runner.temp }}/ci-out/launch-smoke"


def test_yaml_control_rejects_duplicate_environment_keys():
    with pytest.raises(ValueError, match="Duplicate"):
        yaml.load("env: {A: 1}\nenv: {B: 2}\n", Loader=UniqueKeyLoader)
