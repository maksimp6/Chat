from datetime import datetime, timedelta, timezone

import base64

import pytest

from key_manager import (
    KeyExpiredError,
    KeyManagerError,
    KeyRevokedError,
    get_metadata,
    list_keys,
    read_secret,
    revoke_key,
    rotate_key,
    delete_key,
    store_secret,
)


def setup_env(monkeypatch):
    monkeypatch.setenv(
        "ALICE_KEY_MANAGER_KEY",
        base64.urlsafe_b64encode(b"0" * 32).decode("ascii"),
    )


@pytest.fixture()
def isolated_db(monkeypatch, tmp_path):
    setup_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    import db

    db.DB_PATH = str(tmp_path / "keys.db")
    db.init_db()
    return db




def test_list_keys_returns_metadata_only(isolated_db):
    store_secret(
        secret="secret-1",
        name="One",
        purpose="signing",
        provider="android",
    )
    metas = list_keys(provider="android")
    assert len(metas) == 1
    assert not hasattr(metas[0], "secret")
    assert metas[0].status == "active"






def test_expired_key_is_not_readable(isolated_db):
    meta = store_secret(
        secret="expired",
        name="Expired",
        purpose="api",
        provider="test",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    with pytest.raises(KeyExpiredError):
        read_secret(meta.key_ref)


def test_invalid_encryption_key_fails_closed(isolated_db, monkeypatch):
    monkeypatch.setenv("ALICE_KEY_MANAGER_KEY", "not-a-fernet-key")
    with pytest.raises(KeyManagerError):
        store_secret(
            secret="secret",
            name="Broken",
            purpose="api",
            provider="test",
        )


def test_delete_requires_revocation_and_removes_key(isolated_db):
    meta = store_secret(
        secret="delete-me",
        name="One",
        purpose="api",
        provider="test",
    )
    with pytest.raises(KeyManagerError):
        delete_key(meta.key_ref)
    revoke_key(meta.key_ref)
    delete_key(meta.key_ref)
    with pytest.raises(Exception):
        get_metadata(meta.key_ref)
