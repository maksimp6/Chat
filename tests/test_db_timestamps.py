"""Chat Unix timestamps must describe the same instant in every local timezone."""

import time

import pytest

import db
from memory_db import MemoryDatabase


@pytest.fixture(params=["UTC0", "MSK-3", "EST5"])
def local_timezone(request, monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("the platform cannot change its process timezone")
    try:
        with monkeypatch.context() as timezone_env:
            timezone_env.setenv("TZ", request.param)
            time.tzset()
            yield
    finally:
        time.tzset()


@pytest.fixture(params=["sqlite", "memory"])
def database(request, monkeypatch, tmp_path):
    monkeypatch.delenv("ALICE_DATABASE_URL", raising=False)
    monkeypatch.setenv("ALICE_DB_BACKEND", request.param)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "timestamps.db"))
    monkeypatch.setattr(db, "_MEMORY_DB", MemoryDatabase())
    monkeypatch.setattr(db, "_MEMORY_INITIALIZED", False)
    db.init_db()
    return request.param


@pytest.mark.parametrize(
    "operation", ["create", "title", "auto_title", "model", "message", "settings"]
)
def test_chat_writes_store_current_unix_epoch(database, local_timezone, operation):
    conversation_id = "timezone-test"
    if operation != "create":
        db.create_conversation(conversation_id, "Новый чат", "test-model")

    before = int(time.time())
    if operation == "create":
        db.create_conversation(conversation_id, "Новый чат", "test-model")
    elif operation == "title":
        db.update_conversation_title(conversation_id, "Updated title")
    elif operation == "auto_title":
        db.maybe_update_conversation_title(conversation_id, "First message title")
    elif operation == "model":
        db.update_conversation_model(conversation_id, "updated-model")
    elif operation == "message":
        db.add_message(conversation_id, "user", "Timezone regression")
    else:
        db.save_conv_settings(conversation_id, {"temperature": 0.2})
    after = int(time.time())

    if operation == "settings":
        if database == "memory":
            settings = db._MEMORY_DB.select("conv_settings")[0]
        else:
            connection = db.get_conn()
            try:
                settings = connection.execute(
                    "SELECT CAST(updated_at AS INTEGER) AS updated_at "
                    "FROM conv_settings WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()
            finally:
                connection.close()
        timestamps = [settings["updated_at"]]
    else:
        conversation = db.get_conversations()[0]
        timestamps = [conversation["updated_at"]]
        if operation == "create":
            timestamps.append(conversation["created_at"])
        elif operation == "message":
            timestamps.append(db.get_messages(conversation_id)[0]["created_at"])

    for timestamp in timestamps:
        assert isinstance(timestamp, int)
        assert before <= timestamp <= after
