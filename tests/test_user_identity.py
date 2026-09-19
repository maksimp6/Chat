import os
import tempfile
import db
from user_identity import get_anonymous_user, init_user_identity_table, register_anonymous_user

def test_anonymous_registration_is_idempotent_and_sanitizes_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        old = db.DB_PATH
        db.DB_PATH = os.path.join(tmp, "users.db")
        try:
            init_user_identity_table()
            first = register_anonymous_user("android-installation-123456", {"platform": "android", "token": "must-not-persist"})
            second = register_anonymous_user("android-installation-123456", {"platform": "android-16"})
            assert first["new_user"] is True
            assert second["new_user"] is False
            assert first["user_id"] == second["user_id"]
            assert get_anonymous_user(first["user_id"])["metadata"] == {"platform": "android-16"}
        finally:
            db.DB_PATH = old

def test_invalid_installation_id_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        old = db.DB_PATH
        db.DB_PATH = os.path.join(tmp, "users.db")
        try:
            init_user_identity_table()
            try:
                register_anonymous_user("short", {})
            except ValueError as exc:
                assert "installation_id" in str(exc)
            else:
                raise AssertionError("invalid installation_id accepted")
        finally:
            db.DB_PATH = old
