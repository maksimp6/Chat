"""Turn a saved Logos runner receipt into a bounded Russian story and SpeechKit audio.

No model is called. Synthesis is explicit, never retried, and requires the shared
Yandex transport from #1569. The source receipt remains unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlencode

from scripts.narration_host import yandex_request
from scripts.narration_trace_security import sanitize_trace_value

TTS_URL = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
MAX_CHARS = 2500
CHUNK_CHARS = 1000
VOICES = {"filipp", "ermil", "jane", "oksana", "zahar", "alyss", "omazh"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_reply(text: str) -> str:
    # Remove known bootstrap values before truncating or sending text externally.
    for name, value in os.environ.items():
        if value and re.search(
            r"TOKEN|SECRET|PASSWORD|PRIVATE_KEY|API_KEY", name, re.IGNORECASE
        ):
            text = text.replace(value, "<redacted>")
    text = sanitize_trace_value(text)
    text = re.sub(
        r"\b(?:gh[pousr]_|github_pat_|sk-)[A-Za-z0-9_\-]+", "<redacted>", text
    )
    return " ".join(text.split())


def build_story(result: dict, max_chars: int = MAX_CHARS) -> tuple[str, bool]:
    if type(max_chars) is not int or not 500 <= max_chars <= MAX_CHARS:
        raise ValueError("Narration limit must be between 500 and 2500 characters")
    if not isinstance(result, dict) or not isinstance(result.get("status"), str):
        raise TypeError("Invalid runner receipt")
    lines = ["Я — Логос. Рассказываю о запуске по сохранённой квитанции раннера."]
    issue = result.get("issue")
    if type(issue) is int and issue > 0:
        lines.append(f"Это работа по задаче номер {issue}.")
    status = result["status"]
    provider = result.get("provider_status")
    if status == "blocked" or result.get("error") == "NativeLogosLaunchBlocked":
        lines.append("Запуск модели заблокирован. Выполнение задачи не подтверждено.")
    elif result.get("provider_error_present") is True or status == "failed":
        lines.append("Запуск завершился ошибкой. Выполнение задачи не подтверждено.")
    elif status == "running" or provider in {"queued", "in_progress"}:
        lines.append(
            "Сохранён промежуточный результат. Завершение запуска не подтверждено."
        )
    elif provider in {"failed", "incomplete", "cancelled"} or status == "incomplete":
        lines.append("Ответ провайдера не завершён. Выполнение задачи не подтверждено.")
    elif status == "needs_approval":
        lines.append(
            "Я остановился: запрошенные инструменты требуют разрешения владельца."
        )
    elif status in {"changed", "no_changes", "completed", "retrieved"}:
        lines.append(
            "Раннер сохранил результат. Это ещё не проверка качества и не приёмка владельца."
        )
    else:
        lines.append("Статус запуска неизвестен. Выполнение задачи не подтверждено.")
    files = result.get("changed_files")
    if isinstance(files, list) and all(isinstance(item, str) for item in files):
        lines.append(f"Квитанция отмечает изменённых файлов: {len(files)}.")
    reply = result.get("reply")
    if isinstance(reply, str) and reply.strip():
        lines.append(
            "Далее мои слова из ответа модели; их утверждения требуют проверки."
        )
        lines.append(safe_reply(reply))
    else:
        lines.append("Текстового ответа модели в квитанции нет.")
    story = "\n\n".join(lines)
    truncated = len(story) > max_chars
    if truncated:
        ending = " Продолжение ответа осталось в текстовой квитанции."
        story = story[: max_chars - len(ending)].rstrip() + ending
    return story, truncated


def synthesize(text: str, voice: str) -> bytes:
    # Lazy import: offline story generation works before #1569 is merged.
    # The contract transport is a port resolved on first use; a missing one fails
    # closed and there is no raw HTTP fallback.
    import requests

    if voice not in VOICES or not text or len(text) > CHUNK_CHARS:
        raise ValueError("Invalid SpeechKit chunk")
    api_key = os.getenv("YANDEX_API_KEY")
    iam_token = os.getenv("YANDEX_IAM_TOKEN")
    if api_key:
        authorization = f"Api-Key {api_key}"
    elif iam_token:
        authorization = f"Bearer {iam_token}"
    else:
        raise ValueError("SpeechKit credentials are missing")
    data = {"text": text, "lang": "ru-RU", "voice": voice, "format": "oggopus"}
    # API-key bootstrap is a service account; folderId is for user IAM tokens.
    if not api_key:
        folder = os.getenv("YANDEX_FOLDER_ID") or os.getenv("YANDEX_PROJECT_ID")
        if folder:
            data["folderId"] = folder
    if len(urlencode(data).encode("utf-8")) > 15000:
        raise ValueError("SpeechKit encoded body is too large")
    response = yandex_request(
        requests.post,
        "POST",
        TTS_URL,
        headers={"Authorization": authorization},
        data=data,
        timeout=30,
    )
    # An HTTP success alone is not audio evidence. Never expose a provider body.
    audio = response.content
    if (
        response.status_code != 200
        or len(audio) > 2 * 1024 * 1024
        or not audio.startswith(b"OggS")
        or b"OpusHead" not in audio[:512]
    ):
        raise ValueError("SpeechKit did not return OggOpus audio")
    return audio


def narrate(
    source: Path, output: Path, *, speak: bool, max_chars: int, voice: str
) -> dict:
    # A separate directory prevents reuse of old audio after a failed attempt.
    output.mkdir(parents=True, exist_ok=False)
    receipt = {
        "status": "failed",
        "audio_status": "not_requested",
        "parts": [],
        "tts_requests_attempted": 0,
        "cost": {"status": "unknown", "provider_confirmed": False},
        "delivery": "not_verified",
    }
    try:
        raw = source.read_bytes()
        receipt["source_sha256"] = digest(raw)
        story, truncated = build_story(json.loads(raw), max_chars)
        encoded = story.encode("utf-8")
        (output / "story.txt").write_bytes(encoded)
        receipt.update(
            status="text_ready",
            text_sha256=digest(encoded),
            characters=len(story),
            truncated=truncated,
        )
        if speak:
            receipt["audio_status"] = "failed"
            for offset in range(0, len(story), CHUNK_CHARS):
                chunk = story[offset : offset + CHUNK_CHARS]
                receipt["tts_requests_attempted"] += 1
                audio = synthesize(chunk, voice)
                name = f"speech-{len(receipt['parts']) + 1:03d}.ogg"
                (output / name).write_bytes(audio)
                receipt["parts"].append(
                    {"file": name, "sha256": digest(audio), "characters": len(chunk)}
                )
            receipt.update(status="audio_ready", audio_status="ready")
    except (
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        ImportError,
        LookupError,
    ) as exc:
        # Error types only: exceptions from transports may contain credentials/body.
        receipt.update(status="failed", error_type=type(exc).__name__)
    (output / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--synthesize", action="store_true")
    parser.add_argument("--max-chars", type=int, default=MAX_CHARS)
    parser.add_argument("--voice", choices=sorted(VOICES), default="filipp")
    args = parser.parse_args(argv)
    receipt = narrate(
        args.result,
        args.out,
        speak=args.synthesize,
        max_chars=args.max_chars,
        voice=args.voice,
    )
    print(json.dumps(receipt, ensure_ascii=False))
    return int(receipt["status"] == "failed")


if __name__ == "__main__":
    from composition import wiring

    wiring.register_units("narration")
    raise SystemExit(main())
