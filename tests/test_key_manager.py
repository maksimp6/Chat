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


def test_store_and_read_secret_without_exposing_plaintext_in_metadata(isolated_db):
    meta = store_secret(
        secret="very-secret-value",
        name="Cloud.ru API key",
        purpose="provider-api",
        provider="cloudru",
    )

    assert meta.key_ref
    assert meta.fingerprint != "very-secret-value"
    assert meta.fingerprint
    assert "very-secret-value" not in str(meta)
    assert read_secret(meta.key_ref) == "very-secret-value"


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


def test_revoke_prevents_read(isolated_db):
    meta = store_secret(
        secret="secret-1",
        name="One",
        purpose="api",
        provider="cloudru",
    )
    revoke_key(meta.key_ref)
    assert get_metadata(meta.key_ref).status == "revoked"
    with pytest.raises(KeyRevokedError):
        read_secret(meta.key_ref)


def test_rotate_creates_new_reference_and_revokes_old(isolated_db):
    old = store_secret(
        secret="old",
        name="One",
        purpose="api",
        provider="cloudru",
    )
    new = rotate_key(old.key_ref, secret="new")
    assert new.key_ref != old.key_ref
    assert new.rotated_from == old.key_ref
    with pytest.raises(KeyRevokedError):
        read_secret(old.key_ref)
    assert read_secret(new.key_ref) == "new"


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
