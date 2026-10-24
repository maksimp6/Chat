"""Offline receipt-to-story controls; network boundary only is replaced."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import logos_narration as narration


def test_story_reports_receipt_and_separates_model_words():
    story, truncated = narration.build_story(
        {"status": "no_changes", "issue": 1413, "changed_files": [], "reply": "Я нашёл ошибку."}
    )
    assert "1413" in story and "изменённых файлов: 0" in story
    assert "приёмка" in story and "утверждения требуют проверки" in story
    assert story.endswith("Я нашёл ошибку.")
    assert not truncated


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"status": "failed"}, "ошибкой"),
        ({"status": "running"}, "промежуточный"),
        ({"status": "blocked"}, "заблокирован"),
        ({"status": "failed", "error": "NativeLogosLaunchBlocked"}, "заблокирован"),
        ({"status": "needs_approval"}, "требуют разрешения"),
        ({"status": "no_changes", "provider_status": "incomplete"}, "не завершён"),
        ({"status": "changed", "provider_error_present": True}, "ошибкой"),
        ({"status": "made_up"}, "неизвестен"),
    ],
)
def test_negative_statuses_never_claim_completion(result, expected):
    story, _ = narration.build_story(result)
    assert expected in story
    assert "Текстового ответа модели" in story
    assert "Раннер сохранил результат" not in story


def test_safe_reply_redacts_before_truncation(monkeypatch):
    monkeypatch.setenv("YANDEX_API_KEY", "secret-canary")
    story, truncated = narration.build_story(
        {"status": "failed", "reply": "secret-canary token=xyz ghp_abc " + "я" * 6000}, 500
    )
    assert len(story) <= 500 and truncated
    assert not any(secret in story for secret in ("secret-canary", "xyz", "ghp_abc"))
    assert "Продолжение ответа" in story


@pytest.mark.parametrize("limit", [True, 0, 499, 2501, -10])
def test_bad_limits_never_call_provider(tmp_path, monkeypatch, limit):
    source = tmp_path / "result.json"
    source.write_text('{"status":"no_changes"}')
    monkeypatch.setattr(narration, "synthesize", lambda *a: pytest.fail("must not synthesize"))
    receipt = narration.narrate(
        source, tmp_path / "out", speak=True, max_chars=limit, voice="filipp"
    )
    assert receipt["status"] == "failed"
    assert receipt["tts_requests_attempted"] == 0


def test_offline_cli_has_zero_calls_and_preserves_source(tmp_path):
    source = tmp_path / "result.json"
    raw = b'{"status":"no_changes","reply":"Hello"}'
    source.write_bytes(raw)
    output = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.logos_narration",
            "--result",
            str(source),
            "--out",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads((output / "receipt.json").read_text())
    assert receipt["status"] == "text_ready" and receipt["tts_requests_attempted"] == 0
    assert receipt["source_sha256"] == narration.digest(raw)
    assert receipt["text_sha256"] == narration.digest((output / "story.txt").read_bytes())
    assert receipt["audio_status"] == "not_requested" and not list(output.glob("*.ogg"))
    assert source.read_bytes() == raw


def test_synthesis_chunks_are_bounded_ordered_and_hashed(tmp_path, monkeypatch):
    source = tmp_path / "result.json"
    source.write_text(json.dumps({"status": "no_changes", "reply": "Текст. " * 500}))
    calls = []

    def synthesize(text, voice):
        calls.append(text)
        return b"OggS-OpusHead-" + str(len(calls)).encode()

    monkeypatch.setattr(narration, "synthesize", synthesize)
    receipt = narration.narrate(
        source, tmp_path / "out", speak=True, max_chars=2500, voice="filipp"
    )
    assert receipt["status"] == "audio_ready"
    assert receipt["tts_requests_attempted"] == len(calls) == 3
    assert "".join(calls) == (tmp_path / "out/story.txt").read_text()
    for part in receipt["parts"]:
        assert part["characters"] <= 1000
        assert part["sha256"] == narration.digest((tmp_path / "out" / part["file"]).read_bytes())
    assert receipt["cost"]["status"] == "unknown"
    assert receipt["delivery"] == "not_verified"


def test_failure_stops_without_retry_or_secret_exception(tmp_path, monkeypatch):
    source = tmp_path / "result.json"
    source.write_text('{"status":"failed","reply":"text"}')
    calls = []

    def fail(*args):
        calls.append(args)
        raise RuntimeError("secret-response-body")

    monkeypatch.setattr(narration, "synthesize", fail)
    output = tmp_path / "out"
    receipt = narration.narrate(source, output, speak=True, max_chars=2500, voice="filipp")
    assert receipt["status"] == receipt["audio_status"] == "failed"
    assert len(calls) == 1 and receipt["parts"] == []
    assert "secret-response-body" not in (output / "receipt.json").read_text()
    assert (output / "story.txt").exists()


@pytest.mark.parametrize("raw", ["[]", '{"status":true}', "invalid"])
def test_invalid_source_saves_failure_not_audio(tmp_path, raw):
    source = tmp_path / "result.json"
    source.write_text(raw)
    receipt = narration.narrate(
        source, tmp_path / "out", speak=True, max_chars=2500, voice="filipp"
    )
    assert receipt["status"] == "failed" and receipt["tts_requests_attempted"] == 0


def test_no_stale_audio_reuse(tmp_path):
    with pytest.raises(FileExistsError):
        narration.narrate(
            tmp_path / "missing", tmp_path, speak=True, max_chars=2500, voice="filipp"
        )


def test_contract_transport_required_with_no_http_fallback(monkeypatch):
    import requests

    monkeypatch.setitem(sys.modules, "api.yandex_transport", None)
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("raw HTTP bypass"))
    with pytest.raises(ModuleNotFoundError):
        narration.synthesize("Проверка", "filipp")


@pytest.mark.parametrize("status,audio", [(503, b"secret"), (200, b"error"), (200, b"")])
def test_transport_errors_and_non_audio_rejected(monkeypatch, status, audio):
    calls = []

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(status_code=status, content=audio)

    monkeypatch.setitem(
        sys.modules, "api.yandex_transport", SimpleNamespace(yandex_request=request)
    )
    monkeypatch.setenv("YANDEX_API_KEY", "canary")
    with pytest.raises(ValueError, match="OggOpus"):
        narration.synthesize("Привет", "filipp")
    assert len(calls) == 1
    assert "folderId" not in calls[0][1]["data"]


def test_workflow_keeps_narration_outside_model_checkout_and_reports_artifact(tmp_path):
    workflow = yaml.safe_load(Path(".github/workflows/logos.yml").read_text())
    steps = {step["name"]: step for step in workflow["jobs"]["alice"]["steps"]}
    step = steps["Build Logos narration"]
    assert step["if"] == "always()"
    assert "vars.LOGOS_TTS_ALLOWED" in step["env"]["LOGOS_TTS_ALLOWED"]
    assert "vars.LOGOS_TTS_MAX_CHARS" in step["env"]["LOGOS_TTS_MAX_CHARS"]
    assert "$RUNNER_TEMP/logos-narration" in step["run"]
    assert "NARRATION_URL" in steps["Report result"]["env"]
    assert steps["Preserve Logos narration"]["if"] == "always()"
    # Execute the actual shell with no result: narration fails, core step stays usable.
    result = subprocess.run(
        ["bash", "-c", step["run"]],
        check=False,
        env={
            **os.environ,
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_OUTPUT": str(tmp_path / "output"),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
            "LOGOS_TTS_ALLOWED": "false",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "status=failed" in (tmp_path / "output").read_text()
    assert json.loads((tmp_path / "logos-narration/receipt.json").read_text())["status"] == "failed"
