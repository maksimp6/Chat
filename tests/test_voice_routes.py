import json
import struct
import time

from typing import ClassVar

import pytest

import db
import voice_routes
from app import app
from tasks import reset_task_queue, run_worker


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
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("YANDEX_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_PROJECT_ID", "test-project")
    # Jobs go to a durable SQLite queue and run only when a worker drains it.
    monkeypatch.setenv("ALICE_TASK_WORKER", "external")
    monkeypatch.delenv("ALICE_TASK_QUEUE", raising=False)
    monkeypatch.delenv("ALICE_REDIS_URL", raising=False)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "voice.db"))
    monkeypatch.setattr(voice_routes, "_SSE_POLL_SECONDS", 0.01)
    reset_task_queue()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c
    with voice_routes._sessions_lock:
        voice_routes._sessions.clear()
    reset_task_queue()


def _sse_events(response):
    return [
        json.loads(line[len("data: ") :])
        for line in response.get_data(as_text=True).splitlines()
        if line.startswith("data: ")
    ]


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
    assert client.get(f"/api/voice/output?session_id={session_id}").status_code == 404

    # The web process only enqueued the job; a separate worker runs it.
    assert calls == []
    assert run_worker(drain=True) == 1

    events = _sse_events(client.get(f"/api/voice/events?session_id={session_id}"))
    assert [event["type"] for event in events] == [
        "input_audio_buffer.speech_stopped",
        "conversation.item.input_audio_transcription.completed",
        "response.output_text.done",
        "response.output_audio.ready",
        "response.done",
    ]
    output = client.get(f"/api/voice/output?session_id={session_id}")
    assert output.status_code == 200
    assert output.data == b"OggOpus"
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
    run_worker(drain=True)

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


def _closed_session(client, audio=b"audio"):
    session_id = client.post("/api/voice/session", json={"response_mode": "text"}).get_json()[
        "session_id"
    ]
    if audio:
        client.post(f"/api/voice/audio?session_id={session_id}", data=audio)
    assert client.post("/api/voice/close", json={"session_id": session_id}).status_code == 200
    return session_id


def test_voice_job_survives_web_restart(client, monkeypatch):
    monkeypatch.setattr(
        voice_routes, "_stt", lambda audio, content_type="application/octet-stream": "тест"
    )
    monkeypatch.setattr(voice_routes, "_chat", lambda text, conversation_id, model: "ответ")
    session_id = _closed_session(client)

    # Simulate a web container restart: in-process state and queue objects are gone.
    with voice_routes._sessions_lock:
        voice_routes._sessions.clear()
    reset_task_queue()

    assert run_worker(drain=True) == 1
    events = _sse_events(client.get(f"/api/voice/events?session_id={session_id}"))
    assert events[-2] == {"type": "response.output_text.done", "text": "ответ"}
    assert events[-1] == {"type": "response.done"}


def test_voice_empty_audio_reports_error(client):
    session_id = _closed_session(client, audio=b"")
    run_worker(drain=True)
    events = _sse_events(client.get(f"/api/voice/events?session_id={session_id}"))
    assert events == [
        {"type": "error", "error": {"message": "audio is empty"}},
        {"type": "response.done"},
    ]


def test_voice_closed_session_rejects_audio_and_second_close(client):
    session_id = _closed_session(client)
    response = client.post(f"/api/voice/audio?session_id={session_id}", data=b"more")
    assert response.status_code == 409
    response = client.post("/api/voice/close", json={"session_id": session_id})
    assert response.status_code == 409


def test_voice_unknown_session_is_not_found(client):
    assert client.post("/api/voice/close", json={}).status_code == 404
    assert client.get("/api/voice/events?session_id=missing").status_code == 404


def test_voice_non_voice_job_is_not_a_session(client):
    job_id = voice_routes.get_task_queue().enqueue("other.task", {})
    assert client.get(f"/api/voice/output?session_id={job_id}").status_code == 404


def test_voice_events_report_failed_job_and_keep_alive(client, monkeypatch):
    monkeypatch.setattr(voice_routes, "_SSE_KEEPALIVE_SECONDS", 0.0)
    session_id = _closed_session(client)
    queue = voice_routes.get_task_queue()
    original_get = queue.get
    polls = []

    def get_after_worker_died(job_id):
        # Session lookup and the first stream poll see a queued job, a worker
        # reports progress, and then that worker dies for good.
        polls.append(job_id)
        if len(polls) == 2:
            queue.append_event(job_id, {"type": "input_audio_buffer.speech_stopped"})
        if len(polls) == 3:
            job = queue.claim("dead-worker", lease_seconds=60)
            queue.fail(job, "worker lease expired too many times")
        return original_get(job_id)

    monkeypatch.setattr(queue, "get", get_after_worker_died)
    body = client.get(f"/api/voice/events?session_id={session_id}").get_data(as_text=True)
    assert body.startswith(": keep-alive")
    assert "input_audio_buffer.speech_stopped" in body
    assert '"message": "worker lease expired too many times"' in body
    assert body.rstrip().endswith('data: {"type": "response.done"}')


def test_voice_close_keeps_audio_when_enqueue_fails(client, monkeypatch):
    monkeypatch.setattr(voice_routes, "_stt", lambda audio, content_type="x": audio.decode())
    monkeypatch.setattr(voice_routes, "_chat", lambda text, conversation_id, model: text)
    session_id = client.post("/api/voice/session", json={"response_mode": "text"}).get_json()[
        "session_id"
    ]
    client.post(f"/api/voice/audio?session_id={session_id}", data=b"original")
    real_enqueue = voice_routes.enqueue

    def enqueue_down(*args, **kwargs):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(voice_routes, "enqueue", enqueue_down)
    failed = client.post("/api/voice/close", json={"session_id": session_id})
    assert failed.status_code == 503
    assert "redis is down" in failed.get_json()["error"]

    monkeypatch.setattr(voice_routes, "enqueue", real_enqueue)
    assert client.post("/api/voice/close", json={"session_id": session_id}).status_code == 200
    run_worker(drain=True)
    events = _sse_events(client.get(f"/api/voice/events?session_id={session_id}"))
    assert {"type": "response.output_text.done", "text": "original"} in events


def test_voice_events_resume_after_last_event_id(client, monkeypatch):
    monkeypatch.setattr(voice_routes, "_stt", lambda audio, content_type="x": "тест")
    monkeypatch.setattr(voice_routes, "_chat", lambda text, conversation_id, model: "ответ")
    session_id = _closed_session(client)
    run_worker(drain=True)

    full = client.get(f"/api/voice/events?session_id={session_id}").get_data(as_text=True)
    assert "id: 1\n" in full
    resumed = client.get(
        f"/api/voice/events?session_id={session_id}", headers={"Last-Event-ID": "2"}
    )
    body = resumed.get_data(as_text=True)
    assert "id: 1\n" not in body and "id: 2\n" not in body
    assert [event["type"] for event in _sse_events(resumed)] == [
        "response.output_text.done",
        "response.done",
    ]


def test_voice_closed_session_is_scoped_to_owner(client, monkeypatch):
    monkeypatch.setattr(voice_routes, "get_current_owner_id", lambda required=False: "alice")
    session_id = _closed_session(client)
    monkeypatch.setattr(voice_routes, "get_current_owner_id", lambda required=False: "mallory")
    assert client.get(f"/api/voice/output?session_id={session_id}").status_code == 403
    assert client.post("/api/voice/close", json={"session_id": session_id}).status_code == 403
