"""Voice assistant HTTP pipeline for SpeechKit STT/TTS and Alice chat."""
from __future__ import annotations

import base64
import json
import os
import queue
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
from treasury_identity import TreasuryIdentityError, get_current_owner_id


voice_bp = Blueprint("voice", __name__)

_MAX_AUDIO_BYTES = 1024 * 1024
_SESSION_TTL_SECONDS = 10 * 60
_STT_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
_TTS_URL = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"


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
    output_audio: bytes | None = None
    events: queue.Queue = field(default_factory=queue.Queue)


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


def _emit(session: VoiceSession, event_type: str, **payload: Any) -> None:
    session.events.put({"type": event_type, **payload})


def _require_session(session_id: str) -> VoiceSession:
    _cleanup_sessions()
    with _sessions_lock:
        session = _sessions.get(session_id)
    if session is None:
        raise KeyError("voice session not found")
    return session


def _check_owner(session: VoiceSession) -> None:
    current_owner = get_current_owner_id(required=False)
    if session.owner_id and current_owner != session.owner_id:
        raise PermissionError("voice session access denied")
    if session.conversation_id and current_owner and not check_access(session.conversation_id, current_owner):
        raise PermissionError("conversation access denied")


def _stt(audio: bytes) -> str:
    if not audio:
        raise ValueError("audio is empty")
    if len(audio) > _MAX_AUDIO_BYTES:
        raise ValueError("audio exceeds 1 MiB limit")

    data: dict[str, str] = {"topic": "general", "lang": "ru-RU"}
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
        raise RuntimeError(f"SpeechKit STT failed: HTTP {response.status_code}: {response.text[:500]}")
    result = response.json().get("result", "")
    if not isinstance(result, str):
        raise RuntimeError("SpeechKit STT returned an invalid result")
    return result.strip()


def _chat(text: str, conversation_id: str | None, model: str) -> str:
    if not conversation_id:
        raise ValueError("conversation_id is required for voice chat")
    text_model = model if model in TEXT_MODELS else os.getenv("ALICE_VOICE_CHAT_MODEL", "aliceai-llm")
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
        raise RuntimeError(f"SpeechKit TTS failed: HTTP {response.status_code}: {response.text[:500]}")
    return response.content


def _process(session: VoiceSession) -> None:
    try:
        _emit(session, "input_audio_buffer.speech_stopped")
        transcript = _stt(bytes(session.audio))
        if not transcript:
            raise ValueError("speech was not recognized")
        _emit(session, "conversation.item.input_audio_transcription.completed", transcript=transcript)

        reply = _chat(transcript, session.conversation_id, session.model)
        if session.response_mode in {"text", "both", "audio"}:
            _emit(session, "response.output_text.done", text=reply)

        if session.response_mode in {"audio", "both"}:
            session.output_audio = _tts(reply, session.voice)
            _emit(session, "response.output_audio.ready", audio_url=f"/api/voice/output?session_id={session.session_id}")

        _emit(session, "response.done")
    except Exception as exc:
        _emit(session, "error", error={"message": str(exc)[:500]})
        _emit(session, "response.done")


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
    if len(session.audio) + len(chunk) > _MAX_AUDIO_BYTES:
        return jsonify({"error": "audio exceeds 1 MiB limit"}), 413
    session.audio.extend(chunk)
    return jsonify({"accepted_bytes": len(chunk), "total_bytes": len(session.audio)})


@voice_bp.get("/api/voice/events")
def voice_events():
    try:
        session = _require_session(request.args.get("session_id", ""))
        _check_owner(session)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403

    @stream_with_context
    def stream():
        while True:
            try:
                event = session.events.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("type") == "response.done":
                break

    return Response(stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@voice_bp.get("/api/voice/output")
def voice_output():
    try:
        session = _require_session(request.args.get("session_id", ""))
        _check_owner(session)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    if not session.output_audio:
        return jsonify({"error": "voice output is not ready"}), 404
    return Response(session.output_audio, mimetype="audio/ogg")


@voice_bp.post("/api/voice/close")
def close_voice_session():
    try:
        session = _require_session(request.get_json(silent=True).get("session_id", ""))
        _check_owner(session)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403

    if not session.audio:
        _emit(session, "error", error={"message": "audio is empty"})
        _emit(session, "response.done")
        return jsonify({"status": "closed"})

    threading.Thread(target=_process, args=(session,), daemon=True).start()
    return jsonify({"status": "processing"})
