"""Voice assistant HTTP pipeline for SpeechKit STT/TTS and Alice chat."""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import requests
from flask import Blueprint, Response, jsonify, request, stream_with_context

from config import Config, TEXT_MODELS
from mcp_routes import AliceClient
from conversation_ownership import check_access
from tasks import TaskContext, enqueue, get_task_queue, register_task
from tasks.queue import FAILED
from treasury_identity import TreasuryIdentityError, get_current_owner_id


voice_bp = Blueprint("voice", __name__)
logger = logging.getLogger(__name__)

_MAX_AUDIO_BYTES = 1024 * 1024
_SESSION_TTL_SECONDS = 10 * 60
_STT_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
_TTS_URL = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
_SSE_POLL_SECONDS = 0.2
_SSE_KEEPALIVE_SECONDS = 15.0
VOICE_TASK = "voice.process"


@dataclass
class VoiceSession:
    session_id: str
    owner_id: str | None
    conversation_id: str | None
    model: str
    voice: str
    response_mode: str
    created_at: float = field(default_factory=time.time)
    audio: bytearray = field(default_factory=bytearray)
    audio_content_type: str = "application/octet-stream"
    closed: bool = False


_sessions: dict[str, VoiceSession] = {}
_sessions_lock = threading.RLock()


def _credentials() -> tuple[str | None, str | None]:
    return os.getenv("YANDEX_API_KEY"), os.getenv("YANDEX_IAM_TOKEN")


def _auth_headers() -> dict[str, str]:
    api_key, iam_token = _credentials()
    if api_key:
        return {"Authorization": f"Api-Key {api_key}"}
    if iam_token:
        return {"Authorization": f"Bearer {iam_token}"}
    raise RuntimeError("YANDEX_API_KEY or YANDEX_IAM_TOKEN is not configured")


def _folder_id() -> str | None:
    return os.getenv("YANDEX_PROJECT_ID") or os.getenv("YANDEX_FOLDER_ID")


def _cleanup_sessions() -> None:
    cutoff = time.time() - _SESSION_TTL_SECONDS
    with _sessions_lock:
        for session_id, session in list(_sessions.items()):
            if session.created_at < cutoff:
                _sessions.pop(session_id, None)


def _require_session(session_id: str) -> VoiceSession:
    """Return an open session, or a closed one rebuilt from its durable job."""
    _cleanup_sessions()
    with _sessions_lock:
        session = _sessions.get(session_id)
    if session is not None:
        return session
    job = get_task_queue().get(session_id) if session_id else None
    if job is None or job.kind != VOICE_TASK:
        raise KeyError("voice session not found")
    payload = job.payload
    return VoiceSession(
        session_id=session_id,
        owner_id=payload.get("owner_id"),
        conversation_id=payload.get("conversation_id"),
        model=payload["model"],
        voice=payload["voice"],
        response_mode=payload["response_mode"],
        audio_content_type=payload["audio_content_type"],
        closed=True,
    )


def _check_owner(session: VoiceSession) -> None:
    current_owner = get_current_owner_id(required=False)
    if session.owner_id and current_owner != session.owner_id:
        raise PermissionError("voice session access denied")
    if (
        session.conversation_id
        and current_owner
        and not check_access(session.conversation_id, current_owner)
    ):
        raise PermissionError("conversation access denied")


def _stt(audio: bytes, content_type: str = "application/octet-stream") -> str:
    if not audio:
        raise ValueError("audio is empty")
    if len(audio) > _MAX_AUDIO_BYTES:
        raise ValueError("audio exceeds 1 MiB limit")

    data: dict[str, str] = {"topic": "general", "lang": "ru-RU"}
    if content_type.startswith("audio/wav") or content_type.startswith("audio/x-wav"):
        if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise ValueError("invalid WAV audio")
        sample_rate = int.from_bytes(audio[24:28], "little")
        channels = int.from_bytes(audio[22:24], "little")
        bits = int.from_bytes(audio[34:36], "little")
        if channels != 1 or bits != 16 or sample_rate not in {8000, 16000, 48000}:
            raise ValueError("WAV must be mono 16-bit PCM at 8, 16 or 48 kHz")
        data["format"] = "lpcm"
        data["sampleRateHertz"] = str(sample_rate)
        audio = audio[44:]
    folder_id = _folder_id()
    if folder_id:
        data["folderId"] = folder_id

    response = requests.post(
        _STT_URL,
        params=data,
        headers=_auth_headers(),
        data=audio,
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"SpeechKit STT failed: HTTP {response.status_code}: {response.text[:500]}"
        )
    result = response.json().get("result", "")
    if not isinstance(result, str):
        raise RuntimeError("SpeechKit STT returned an invalid result")
    return result.strip()


def _chat(text: str, conversation_id: str | None, model: str) -> str:
    if not conversation_id:
        raise ValueError("conversation_id is required for voice chat")
    text_model = (
        model if model in TEXT_MODELS else os.getenv("ALICE_VOICE_CHAT_MODEL", "aliceai-llm")
    )
    client = AliceClient(Config)
    response = client.ask_with_mcp(
        message=text,
        model_key=text_model,
        conversation_id=conversation_id,
        params={},
        trace=None,
    )
    reply = client.extract_text(response)
    if not reply:
        raise RuntimeError("voice chat returned no text response")
    return reply.strip()


def _tts(text: str, voice: str) -> bytes:
    if len(text) > 5000:
        text = text[:5000]
    data = {"text": text, "lang": "ru-RU", "voice": voice, "format": "oggopus"}
    folder_id = _folder_id()
    if folder_id:
        data["folderId"] = folder_id
    response = requests.post(
        _TTS_URL,
        headers=_auth_headers(),
        data=data,
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"SpeechKit TTS failed: HTTP {response.status_code}: {response.text[:500]}"
        )
    return response.content


@register_task(VOICE_TASK)
def _process(context: TaskContext) -> dict[str, Any]:
    """Worker handler: STT -> chat -> TTS, publishing events to the job log."""
    payload = context.payload

    def emit(event_type: str, **data: Any) -> None:
        context.emit({"type": event_type, **data})

    output_audio = None
    try:
        audio = base64.b64decode(payload["audio_b64"])
        if not audio:
            raise ValueError("audio is empty")
        emit("input_audio_buffer.speech_stopped")
        transcript = _stt(audio, payload["audio_content_type"])
        if not transcript:
            raise ValueError("speech was not recognized")
        emit("conversation.item.input_audio_transcription.completed", transcript=transcript)

        reply = _chat(transcript, payload.get("conversation_id"), payload["model"])
        if payload["response_mode"] in {"text", "both", "audio"}:
            emit("response.output_text.done", text=reply)

        if payload["response_mode"] in {"audio", "both"}:
            output_audio = _tts(reply, payload["voice"])
            emit(
                "response.output_audio.ready",
                audio_url=f"/api/voice/output?session_id={context.job_id}",
            )

        emit("response.done")
    except Exception as exc:
        emit("error", error={"message": str(exc)[:500]})
        emit("response.done")
    return {
        "output_audio_b64": base64.b64encode(output_audio).decode("ascii") if output_audio else None
    }


def _closed_error():
    return jsonify({"error": "voice session is already closed"}), 409


@voice_bp.post("/api/voice/session")
def create_voice_session():
    try:
        owner_id = get_current_owner_id(required=False)
    except TreasuryIdentityError:
        owner_id = None

    data = request.get_json(silent=True) or {}
    conversation_id = data.get("conversation_id")
    if conversation_id and owner_id and not check_access(conversation_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404

    session = VoiceSession(
        session_id=uuid.uuid4().hex,
        owner_id=owner_id,
        conversation_id=conversation_id,
        model=str(data.get("model") or "speech-realtime-260528"),
        voice=str(data.get("voice") or "filipp"),
        response_mode=str(data.get("response_mode") or "audio"),
    )
    with _sessions_lock:
        _sessions[session.session_id] = session
    return jsonify({"session_id": session.session_id})


@voice_bp.post("/api/voice/audio")
def append_voice_audio():
    try:
        session = _require_session(request.args.get("session_id", ""))
        _check_owner(session)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    chunk = request.get_data(cache=False)
    if not chunk:
        return jsonify({"error": "audio chunk is empty"}), 400
    # Same lock as close, so a chunk is either in the enqueued job or rejected.
    with _sessions_lock:
        if session.closed:
            return _closed_error()
        if len(session.audio) + len(chunk) > _MAX_AUDIO_BYTES:
            return jsonify({"error": "audio exceeds 1 MiB limit"}), 413
        if session.audio and session.audio_content_type != request.content_type:
            return jsonify({"error": "audio content type cannot change within a session"}), 400
        session.audio_content_type = request.content_type or "application/octet-stream"
        session.audio.extend(chunk)
        total = len(session.audio)
    return jsonify({"accepted_bytes": len(chunk), "total_bytes": total})


@voice_bp.get("/api/voice/events")
def voice_events():
    try:
        session = _require_session(request.args.get("session_id", ""))
        _check_owner(session)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403

    queue = get_task_queue()

    # EventSource resends the last seen id on reconnect; resume after it so
    # events such as output_audio.ready are not replayed.
    resume_after = request.headers.get("Last-Event-ID", "")
    start_seq = int(resume_after) if resume_after.isdigit() else 0

    @stream_with_context
    def stream():
        seq = start_seq
        last_write = time.monotonic()
        while True:
            batch = queue.events(session.session_id, after=seq)
            for seq, event in batch:
                yield f"id: {seq}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") == "response.done":
                    return
            if batch:
                last_write = time.monotonic()
                continue
            job = queue.get(session.session_id)
            if job is not None and job.status == FAILED:
                for event in (
                    {"type": "error", "error": {"message": job.error or "voice task failed"}},
                    {"type": "response.done"},
                ):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                return
            if time.monotonic() - last_write >= _SSE_KEEPALIVE_SECONDS:
                yield ": keep-alive\n\n"
                last_write = time.monotonic()
            time.sleep(_SSE_POLL_SECONDS)

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@voice_bp.get("/api/voice/output")
def voice_output():
    try:
        session = _require_session(request.args.get("session_id", ""))
        _check_owner(session)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    job = get_task_queue().get(session.session_id)
    output_audio = ((job.result or {}) if job else {}).get("output_audio_b64")
    if not output_audio:
        return jsonify({"error": "voice output is not ready"}), 404
    return Response(base64.b64decode(output_audio), mimetype="audio/ogg")


@voice_bp.post("/api/voice/close")
def close_voice_session():
    try:
        data = request.get_json(silent=True) or {}
        session = _require_session(str(data.get("session_id") or ""))
        _check_owner(session)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    with _sessions_lock:
        if session.closed:
            return _closed_error()
        session.closed = True
        audio = bytes(session.audio)
        audio_content_type = session.audio_content_type
    # The job id is the session id, so events and output survive a web restart.
    # The session (and its audio) is dropped only once the job is persisted, so a
    # failed enqueue can be retried with the same audio.
    try:
        enqueue(
            VOICE_TASK,
            {
                "owner_id": session.owner_id,
                "conversation_id": session.conversation_id,
                "model": session.model,
                "voice": session.voice,
                "response_mode": session.response_mode,
                "audio_content_type": audio_content_type,
                "audio_b64": base64.b64encode(audio).decode("ascii"),
            },
            job_id=session.session_id,
        )
    except Exception:
        session.closed = False
        logger.exception("Voice session %s could not be enqueued", session.session_id)
        return jsonify({"error": "voice queue is unavailable, retry close"}), 503
    with _sessions_lock:
        _sessions.pop(session.session_id, None)
    return jsonify({"status": "processing"})
