"""Logos's single native storage boundary, including fresh-process recovery."""

import json
import os
from pathlib import Path
import subprocess
import sys
import storage.db as db
from agent_memory.client import close_memory_clients


def test_no_sql_connection_api():
    assert not hasattr(db, "get_conn")
    assert not (Path(db.__file__).parent / "db_backend.py").exists()


def test_native_chat_and_settings_survive_fresh_process(tmp_path, monkeypatch):
    path = tmp_path / "alice.memory"
    monkeypatch.setenv("ALICE_MEMORY_PATH", str(path))
    db.create_conversation("chat", "Новый чат", "alice")
    db.add_message("chat", "user", "Привет", trace={"trace_id": "trace-1"})
    db.save_conv_settings("chat", {"temperature": 0.2})
    db.set_config("feature", {"enabled": True})
    db.set_yandex_conversation("chat", "provider-chat")
    close_memory_clients()
    code = "import json,storage.db as db; print(json.dumps([db.get_conversations(),db.get_messages('chat'),db.get_conv_settings('chat'),db.get_config('feature'),db.get_yandex_conversation('chat')]))"
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    recovered = json.loads(result.stdout)
    assert recovered[0][0]["id"] == "chat"
    assert recovered[1][0]["text"] == "Привет"
    assert recovered[1][0]["trace"] == {"trace_id": "trace-1"}
    assert recovered[2:] == [{"temperature": 0.2}, {"enabled": True}, "provider-chat"]


def test_delete_is_one_durable_cascade():
    db.create_conversation("chat", "Chat", "alice")
    db.add_message("chat", "user", "message")
    db.save_conv_settings("chat", {"value": 1})
    db.set_yandex_conversation("chat", "provider")
    before = db.get_store().last_commit.sequence
    db.delete_conversation("chat")
    assert db.get_store().last_commit.sequence == before + 1
    close_memory_clients()
    assert db.get_conversations() == []
    assert db.get_messages("chat") == []
    assert db.get_conv_settings("chat") is None
    assert db.get_yandex_conversation("chat") is None
