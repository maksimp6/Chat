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
