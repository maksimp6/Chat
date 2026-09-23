import db
def test_shared_sqlite_database_supports_core_conversation_flow(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("ALICE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "alice_pro.db"))

    db.init_db()
    db.create_conversation("c1", "Test", "model")
    db.add_message("c1", "user", "hello", trace={"trace_id": "t1"})
    db.save_conv_settings("c1", {"temperature": 0.2})

    conversations = db.get_conversations()
    messages = db.get_messages("c1")
    settings = db.get_conv_settings("c1")

    assert conversations[0]["id"] == "c1"
    assert messages[0]["text"] == "hello"
    assert messages[0]["trace"]["trace_id"] == "t1"
    assert settings == {"temperature": 0.2}
