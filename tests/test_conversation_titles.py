def test_legacy_default_conversation_title_is_migrated(tmp_path, monkeypatch):
    import db

    db_path = tmp_path / "conversations.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))

    db.init_db()
    db.create_conversation("conv-old-title", "Новый диалог", "aliceai-llm")

    conversations = db.get_conversations()
    assert conversations[0]["title"] == "Новый чат"
