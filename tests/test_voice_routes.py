import json
import queue
import struct
import time

from typing import ClassVar

import pytest

import chat.voice_routes as voice_routes
from app import app


@pytest.fixture(autouse=True)
def voice_memory(tmp_path, monkeypatch):
    from agent_memory.client import close_memory_clients

    monkeypatch.setenv("LOGOS_MEMORY_PATH", str(tmp_path / "voice.memory"))
    close_memory_clients()
    yield
    close_memory_clients()


def _wav():
    samples = b"\x00\x00" * 100
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(samples))
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
        + b"data"
        + struct.pack("<I", len(samples))
        + samples
    )


@pytest.fixture()
def client(monkeypatch):
    from storage.db import create_conversation

    monkeypatch.setenv("YANDEX_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_PROJECT_ID", "test-project")
    create_conversation("voice-conv", "Voice", "yandexgpt-5-lite")
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c
    with voice_routes._sessions_lock:
        voice_routes._sessions.clear()


def test_voice_session_lifecycle_and_pipeline(client, monkeypatch):
    calls = []

    def fake_stt(audio, content_type="application/octet-stream"):
        calls.append(("stt", audio))
        return "Привет"

    def fake_chat(text, conversation_id, model):
        calls.append(("chat", text, conversation_id, model))
        return "Здравствуйте!"

    def fake_tts(text, voice):
        calls.append(("tts", text, voice))
        return b"OggOpus"

    monkeypatch.setattr(voice_routes, "_stt", fake_stt)
    monkeypatch.setattr(voice_routes, "_chat", fake_chat)
    monkeypatch.setattr(voice_routes, "_tts", fake_tts)

    session = client.post(
        "/api/voice/session",
        json={
            "conversation_id": "voice-conv",
            "model": "speech-realtime-250923",
            "voice": "filipp",
            "response_mode": "audio",
        },
    )
    assert session.status_code == 200
    session_id = session.get_json()["session_id"]

    uploaded = client.post(
        f"/api/voice/audio?session_id={session_id}",
        data=_wav(),
        content_type="audio/wav",
    )
    assert uploaded.status_code == 200

    closed = client.post("/api/voice/close", json={"session_id": session_id})
    assert closed.status_code == 200

    deadline = time.time() + 2
    events = []
    while time.time() < deadline:
        event = voice_routes._require_session(session_id).events.get(timeout=0.2)
        events.append(event)
        if event["type"] == "response.done":
            break

    assert [event["type"] for event in events] == [
        "input_audio_buffer.speech_stopped",
        "conversation.item.input_audio_transcription.completed",
        "response.output_text.done",
        "response.output_audio.ready",
        "response.done",
    ]
    assert voice_routes._require_session(session_id).output_audio == b"OggOpus"
    assert calls[1][0] == "chat"
    assert calls[1][1] == "Привет"


def test_voice_audio_size_limit(client):
    session = client.post("/api/voice/session", json={}).get_json()["session_id"]
    response = client.post(
        f"/api/voice/audio?session_id={session}",
        data=b"x" * (voice_routes._MAX_AUDIO_BYTES + 1),
        content_type="application/octet-stream",
    )
    assert response.status_code == 413


def test_first_voice_session_has_a_durable_local_conversation(client):
    import storage.db as db

    response = client.post("/api/voice/session", json={"model": "speech-realtime-250923"})

    assert response.status_code == 200
    data = response.get_json()
    assert data["conversation_id"]
    assert voice_routes._require_session(data["session_id"]).conversation_id == data["conversation_id"]
    assert any(item["id"] == data["conversation_id"] for item in db.get_conversations())
    assert db.is_provider_pending(data["conversation_id"])


def test_completed_voice_stream_replays_terminal_event_after_reconnect(client, monkeypatch):
    session_id = client.post("/api/voice/session", json={}).get_json()["session_id"]
    session = voice_routes._require_session(session_id)
    session.processing_done = True

    def empty_queue(**kwargs):
        raise queue.Empty

    monkeypatch.setattr(session.events, "get", empty_queue)
    response = client.get(f"/api/voice/events?session_id={session_id}")

    assert response.status_code == 200
    assert '"type": "response.done"' in response.get_data(as_text=True)


def test_voice_events_is_sse(client, monkeypatch):
    monkeypatch.setattr(
        voice_routes, "_stt", lambda audio, content_type="application/octet-stream": "тест"
    )
    monkeypatch.setattr(voice_routes, "_chat", lambda text, conversation_id, model: "ответ")
    monkeypatch.setattr(voice_routes, "_tts", lambda text, voice: b"audio")

    session_id = client.post("/api/voice/session", json={"response_mode": "text", "conversation_id": "voice-conv"}).get_json()[
        "session_id"
    ]
    client.post(f"/api/voice/audio?session_id={session_id}", data=b"audio")
    client.post("/api/voice/close", json={"session_id": session_id})

    response = client.get(f"/api/voice/events?session_id={session_id}")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert "conversation.item.input_audio_transcription.completed" in body
    assert "response.output_text.done" in body
    assert "response.done" in body


class _HttpResponse:
    def __init__(self, status_code, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self.content = b"audio"
        self._payload = payload or {}

    def json(self):
        return self._payload


@pytest.mark.parametrize(
    ("call", "label"),
    [
        (lambda: voice_routes._stt(b"ogg-bytes", "audio/ogg"), "STT"),
        (lambda: voice_routes._tts("привет", "filipp"), "TTS"),
    ],
)
def test_speechkit_http_errors_are_raised(monkeypatch, call, label):
    monkeypatch.setenv("YANDEX_API_KEY", "test-key")
    monkeypatch.setattr(
        voice_routes.requests, "post", lambda *a, **k: _HttpResponse(503, "unavailable")
    )
    with pytest.raises(RuntimeError, match=f"SpeechKit {label} failed: HTTP 503"):
        call()


class _FakeLogosClient:
    reply = " Ответ "
    calls: ClassVar[list] = []

    def __init__(self, config):
        pass

    def ask_with_mcp(self, **kwargs):
        self.calls.append(kwargs)
        return {"output": []}

    def extract_text(self, response):
        return self.reply


def test_voice_chat_uses_text_model_and_conversation(monkeypatch):
    monkeypatch.setattr(voice_routes, "LogosClient", _FakeLogosClient)
    monkeypatch.setattr(_FakeLogosClient, "calls", [])
    monkeypatch.setattr(voice_routes, "_provider_conversation_id", lambda client, conv_id, trace: conv_id)
    monkeypatch.setenv("LOGOS_VOICE_CHAT_MODEL", "yandexgpt-5-lite")

    assert voice_routes._chat("вопрос", "conv-1", "speech-realtime-250923") == "Ответ"
    call = _FakeLogosClient.calls[0]
    assert call["model_key"] == "yandexgpt-5-lite"
    assert call["conversation_id"] == "conv-1"


def test_voice_chat_requires_conversation_and_text_reply(monkeypatch):
    monkeypatch.setattr(voice_routes, "LogosClient", _FakeLogosClient)
    monkeypatch.setattr(voice_routes, "_provider_conversation_id", lambda client, conv_id, trace: conv_id)
    with pytest.raises(ValueError, match="conversation_id is required"):
        voice_routes._chat("вопрос", None, "yandexgpt-5-lite")
    monkeypatch.setattr(_FakeLogosClient, "reply", "")
    with pytest.raises(RuntimeError, match="no text response"):
        voice_routes._chat("вопрос", "conv-1", "yandexgpt-5-lite")


def test_voice_chat_sends_the_provider_conversation(monkeypatch):
    monkeypatch.setattr(voice_routes, "LogosClient", _FakeLogosClient)
    monkeypatch.setattr(_FakeLogosClient, "calls", [])
    monkeypatch.setattr(
        voice_routes, "_provider_conversation_id", lambda client, conv_id, trace: "provider-9"
    )

    voice_routes._chat("вопрос", "local-1", "yandexgpt-5-lite")
    assert _FakeLogosClient.calls[0]["conversation_id"] == "provider-9"
    assert _FakeLogosClient.calls[0]["settings_conversation_id"] == "local-1"


def test_voice_credentials_prefer_the_stored_key(monkeypatch):
    import storage.db as db
    from provider_accounts.credentials import YANDEX, replace_active_credential

    replace_active_credential(
        db.get_store(),
        "stored-voice-key",
        "stored-folder",
        lambda value: "cipher:" + value,
        YANDEX,
        provider_key_id="voice-key-1",
    )
    monkeypatch.setattr(voice_routes, "decrypt_secret", lambda value: value.removeprefix("cipher:"))
    monkeypatch.setenv("YANDEX_API_KEY", "env-key")

    assert voice_routes._credentials() == ("stored-voice-key", None, "stored-folder")
    assert voice_routes._folder_id() == "stored-folder"


def test_voice_exchange_is_saved_to_its_conversation(monkeypatch):
    import storage.db as db

    monkeypatch.setattr(voice_routes, "_stt", lambda audio, content_type: "вопрос")
    monkeypatch.setattr(voice_routes, "_chat", lambda text, conversation_id, model: "ответ")
    db.create_conversation("conv-saved", "Voice", "yandexgpt-5-lite")
    session = voice_routes.VoiceSession(
        session_id="saved-session",
        owner_id=None,
        conversation_id="conv-saved",
        model="yandexgpt-5-lite",
        voice="filipp",
        response_mode="text",
    )

    voice_routes._process(session)

    saved = {(item["role"], item["text"]) for item in db.get_messages("conv-saved")}
    assert {("user", "вопрос"), ("assistant", "ответ")} <= saved


def test_recognized_question_survives_model_failure(monkeypatch):
    import storage.db as db

    db.create_conversation("voice-failed", "Voice", "yandexgpt-5-lite")
    monkeypatch.setattr(voice_routes, "_stt", lambda audio, content_type: "Сохранённый вопрос")

    def fail_chat(*args):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(voice_routes, "_chat", fail_chat)
    session = voice_routes.VoiceSession(
        session_id="failed-session", owner_id=None, conversation_id="voice-failed",
        model="yandexgpt-5-lite", voice="filipp", response_mode="text",
    )

    voice_routes._process(session)

    saved = db.get_messages("voice-failed")
    assert [(item["role"], item["text"]) for item in saved] == [("user", "Сохранённый вопрос")]
