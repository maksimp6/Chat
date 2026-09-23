import pytest

import db


def test_conversation_ownership_is_scoped_and_cannot_be_overwritten(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "alice.db"))
    db.init_db()

    db.create_conversation("c-a", "A", "model", user_id="user-a")
    db.create_conversation("c-b", "B", "model", user_id="user-b")

    assert [c["id"] for c in db.get_conversations("user-a")] == ["c-a"]
    assert [c["id"] for c in db.get_conversations("user-b")] == ["c-b"]
    assert db.get_conversation("c-b", "user-a") is None

    with pytest.raises(PermissionError, match="conversation_not_owned"):
        db.create_conversation("c-a", "Hijack", "model", user_id="user-b")


def test_legacy_unowned_conversation_can_be_claimed_without_cross_user_reassignment(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "alice.db"))
    db.init_db()

    db.create_conversation("legacy", "Legacy", "model")
    db.create_conversation("legacy", "Legacy", "model", user_id="user-a")

    assert db.get_conversation("legacy", "user-a")["id"] == "legacy"
    with pytest.raises(PermissionError, match="conversation_not_owned"):
        db.create_conversation("legacy", "Stolen", "model", user_id="user-b")
