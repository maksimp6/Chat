import json
import struct
import time

import pytest

import voice_routes
from app import app


def _wav():
    samples = b"\x00\x00" * 100
    return (
        b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
        + b"data" + struct.pack("<I", len(samples)) + samples
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

    def fake_stt(audio):
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

    session = client.post("/api/voice/session", json={
        "conversation_id": None,
        "model": "speech-realtime-260528",
        "voice": "filipp",
        "response_mode": "audio",
    })
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
    monkeypatch.setattr(voice_routes, "_stt", lambda audio: "тест")
    monkeypatch.setattr(voice_routes, "_chat", lambda text, conversation_id, model: "ответ")
    monkeypatch.setattr(voice_routes, "_tts", lambda text, voice: b"audio")

    session_id = client.post("/api/voice/session", json={"response_mode": "text"}).get_json()["session_id"]
    client.post(f"/api/voice/audio?session_id={session_id}", data=b"audio")
    client.post("/api/voice/close", json={"session_id": session_id})

    response = client.get(f"/api/voice/events?session_id={session_id}")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert "conversation.item.input_audio_transcription.completed" in body
    assert "response.output_text.done" in body
    assert "response.done" in body
