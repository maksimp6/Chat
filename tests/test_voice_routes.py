import json
import struct
import time

from typing import ClassVar

import pytest

import voice_routes
from app import app


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
    monkeypatch.setenv("YANDEX_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_PROJECT_ID", "test-project")
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
            "conversation_id": None,
            "model": "speech-realtime-260528",
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


def test_voice_events_is_sse(client, monkeypatch):
    monkeypatch.setattr(
        voice_routes, "_stt", lambda audio, content_type="application/octet-stream": "тест"
    )
    monkeypatch.setattr(voice_routes, "_chat", lambda text, conversation_id, model: "ответ")
    monkeypatch.setattr(voice_routes, "_tts", lambda text, voice: b"audio")

    session_id = client.post("/api/voice/session", json={"response_mode": "text"}).get_json()[
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


class _FakeAliceClient:
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
    monkeypatch.setattr(voice_routes, "AliceClient", _FakeAliceClient)
    monkeypatch.setattr(_FakeAliceClient, "calls", [])
    monkeypatch.setenv("ALICE_VOICE_CHAT_MODEL", "aliceai-llm")

    assert voice_routes._chat("вопрос", "conv-1", "speech-realtime-260528") == "Ответ"
    call = _FakeAliceClient.calls[0]
    assert call["model_key"] == "aliceai-llm"
    assert call["conversation_id"] == "conv-1"


def test_voice_chat_requires_conversation_and_text_reply(monkeypatch):
    monkeypatch.setattr(voice_routes, "AliceClient", _FakeAliceClient)
    with pytest.raises(ValueError, match="conversation_id is required"):
        voice_routes._chat("вопрос", None, "aliceai-llm")
    monkeypatch.setattr(_FakeAliceClient, "reply", "")
    with pytest.raises(RuntimeError, match="no text response"):
        voice_routes._chat("вопрос", "conv-1", "aliceai-llm")
