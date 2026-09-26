import db


def setup_function():
    db.reset_memory_db()


def test_memory_backend_core_persistence(monkeypatch):
    monkeypatch.setenv("ALICE_DB_BACKEND", "memory")
    db.init_db()

    db.create_conversation("c1", "Новый чат", "alice-lite")
    db.add_message("c1", "user", "Привет 👋", timings=[{"name": "db"}], trace={"trace_id": "t1"})
    db.save_conv_settings("c1", {"temperature": 0.2})
    db.set_config("feature", {"enabled": True})

    assert db.get_conversation_title("c1") == "Новый чат"
    assert db.get_messages("c1")[0]["text"] == "Привет 👋"
    assert db.get_messages("c1")[0]["trace"]["trace_id"] == "t1"
    assert db.get_conv_settings("c1") == {"temperature": 0.2}
    assert db.get_config("feature") == {"enabled": True}

    db.update_conversation_title("c1", "  Новое   имя  ")
    db.update_conversation_model("c1", "alice-pro")
    assert db.get_conversations()[0]["title"] == "Новое имя"
    assert db.get_conversations()[0]["model"] == "alice-pro"


def test_memory_backend_delete_conversation(monkeypatch):
    monkeypatch.setenv("ALICE_DB_BACKEND", "memory")
    db.init_db()
    db.create_conversation("c1", "Chat", "model")
    db.add_message("c1", "user", "text")
    db.save_conv_settings("c1", {"x": 1})

    db.delete_conversation("c1")

    assert db.get_conversations() == []
    assert db.get_messages("c1") == []
    assert db.get_conv_settings("c1") is None


def test_memory_backend_does_not_expose_sql_connection(monkeypatch):
    monkeypatch.setenv("ALICE_DB_BACKEND", "memory")
    db.init_db()

    try:
        db.get_conn()
    except RuntimeError as exc:
        assert "does not expose a SQL connection" in str(exc)
    else:
        raise AssertionError("memory backend leaked a SQL connection")


def test_memory_backend_title_logic_and_config_string(monkeypatch):
    monkeypatch.setenv("ALICE_DB_BACKEND", "memory")
    db.init_db()
    db.create_conversation("c1", "Новый чат", "model")

    assert (
        db.maybe_update_conversation_title("c1", "\n  Первая строка  \nвторая") == "Первая строка"
    )
    assert db.get_conversation_title("c1") == "Первая строка"
    assert db.maybe_update_conversation_title("c1", "другая") == "Первая строка"
    assert db.maybe_update_conversation_title("c1", "   ") == "Первая строка"
    assert db.maybe_update_conversation_title("missing", "текст") == "текст"

    db.set_config("plain", "hello")
    assert db.get_config("plain") == "hello"
    assert db.get_config("missing", "fallback") == "fallback"


def test_memory_backend_non_string_message_and_invalid_json(monkeypatch):
    monkeypatch.setenv("ALICE_DB_BACKEND", "memory")
    db.init_db()
    db.create_conversation("c1", "Chat", "model")
    db.add_message("c1", "assistant", {"answer": "ok"})
    assert db.get_messages("c1")[0]["text"] == '{"answer": "ok"}'

    db._MEMORY_DB.update("messages", lambda r: True, timings_json="{bad", trace_json="{bad")
    message = db.get_messages("c1")[0]
    assert message["timings"] == []
    assert message["trace"] == {}
