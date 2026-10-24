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


def test_one_step_runner_attaches_real_offline_story_without_changing_native_status(tmp_path):
    from scripts.logos_once import attach_narration

    result = {"status": "blocked", "generation_requests": 0, "reply": "secret-canary"}
    attach_narration(result, tmp_path / "once", {**os.environ, "GITHUB_TOKEN": "secret-canary"})
    assert result["status"] == "blocked" and result["generation_requests"] == 0
    assert result["narration"]["status"] == "text_ready"
    assert "заблокирован" in result["narration"]["text"]
    assert "secret-canary" not in result["narration"]["text"]
    assert result["narration"]["tts_requests_attempted"] == 0


def test_one_step_narration_failure_keeps_model_result_and_does_not_retry(tmp_path, monkeypatch):
    from scripts import logos_once

    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        assert "GITHUB_TOKEN" not in kwargs["env"]
        raise subprocess.TimeoutExpired(args[0], 110)

    monkeypatch.setattr(logos_once.subprocess, "run", fail)
    result = {"status": "completed", "reply": "Ответ"}
    logos_once.attach_narration(result, tmp_path / "once", {**os.environ, "GITHUB_TOKEN": "canary"})
    assert result["status"] == "completed" and result["reply"] == "Ответ"
    assert result["narration"]["status"] == "failed" and len(calls) == 1


def test_one_step_keeps_single_visible_step_with_tts_opt_in():
    workflow = yaml.safe_load(Path(".github/workflows/logos-once.yml").read_text())
    steps = workflow["jobs"]["logos"]["steps"]
    assert len(steps) == 1 and steps[0]["name"] == "logos"
    assert "vars.LOGOS_TTS_ALLOWED" in steps[0]["env"]["LOGOS_TTS_ALLOWED"]


def test_one_step_audio_bytes_survive_result_log_delivery(tmp_path, monkeypatch, capsys):
    import base64

    from scripts import logos_once

    audio = b"OggS-OpusHead-offline-positive-control"
    monkeypatch.setattr(narration, "synthesize", lambda *a: audio)

    def child(command, **kwargs):
        narration.main(command[3:])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(logos_once.subprocess, "run", child)
    result = {"status": "completed", "reply": "Ответ"}
    logos_once.attach_narration(
        result,
        tmp_path / "once",
        {**os.environ, "LOGOS_TTS_ALLOWED": "true", "LOGOS_TTS_MAX_CHARS": "500"},
    )
    assert result["narration"]["audio_status"] == "ready"
    capsys.readouterr()
    logos_once.emit_result(result, tmp_path / "summary")
    chunks = [
        line.split(":", 1)[1]
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("LOGOS_RESULT_PART=")
    ]
    delivered = json.loads(base64.b64decode("".join(chunks)))
    assert base64.b64decode(delivered["narration"]["parts"][0]["audio_base64"]) == audio
    assert "Narration audio: ready" in (tmp_path / "summary").read_text()


def test_real_repeated_process_never_reuses_previous_story(tmp_path):
    from scripts.logos_once import attach_narration

    environment = {**os.environ, "LOGOS_TTS_ALLOWED": "false"}
    first = {"status": "completed", "reply": "Первый ответ"}
    second = {"status": "failed", "reply": "Второй ответ"}
    attach_narration(first, tmp_path, environment)
    attach_narration(second, tmp_path, environment)
    assert first["narration"]["status"] == second["narration"]["status"] == "text_ready"
    assert "Второй ответ" in second["narration"]["text"]
    assert "Первый ответ" not in second["narration"]["text"]
    assert "ошибкой" in second["narration"]["text"]
    assert first["narration"]["source_sha256"] != second["narration"]["source_sha256"]
    assert len(list(tmp_path.glob("narration-*/output/receipt.json"))) == 2


@pytest.mark.parametrize("failure", ["wrong_source", "changed_story", "exit_code"])
def test_process_receipt_cannot_claim_success_for_wrong_data(tmp_path, monkeypatch, failure):
    from scripts import logos_once

    def child(command, **kwargs):
        narration.main(command[3:])
        output = Path(command[command.index("--out") + 1])
        receipt = output / "receipt.json"
        if failure == "wrong_source":
            data = json.loads(receipt.read_text())
            data["source_sha256"] = "0" * 64
            receipt.write_text(json.dumps(data))
        elif failure == "changed_story":
            (output / "story.txt").write_text("Старый или подменённый рассказ")
        return SimpleNamespace(returncode=9 if failure == "exit_code" else 0)

    monkeypatch.setattr(logos_once.subprocess, "run", child)
    result = {"status": "completed", "reply": "Текущий ответ"}
    logos_once.attach_narration(result, tmp_path, {**os.environ, "LOGOS_TTS_ALLOWED": "false"})
    assert result["status"] == "completed" and result["reply"] == "Текущий ответ"
    assert result["narration"]["status"] == "failed"
    assert result["narration"]["error_type"] == "ValueError"


def test_failed_process_cannot_read_legacy_audio_directory(tmp_path, monkeypatch):
    from scripts import logos_once

    old = tmp_path / "narration"
    old.mkdir()
    (old / "receipt.json").write_text('{"status":"audio_ready","parts":[]}')
    (old / "story.txt").write_text("Старый ответ")
    monkeypatch.setattr(logos_once.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    result = {"status": "failed", "reply": "Новый ответ"}
    logos_once.attach_narration(result, tmp_path, {**os.environ, "LOGOS_TTS_ALLOWED": "false"})
    assert result["narration"]["status"] == "failed"
    assert "text" not in result["narration"]
