"""HTTP/SSE facade for the selected Yandex Realtime WebSocket model."""

from __future__ import annotations

from core.wikifunctions import lowercase_string, trim_string

import asyncio
from contextvars import copy_context
import io
import json
import threading
import time
import uuid
import wave
from dataclasses import dataclass, field
from typing import Any

import requests
from flask import Blueprint, Response, copy_current_request_context, jsonify, request, stream_with_context

from core.config import Config, TEXT_MODELS, VOICE_MODELS, get_model_uri
from chat.mcp_routes import LogosClient, _provider_conversation_id
from conversations.ownership import check_access
from economy.treasury_identity import get_current_owner_id
from provider_accounts.model_discovery import get_model_discovery
from provider_accounts.quotas import ProviderQuotaExceeded, reserve_request
from storage.db import add_message, get_conv_settings, get_messages, maybe_update_conversation_title
from yandex_client_modules.realtime import (
    MODEL_UPSTREAMS,
    OUTPUT_RATE,
    TTS_MODEL,
    RealtimeError,
    RealtimeResult,
    generate_realtime,
)
from yandex_client_modules.request_mixin import _record_quota_usage, _resolve_global_provider_credential


voice_bp = Blueprint("voice", __name__)

# Per-upload memory boundary, returned to the browser so recording stops before it.
_MAX_AUDIO_BYTES = 1024 * 1024
_SESSION_TTL_SECONDS = 10 * 60
_STT_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
_TTS_URL = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
_LIVE_VOICES = {
    "sofia": {"casual", "support"},
    "vera": {"casual", "support"},
    "vasily": {"support", "sales"},
    "sergey": {"support", "sales"},
    "denis": {"casual", "formal", "neutral", "sales"},
    "irina": {"formal", "support", "narrator", "sales", "neutral"},
}
_GENERAL_VOICES = {
    "lea", "john", "naomi", "amira", "madi", "saule", "zhanar", "filipp",
    "ermil", "jane", "omazh", "zahar", "dasha", "julia", "lera", "masha",
    "marina", "alexander", "kirill", "anton", "madi_ru", "saule_ru",
    "zamira_ru", "zhanar_ru", "yulduz_ru", "nigora", "zamira", "yulduz",
}


@dataclass
class VoiceSession:
    session_id: str
    owner_id: str | None
    conversation_id: str
    model: str
    voice: str
    role: str | None
    response_mode: str
    transport: str = "realtime"
    instructions: str | None = None
    created_at: float = field(default_factory=time.time)
    audio: bytearray = field(default_factory=bytearray)
    text: str | None = None
    output_audio: bytes | None = None
    closed: bool = False
    finished: bool = False
    cancelled: threading.Event = field(default_factory=threading.Event)
    events: list[dict] = field(default_factory=list)
    condition: threading.Condition = field(default_factory=threading.Condition)


_sessions: dict[str, VoiceSession] = {}
_sessions_lock = threading.RLock()


def _cleanup_sessions() -> None:
    cutoff = time.time() - _SESSION_TTL_SECONDS
    with _sessions_lock:
        for session_id, session in list(_sessions.items()):
            if session.created_at < cutoff:
                session.cancelled.set()
                with session.condition:
                    session.condition.notify_all()
                _sessions.pop(session_id, None)


def _emit(session: VoiceSession, event_type: str, **payload: Any) -> None:
    with session.condition:
        session.events.append({"type": event_type, **payload})
        if event_type == "response.done":
            session.finished = True
        session.condition.notify_all()


def _require_session(session_id: str) -> VoiceSession:
    _cleanup_sessions()
    with _sessions_lock:
        session = _sessions.get(session_id) if isinstance(session_id, str) else None
    owner_id = get_current_owner_id()
    if (
        session is None
        or session.owner_id != owner_id
        or not check_access(session.conversation_id, owner_id)
    ):
        # Do not reveal the existence of a different owner's session.
        raise KeyError("voice session not found")
    return session


class _PCM16WaveReader(wave.Wave_read):
    """Retain the exact wire codec before wave rounds bit depth to whole bytes."""

    def _read_fmt_chunk(self, chunk) -> None:
        start = chunk.tell()
        header = chunk.read(16)
        if len(header) != 16:
            raise wave.Error("incomplete WAV format header")
        if int.from_bytes(header[:2], "little") != 1 or int.from_bytes(header[14:16], "little") != 16:
            raise wave.Error("WAV format must be integer PCM with exactly 16 bits per sample")
        chunk.seek(start)
        super()._read_fmt_chunk(chunk)


def _wav_pcm(audio: bytes) -> tuple[int, bytes]:
    """Decode the WAV container with wave, retaining the Realtime PCM contract."""
    # wave.readframes may return the available prefix of a truncated RIFF file.
    if len(audio) < 12 or int.from_bytes(audio[4:8], "little") + 8 > len(audio):
        raise ValueError("audio must be a complete PCM16 WAV file")
    try:
        with _PCM16WaveReader(io.BytesIO(audio)) as wav:
            sample_rate = wav.getframerate()
            if (
                wav.getnchannels() != 1
                or wav.getsampwidth() != 2
                or wav.getcomptype() != "NONE"
                or sample_rate not in {8000, 16000, 24000, 44100, 48000}
            ):
                raise ValueError("WAV must be mono PCM16 at 8, 16, 24, 44.1 or 48 kHz")
            frames = wav.getnframes()
            # One extra frame exposes an odd final byte, which getnframes() rounds down.
            pcm = wav.readframes(frames + 1)
            if not frames or len(pcm) != frames * 2:
                raise ValueError("WAV audio samples are empty or truncated")
            return sample_rate, pcm
    except (wave.Error, EOFError, OSError) as exc:
        raise ValueError("audio must be a complete PCM16 WAV file") from exc


def _output_wav(pcm: bytes, sample_rate: int = OUTPUT_RATE) -> bytes:
    if len(pcm) % 2:
        raise RealtimeError("Realtime вернул неполный PCM-сэмпл")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()


def _speechkit_turn(session: VoiceSession, credential, pcm: bytes, sample_rate: int,
                    provider_event) -> RealtimeResult:
    """Explicit text-model microphone transport, preserving its existing tool loop."""
    if sample_rate not in {8000, 16000, 48000} or len(pcm) > sample_rate * 2 * 30:
        raise RealtimeError("SpeechKit v1 принимает PCM 8/16/48 кГц длительностью не более 30 секунд")
    headers = {"Authorization": "Api-Key " + credential.api_key}
    recognized = requests.post(
        _STT_URL,
        headers=headers,
        params={"topic": "general", "lang": "ru-RU", "format": "lpcm",
                "sampleRateHertz": str(sample_rate), "folderId": credential.project_id},
        data=pcm, timeout=30,
    )
    if not recognized.ok:
        raise RealtimeError(f"SpeechKit не распознал запись: HTTP {recognized.status_code}")
    recognized_data = recognized.json()
    if not isinstance(recognized_data, dict) or "error" in recognized_data:
        raise RealtimeError("SpeechKit вернул ошибку распознавания")
    value = recognized_data.get("result")
    if not isinstance(value, str):
        raise RealtimeError("SpeechKit не распознал речь")
    transcript = trim_string(value)
    if not transcript:
        raise RealtimeError("SpeechKit не распознал речь")
    provider_event("conversation.item.input_audio_transcription.completed", transcript=transcript)
    if session.cancelled.is_set():
        raise RealtimeError("Голосовая сессия отменена")
    client = LogosClient(Config)
    settings = get_conv_settings(session.conversation_id) or {}
    params = {"instructions": session.instructions} if session.instructions else {}
    if settings.get("active_tool_categories") is not None:
        params["active_tool_categories"] = settings["active_tool_categories"]
    response = client.ask_with_mcp(
        message=transcript, model_key=session.model,
        conversation_id=_provider_conversation_id(client, session.conversation_id, None),
        settings_conversation_id=session.conversation_id,
        params=params, trace=None,
    )
    if not isinstance(response, dict):
        raise RealtimeError("Текстовая модель вернула некорректный ответ")
    result = RealtimeResult(
        transcript=transcript, reply=client.extract_text(response) or "",
        usage=client.extract_usage(response) or {}, status=str(response.get("status") or "failed"),
        response_id=response.get("id"),
    )
    if "error" in response:
        result.status = "failed"
    if any(item.get("type") == "function_call" for item in (response.get("output") or []) if isinstance(item, dict)):
        result.status = "incomplete"
    if session.response_mode != "text" and result.reply and result.status == "completed":
        # SpeechKit v1 accepts at most 5000 characters per synthesis request.
        # Concatenate raw PCM chunks so a long reply is never silently truncated.
        for offset in range(0, len(result.reply), 5000):
            if session.cancelled.is_set():
                raise RealtimeError("Голосовая сессия отменена")
            try:
                synthesis = {"text": result.reply[offset:offset + 5000], "lang": "ru-RU",
                             "voice": session.voice, "format": "lpcm", "sampleRateHertz": "48000",
                             "folderId": credential.project_id}
                if session.role:
                    synthesis["emotion"] = session.role
                spoken = requests.post(
                    _TTS_URL, headers=headers,
                    data=synthesis,
                    timeout=30,
                )
            except requests.RequestException:
                result.error_message = "Текстовый ответ получен, но SpeechKit недоступен для озвучивания"
                return result
            if not spoken.ok:
                result.error_message = f"Текстовый ответ получен, но SpeechKit не синтезировал аудио: HTTP {spoken.status_code}"
                return result
            lowercase_string((spoken.headers.get("Content-Type") or "")).startswith("application/json"):
                result.error_message = "SpeechKit вернул ошибку вместо PCM-аудио"
                return result
            result.audio.extend(spoken.content)
    return result


def _process(session: VoiceSession) -> None:
    transcript = session.text or ""
    input_saved = False

    def save_input(value: str) -> None:
        nonlocal transcript, input_saved
        transcript = value
        if value and not input_saved and check_access(session.conversation_id, session.owner_id):
            add_message(session.conversation_id, "user", value, source="voice")
            maybe_update_conversation_title(session.conversation_id, value)
            input_saved = True

    def provider_event(event_type: str, **payload: Any) -> None:
        if event_type == "conversation.item.input_audio_transcription.completed":
            save_input(str(payload.get("transcript") or ""))
        _emit(session, event_type, **payload)

    try:
        if not check_access(session.conversation_id, session.owner_id):
            raise RealtimeError("Диалог больше недоступен")
        history = get_messages(session.conversation_id) if session.model != TTS_MODEL else []
        credential = _resolve_global_provider_credential(LogosClient(Config))
        try:
            input_rate, pcm = (16000, b"") if session.text is not None else _wav_pcm(bytes(session.audio))
        except ValueError as exc:
            raise RealtimeError(str(exc)) from None
        # Release the WAV container once the provider has its raw PCM input.
        session.audio.clear()
        save_input(transcript)
        _emit(session, "input_audio_buffer.speech_stopped")
        if session.transport == "speechkit_pipeline":
            result = _speechkit_turn(session, credential, pcm, input_rate, provider_event)
        else:
            # Apply the existing configured quota to the actual Realtime request,
            # once per turn. Unknown network outcomes retain their admission.
            reservation = reserve_request(session.owner_id)
            result = asyncio.run(generate_realtime(
                api_key=credential.api_key,
                folder_id=credential.project_id,
                model=session.model,
                voice=session.voice,
                role=session.role,
                response_mode=session.response_mode,
                text=session.text,
                pcm=pcm,
                input_rate=input_rate,
                history=history,
                instructions=session.instructions,
                emit=provider_event,
                cancelled=session.cancelled.is_set,
                timeout=max(1, session.created_at + _SESSION_TTL_SECONDS - time.time()),
            ))
            if result.usage:
                _record_quota_usage(None, reservation, {"usage": result.usage, "id": result.response_id})
        if session.cancelled.is_set():
            raise RealtimeError("Голосовая сессия отменена")
        if not check_access(session.conversation_id, session.owner_id):
            raise RealtimeError("Диалог больше недоступен")
        save_input(result.transcript)
        final_status = result.status
        if result.error_message:
            final_status = "incomplete"
            _emit(session, "error", error={"message": result.error_message})
        if not input_saved and session.text is None:
            # Keep an actual user turn before the assistant even if independent
            # transcription fails after a completed audio response.
            add_message(session.conversation_id, "user", "[Голосовое сообщение: расшифровка недоступна]",
                        source="voice_transcription_unavailable",
                        attachments={"audio": {"bytes": len(pcm), "sample_rate": input_rate,
                                               "transcription_status": result.transcription_status}})
            input_saved = True
            final_status = "incomplete"
            _emit(session, "error", error={"message": "Ответ получен, но расшифровка вашего аудио недоступна"})
        if result.reply:
            add_message(
                session.conversation_id,
                "assistant",
                result.reply,
                cost=None,
                usage=result.usage,
                source="voice" if final_status == "completed" else "voice_partial",
                trace={"voice_session_id": session.session_id, "model": session.model,
                       "provider_response_id": result.response_id, "provider_status": result.status,
                       "transport": session.transport, "transcription_status": result.transcription_status},
            )
            _emit(session, "response.output_text.done", text=result.reply, response_id=result.response_id)
        elif result.audio:
            add_message(
                session.conversation_id, "assistant", "[Голосовой ответ]", cost=None,
                usage=result.usage, source="voice_audio",
                attachments={"audio": {"bytes": len(result.audio), "sample_rate": OUTPUT_RATE}},
                trace={"voice_session_id": session.session_id, "model": session.model,
                       "provider_response_id": result.response_id, "provider_status": result.status},
            )
            _emit(session, "response.output_text.done", text="[Голосовой ответ]", response_id=result.response_id)
        if result.audio and session.response_mode != "text":
            session.output_audio = _output_wav(bytes(result.audio), 48000 if session.transport == "speechkit_pipeline" else OUTPUT_RATE)
            _emit(session, "response.output_audio.ready", audio_url=f"/api/voice/output?session_id={session.session_id}")
        if result.status != "completed":
            _emit(session, "error", error={"message": "Модель не завершила ответ", "status": result.status})
        elif not result.reply and not result.audio:
            raise RealtimeError("Realtime завершился без текста и аудио")
        _emit(session, "response.done", status=final_status, usage=result.usage, model=session.model,
              conversation_id=session.conversation_id, response_id=result.response_id)
    except ProviderQuotaExceeded as exc:
        _emit(session, "error", error={**exc.to_dict()["error"], "message": "Достигнут установленный лимит запросов провайдера"})
        _emit(session, "response.done", status="failed")
    except Exception as exc:
        # Never return raw websocket/credential exceptions: their URL may contain
        # provider metadata and their headers may contain authorization material.
        message = str(exc) if isinstance(exc, RealtimeError) else "Не удалось выполнить голосовой запрос к провайдеру"
        _emit(session, "error", error={"message": message})
        _emit(session, "response.done", status="cancelled" if session.cancelled.is_set() else "failed")
    finally:
        session.audio.clear()
        session.text = None


def _start_processing(session: VoiceSession):
    with _sessions_lock:
        if session.cancelled.is_set():
            return jsonify({"error": "voice_session_cancelled"}), 409
        if session.closed:
            return jsonify({"status": "completed" if session.finished else "processing"})
        if session.text is None and not session.audio:
            return jsonify({"error": "text or PCM WAV audio is required"}), 400
        session.closed = True

    context = copy_context()

    @copy_current_request_context
    def process_in_context():
        _process(session)

    threading.Thread(target=lambda: context.run(process_in_context), daemon=True).start()
    return jsonify({"status": "processing"})


@voice_bp.post("/api/voice/session")
def create_voice_session():
    _cleanup_sessions()
    owner_id = get_current_owner_id()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "json_body_required"}), 400
    conversation_id = data.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id or not check_access(conversation_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    model = data.get("model") or "speech-realtime-deepseek-v4-flash"
    transport = data.get("transport") or "realtime"
    if not isinstance(transport, str) or transport not in {"realtime", "speechkit_pipeline"}:
        return jsonify({"error": "unsupported_voice_transport"}), 400
    if transport == "speechkit_pipeline":
        if not isinstance(model, str) or not model or TEXT_MODELS.get(model, {}).get("available") is False:
            return jsonify({"error": "select_an_available_text_model_for_speechkit_pipeline"}), 400
    elif not isinstance(model, str) or model not in VOICE_MODELS or model not in MODEL_UPSTREAMS:
        return jsonify({"error": "select_a_realtime_voice_model"}), 400
    live = transport == "realtime" and model != "speech-realtime-deepseek-v4-flash"
    voice = data.get("voice") or ("sofia" if live else "filipp")
    role = data.get("role")
    if not isinstance(voice, str) or voice not in (_LIVE_VOICES if live else _GENERAL_VOICES):
        return jsonify({"error": "voice_is_not_compatible_with_model"}), 400
    if role is not None and (not isinstance(role, str) or (live and role not in _LIVE_VOICES[voice])):
        return jsonify({"error": "role_is_not_compatible_with_voice"}), 400
    mode = data.get("response_mode") or "audio"
    if not isinstance(mode, str) or mode not in {"audio", "both", "text"}:
        return jsonify({"error": "response_mode_must_be_audio_both_or_text"}), 400
    if model == TTS_MODEL and mode == "text":
        return jsonify({"error": "speech_tts_live_requires_audio_output"}), 400
    settings = get_conv_settings(conversation_id) or {}
    if not isinstance(settings, dict):
        return jsonify({"error": "invalid_conversation_settings"}), 400
    instructions = data.get("instructions", settings.get("instructions"))
    if instructions is not None and not isinstance(instructions, str):
        return jsonify({"error": "instructions_must_be_text"}), 400
    try:
        credential = _resolve_global_provider_credential(LogosClient(Config))
    except Exception:
        return jsonify({"error": "provider_credential_unavailable"}), 503
    if transport == "speechkit_pipeline":
        try:
            get_model_uri(credential.project_id, model)
        except ValueError:
            return jsonify({"error": "invalid_text_model_uri"}), 400
        if model not in TEXT_MODELS:
            try:
                discovered = get_model_discovery().get_models().get("text", {}).get(model)
            except Exception:
                return jsonify({"error": "text_model_catalog_unavailable"}), 503
            if not discovered or discovered.get("available") is False:
                return jsonify({"error": "select_an_available_discovered_text_model"}), 400
    session = VoiceSession(
        session_id=uuid.uuid4().hex, owner_id=owner_id,
        conversation_id=conversation_id, model=model, voice=voice, role=role,
        response_mode=mode, transport=transport, instructions=instructions,
    )
    with _sessions_lock:
        _sessions[session.session_id] = session
    return jsonify({"session_id": session.session_id, "model": model, "voice": voice, "transport": transport,
                    "input_audio_format": "audio/wav", "input_sample_rate": 16000,
                    "max_audio_bytes": min(_MAX_AUDIO_BYTES, 16000 * 2 * 30 + 44) if transport == "speechkit_pipeline" else _MAX_AUDIO_BYTES,
                    "ttl_seconds": _SESSION_TTL_SECONDS,
                    "audio_input": model != TTS_MODEL, "text_input": transport == "realtime"})


@voice_bp.post("/api/voice/audio")
def append_voice_audio():
    try:
        session = _require_session(request.args.get("session_id", ""))
    except KeyError:
        return jsonify({"error": "voice_session_not_found"}), 404
    if session.model == TTS_MODEL:
        return jsonify({"error": "speech_tts_live_requires_text"}), 400
    if not (request.content_type or "").startswith(("audio/wav", "audio/x-wav")):
        return jsonify({"error": "realtime_requires_pcm16_wav_audio"}), 415
    # Read one byte beyond the available space to detect oversize uploads without
    # loading an arbitrary request body into server memory.
    with _sessions_lock:
        if session.closed or session.text is not None:
            return jsonify({"error": "voice_session_input_closed"}), 409
        room = _MAX_AUDIO_BYTES - len(session.audio)
    chunk = request.stream.read(room + 1)
    with _sessions_lock:
        # Concurrent uploads/close may have changed the session while the network
        # body arrived. No slow client may hold the global session lock.
        if session.closed or session.text is not None or session.cancelled.is_set():
            return jsonify({"error": "voice_session_input_closed"}), 409
        room = _MAX_AUDIO_BYTES - len(session.audio)
        if not chunk:
            return jsonify({"error": "audio_chunk_is_empty"}), 400
        if len(chunk) > room:
            return jsonify({"error": "audio_exceeds_session_upload_limit", "max_audio_bytes": _MAX_AUDIO_BYTES}), 413
        session.audio.extend(chunk)
        total = len(session.audio)
    return jsonify({"accepted_bytes": len(chunk), "total_bytes": total})


@voice_bp.post("/api/voice/text")
def submit_voice_text():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "json_body_required"}), 400
    try:
        session = _require_session(data.get("session_id", ""))
    except KeyError:
        return jsonify({"error": "voice_session_not_found"}), 404
    text = data.get("text")
    if not isinstance(text, str) or not trim_string(text):
        return jsonify({"error": "text_is_required"}), 400
    if session.transport != "realtime":
        return jsonify({"error": "speechkit_pipeline_requires_microphone_audio"}), 400
    with _sessions_lock:
        if session.closed or session.audio or session.text is not None:
            return jsonify({"error": "voice_session_input_closed"}), 409
        session.text = text
    return _start_processing(session)


@voice_bp.get("/api/voice/events")
def voice_events():
    try:
        session = _require_session(request.args.get("session_id", ""))
    except KeyError:
        return jsonify({"error": "voice_session_not_found"}), 404
    try:
        start_index = max(0, int(request.headers.get("Last-Event-ID", "0")))
    except ValueError:
        start_index = 0

    @stream_with_context
    def stream():
        index = start_index
        while True:
            if time.time() >= session.created_at + _SESSION_TTL_SECONDS or session.cancelled.is_set():
                return
            with session.condition:
                if index >= len(session.events) and not session.finished:
                    session.condition.wait(timeout=15)
                pending = list(session.events[index:])
                done = session.finished
            if not pending:
                if done:
                    return
                yield ": keep-alive\n\n"
                continue
            for event in pending:
                index += 1
                yield f"id: {index}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") == "response.done":
                    return

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@voice_bp.get("/api/voice/output")
def voice_output():
    try:
        session = _require_session(request.args.get("session_id", ""))
    except KeyError:
        return jsonify({"error": "voice_session_not_found"}), 404
    if not session.output_audio:
        return jsonify({"error": "voice_output_not_ready"}), 404
    return Response(session.output_audio, mimetype="audio/wav", headers={"Cache-Control": "no-store"})


@voice_bp.post("/api/voice/close")
def close_voice_session():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "json_body_required"}), 400
    try:
        session = _require_session(data.get("session_id", ""))
    except KeyError:
        return jsonify({"error": "voice_session_not_found"}), 404
    if data.get("cancel") is True:
        session.cancelled.set()
        with session.condition:
            session.condition.notify_all()
        if not session.closed:
            session.audio.clear()
            session.text = None
        return jsonify({"status": "cancelled"})
    return _start_processing(session)
