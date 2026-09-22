from datetime import datetime, timedelta, timezone

import pytest

from provider_credentials import (
    ExpiredCredentialError,
    NoActiveCredentialError,
    get_active_key,
    issue_window,
    rotation_needed,
    should_revoke,
)


class FakeDb:
    def __init__(self, row):
        self.row = row

    def fetch_one(self, _query):
        return self.row


def test_get_active_key_decrypts_active_unexpired_key():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = FakeDb({
        "api_key_encrypted": "ciphertext",
        "id": 7,
        "yandex_key_id": "aje-key-7",
        "project_id": "project-1",
        "issued_at": now - timedelta(hours=11),
        "expires_at": now + timedelta(hours=1),
    })
    assert get_active_key(db, lambda value: f"secret:{value}", now) == "secret:ciphertext"


def test_missing_active_key_fails():
    with pytest.raises(NoActiveCredentialError):
        get_active_key(FakeDb(None), lambda value: value)


def test_expired_active_key_fails():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = FakeDb({
        "api_key_encrypted": "ciphertext",
        "id": 7,
        "yandex_key_id": "aje-key-7",
        "project_id": "project-1",
        "issued_at": now - timedelta(hours=13),
        "expires_at": now - timedelta(seconds=1),
    })
    with pytest.raises(ExpiredCredentialError):
        get_active_key(db, lambda value: value, now)


def test_rotation_boundary_is_inclusive():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert not rotation_needed(now + timedelta(hours=1, seconds=1), now)
    assert rotation_needed(now + timedelta(hours=1), now)
    assert should_revoke(now, now)


def test_issue_window_is_12_hours():
    issued, expires = issue_window(datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert expires - issued == timedelta(hours=12)


def test_fingerprint_is_stable_and_non_secret():
    from provider_credentials import fingerprint_key
    secret = "sk-provider-secret"
    fingerprint = fingerprint_key(secret)
    assert len(fingerprint) == 64
    assert fingerprint != secret


def test_provider_credentials_are_isolated():
    import sqlite3

    from provider_credentials import create_schema, get_active_credential

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    conn.execute(
        """INSERT INTO provider_credentials
           (api_key_encrypted, provider_key_id, provider, project_id,
            issued_at, expires_at, status)
           VALUES (?, ?, ?, ?, ?, ?, 'active')""",
        (
            "cipher-yandex",
            "yandex-key",
            "yandex",
            "project-yandex",
            "2026-01-01T00:00:00+00:00",
            "2026-01-02T00:00:00+00:00",
        ),
    )
    conn.execute(
        """INSERT INTO provider_credentials
           (api_key_encrypted, provider_key_id, provider, project_id,
            issued_at, expires_at, status)
           VALUES (?, ?, ?, ?, ?, ?, 'active')""",
        (
            "cipher-cloudru",
            "cloudru-key",
            "cloudru",
            "",
            "2026-01-01T00:00:00+00:00",
            "2026-01-02T00:00:00+00:00",
        ),
    )
    conn.commit()

    yandex = get_active_credential(
        conn,
        lambda value: "yandex-secret" if value == "cipher-yandex" else "wrong",
        provider="yandex",
        now=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
    )
    cloudru = get_active_credential(
        conn,
        lambda value: "cloudru-secret" if value == "cipher-cloudru" else "wrong",
        provider="cloudru",
        now=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
    )

    assert yandex.api_key == "yandex-secret"
    assert yandex.provider == "yandex"
    assert yandex.provider_key_id == "yandex-key"
    assert cloudru.api_key == "cloudru-secret"
    assert cloudru.provider == "cloudru"
    assert cloudru.provider_key_id == "cloudru-key"


def test_create_schema_migrates_legacy_global_index():
    import sqlite3

    from provider_credentials import create_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE provider_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key_encrypted TEXT NOT NULL,
            yandex_key_id TEXT,
            project_id TEXT NOT NULL,
            issued_at TIMESTAMP NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        "CREATE UNIQUE INDEX one_active_key ON provider_credentials(status) WHERE status = 'active'"
    )
    conn.execute(
        """INSERT INTO provider_credentials
           (api_key_encrypted, yandex_key_id, project_id, issued_at, expires_at, status)
           VALUES ('cipher', 'old-yandex-id', 'project-1',
                   '2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00', 'active')"""
    )
    create_schema(conn)
    indexes = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    row = conn.execute(
        "SELECT provider, provider_key_id FROM provider_credentials WHERE id=1"
    ).fetchone()

    assert "one_active_key" not in indexes
    assert "one_active_key_per_provider" in indexes
    assert row["provider"] == "yandex"
    assert row["provider_key_id"] == "old-yandex-id"


def test_provider_health_metadata_has_no_secret():
    import sqlite3

    from cryptography.fernet import Fernet
    from provider_credentials import create_schema, record_health_check, replace_active_credential

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    fernet = Fernet(Fernet.generate_key())

    meta = replace_active_credential(
        conn,
        "secret-health",
        "project-1",
        lambda value: fernet.encrypt(value.encode("utf-8")).decode("utf-8"),
        "yandex",
        provider_key_id="yandex-health",
    )
    record_health_check(conn, "yandex", status="connected")

    row = conn.execute(
        """SELECT fingerprint, last_check_status, last_check_error
           FROM provider_credentials WHERE id = ?""",
        (meta.id,),
    ).fetchone()
    conn.close()

    assert row["fingerprint"]
    assert row["last_check_status"] == "connected"
    assert row["last_check_error"] is None
    assert "secret-health" not in str(dict(row))
