"""Yandex Realtime WebSocket transport, with server-side credentials only."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


REALTIME_URL = "wss://ai.api.cloud.yandex.net/v1/realtime/openai"
OUTPUT_RATE = 44100
TTS_MODEL = "speech-realtime-tts-live"
MODEL_UPSTREAMS = {
    "speech-realtime-deepseek-v4-flash": "speech-realtime-deepseek-v4-flash",
    "speech-realtime-max-tts-live": "speech-realtime-max-tts-live/latest",
    TTS_MODEL: "speech-realtime-tts-live/latest",
}


class RealtimeError(RuntimeError):
    """A safe client-facing transport failure; never contains a credential or URL."""


@dataclass
class RealtimeResult:
    transcript: str = ""
    reply: str = ""
    audio: bytearray = field(default_factory=bytearray)
    usage: dict = field(default_factory=dict)
    status: str = "failed"
    response_id: str | None = None
    transcription_status: str = "completed"
    error_message: str | None = None


async def generate_realtime(
    *,
    api_key: str,
    folder_id: str,
    model: str,
    voice: str,
    role: str | None,
    response_mode: str,
    text: str | None,
    pcm: bytes,
    input_rate: int,
    history: list[dict],
    instructions: str | None,
    emit: Callable,
    cancelled: Callable[[], bool],
    timeout: float,
) -> RealtimeResult:
    """Send one real Realtime turn; neither STT/TTS v1 nor another LLM is substituted.

    The HTTP facade records one turn before connecting. Manual commit disables VAD
    so it cannot split the uploaded utterance into multiple paid responses.
    """
    upstream = MODEL_UPSTREAMS.get(model)
    if upstream is None:
        raise RealtimeError("Выберите поддерживаемую модель Realtime")
    tts_only = model == TTS_MODEL
    if tts_only and not text:
        raise RealtimeError("Speech TTS Live принимает текст для озвучивания")
    result = RealtimeResult(transcript=text or "", transcription_status="completed" if text else "pending")
    modalities = ["text"] if response_mode == "text" else ["audio"]
    audio_options = {
        "output": {
            "format": {"type": "audio/pcm", "rate": OUTPUT_RATE},
            "voice": voice,
        }
    }
    if role:
        audio_options["output"]["role"] = role
    if not tts_only:
        audio_options["input"] = {
            "format": {"type": "audio/pcm", "rate": input_rate},
            "turn_detection": None,
        }
    config = {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "output_modalities": modalities,
            "audio": audio_options,
        },
    }
    if instructions and not tts_only:
        config["session"]["instructions"] = instructions
    deadline = time.monotonic() + timeout
    url = REALTIME_URL + "?" + urlencode({"model": f"gpt://{folder_id}/{upstream}"})
    # Fixed trusted endpoint: a configurable URL must never receive the active key.
    async with connect(
        url,
        additional_headers={"Authorization": "Api-Key " + api_key},
        open_timeout=min(30, timeout),
        close_timeout=5,
        max_size=None,
    ) as websocket:
        async def send(event: dict) -> None:
            if cancelled():
                raise RealtimeError("Голосовая сессия отменена")
            await websocket.send(json.dumps(event, ensure_ascii=False))

        async def receive() -> dict:
            while True:
                if cancelled():
                    raise RealtimeError("Голосовая сессия отменена")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RealtimeError("Истекло время голосовой сессии")
                try:
                    raw = await asyncio.wait_for(websocket.recv(), min(1, remaining))
                except asyncio.TimeoutError:
                    continue
                try:
                    event = json.loads(raw)
                except (ValueError, TypeError):
                    raise RealtimeError("Realtime вернул некорректное событие") from None
                if not isinstance(event, dict):
                    raise RealtimeError("Realtime вернул некорректное событие")
                if event.get("type") == "error":
                    # Provider messages may echo request material. Only expose the
                    # structural error code and keep credentials out of exceptions.
                    error = event.get("error") or {}
                    code = str(error.get("code") or error.get("type") or "provider_error")
                    safe_code = "".join(c for c in code if c.isalnum() or c in "_-")[:80]
                    raise RealtimeError("Realtime отклонил запрос: " + safe_code)
                return event

        await send(config)
        while (await receive()).get("type") != "session.updated":
            pass

        # Text TTS must receive exactly the requested text, without chat history.
        if not tts_only:
            for message in history:
                message_role = message.get("role")
                message_text = message.get("text")
                if message_role not in {"system", "user", "assistant"} or not isinstance(message_text, str):
                    continue
                if not message_text:
                    continue
                await send({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": message_role,
                        "content": [{
                            "type": "output_text" if message_role == "assistant" else "input_text",
                            "text": message_text,
                        }],
                    },
                })
        if text:
            await send({
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            })
        else:
            for offset in range(0, len(pcm), 32768):
                await send({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(pcm[offset:offset + 32768]).decode("ascii"),
                })
            await send({"type": "input_audio_buffer.commit"})
        await send({"type": "response.create"})

        text_parts: list[str] = []
        audio_text_parts: list[str] = []
        response_finished = False
        while True:
            try:
                event = await receive()
            except (RealtimeError, ConnectionClosed):
                if not response_finished or cancelled():
                    raise
                # The independently produced input transcription can arrive
                # after response.done. Preserve a completed provider response
                # if transcription eventually fails or the connection ends.
                result.transcription_status = "unavailable"
                return result
            event_type = event.get("type")
            if event_type == "conversation.item.input_audio_transcription.completed":
                result.transcript = str(event.get("transcript") or "").strip()
                result.transcription_status = "completed" if result.transcript else "empty"
                emit(event_type, transcript=result.transcript, item_id=event.get("item_id"))
                if response_finished:
                    return result
            elif event_type == "conversation.item.input_audio_transcription.failed":
                result.transcription_status = "failed"
                if response_finished:
                    return result
            elif event_type == "response.output_text.delta":
                text_parts.append(str(event.get("delta") or ""))
            elif event_type == "response.output_text.done":
                result.reply = str(event.get("text") or "")
            elif event_type == "response.output_audio_transcript.delta":
                audio_text_parts.append(str(event.get("delta") or ""))
            elif event_type == "response.output_audio_transcript.done":
                result.reply = str(event.get("transcript") or result.reply)
            elif event_type == "response.output_audio.delta":
                try:
                    result.audio.extend(base64.b64decode(event.get("delta") or "", validate=True))
                except (ValueError, TypeError):
                    raise RealtimeError("Realtime вернул некорректное аудио") from None
                # Never retain more audio than the session can play within its TTL.
                if len(result.audio) > OUTPUT_RATE * 2 * timeout:
                    raise RealtimeError("Аудио превышает время хранения голосовой сессии")
            elif event_type == "response.done":
                response_finished = True
                response = event.get("response") or {}
                if not isinstance(response, dict):
                    raise RealtimeError("Realtime вернул некорректный итог ответа")
                result.status = str(response.get("status") or "failed")
                details = response.get("status_details")
                if "error" in response or (isinstance(details, dict) and "error" in details):
                    result.status = "failed"
                result.response_id = response.get("id")
                usage = response.get("usage")
                result.usage = usage if isinstance(usage, dict) else {}
                if not result.reply:
                    result.reply = "".join(text_parts or audio_text_parts).strip()
                if not result.reply:
                    for item in response.get("output") or []:
                        for part in item.get("content") or []:
                            result.reply += str(part.get("text") or part.get("transcript") or "")
                if tts_only and not result.reply:
                    result.reply = text or ""
                if result.transcription_status != "pending":
                    return result
