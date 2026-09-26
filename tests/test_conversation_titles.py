from pathlib import Path


def test_legacy_default_conversation_title_is_migrated(tmp_path, monkeypatch):
    import db

    db_path = tmp_path / "conversations.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))

    db.init_db()
    db.create_conversation("conv-old-title", "Новый диалог", "aliceai-llm")
    db.init_db()

    conversations = db.get_conversations()
    assert conversations[0]["title"] == "Новый чат"


def test_conversation_title_update_accepts_owner_and_normalizes(tmp_path, monkeypatch):
    import db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "titles.db"))
    db.init_db()
    db.create_conversation("conv-title", "Новый чат", "aliceai-llm")

    db.update_conversation_title("conv-title", "  Очень   хороший   заголовок  ", "owner-1")

    assert db.get_conversation_title("conv-title") == "Очень хороший заголовок"


def test_default_title_is_replaced_by_first_non_empty_message_and_not_overwritten(
    tmp_path, monkeypatch
):
    import db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "titles.db"))
    db.init_db()
    db.create_conversation("conv-auto", "Новый чат", "aliceai-llm")

    first = db.maybe_update_conversation_title("conv-auto", "Первая тема диалога\nи детали")
    second = db.maybe_update_conversation_title(
        "conv-auto", "Поздняя тема, которая не должна затереть первую"
    )

    assert first == "Первая тема диалога"
    assert second == "Первая тема диалога"
    assert db.get_conversation_title("conv-auto") == "Первая тема диалога"


def test_chat_client_syncs_server_title_to_sidebar():
    chat = (Path(__file__).resolve().parents[1] / "static" / "chat.js").read_text(encoding="utf-8")
    assert "conversation.title = data.title" in chat
    assert "renderSidebar()" in chat
